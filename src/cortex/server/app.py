"""App FastAPI do Cortex (Fase 7a) — POST /v1/mensagens + GET /v1/saude.

Quem chama é um BRIDGE confiável (n8n etc.), autenticado pelo token de
TRANSPORTE (X-Cortex-Token). A identidade do REMETENTE é outra coisa: vem do
canal (canais.yaml), nunca do texto. Turnos são serializados por um lock global
do deploy — engine/GraphitiStore não são thread-safe; um turno por vez (dívida
consciente: worker/fila quando houver volume; aqui latência honesta > corrupção).
"""

import logging
import secrets
import threading
from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from cortex.identity.models import Persona
from cortex.runtime import AgentLoop, LoopLimiteExcedidoError
from cortex.server.identidade import ChaveCanal, resolver_identidade
from cortex.server.sessoes import GerenciadorSessoes

logger = logging.getLogger("cortex.server")


class MensagemEntrada(BaseModel):
    """Mensagem entregue pelo bridge — `canal` é livre ('whatsapp', 'teste')."""

    canal: str
    canal_id: str
    texto: str


class MensagemResposta(BaseModel):
    resposta: str
    sessao: str


def criar_app(
    *,
    persona: Persona,
    loop: AgentLoop,
    mapa_identidades: dict[ChaveCanal, str],
    token: str | None,
    ttl_minutos: int = 30,
    agora: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Monta o app FastAPI sobre um runtime já montado (testável com StubProvider)."""
    app = FastAPI(title=f"Cortex — {persona.soul.nome}", version="7a")
    gerenciador = GerenciadorSessoes(persona, ttl_minutos, agora=agora)
    lock = threading.Lock()
    contadores = {"turnos": 0}

    def _conferir_token(recebido: str | None) -> None:
        # O token autentica o TRANSPORTE. Sem token configurado, o gateway é
        # fechado (não atende) — não se serve um deploy sem token de bridge.
        if token is None or recebido is None or not secrets.compare_digest(recebido, token):
            raise HTTPException(status_code=401, detail="token de bridge ausente ou inválido")

    @app.post("/v1/mensagens", response_model=MensagemResposta)
    def receber_mensagem(
        msg: MensagemEntrada, x_cortex_token: str | None = Header(default=None)
    ) -> MensagemResposta:
        _conferir_token(x_cortex_token)
        identidade = resolver_identidade(msg.canal, msg.canal_id, mapa_identidades, persona)
        with lock:  # um turno por vez (engine não é thread-safe)
            sessao = gerenciador.obter(msg.canal, msg.canal_id, identidade)
            try:
                resposta = loop.executar_turno(sessao, msg.texto)
            except LoopLimiteExcedidoError as exc:
                raise HTTPException(status_code=503, detail=f"turno abortado: {exc}") from exc
            contadores["turnos"] += 1
        logger.info(
            "turno canal=%s id=%s identidade=%s procedencia=%s resp_chars=%d",
            msg.canal,
            msg.canal_id,
            identidade.nome,
            identidade.procedencia.value,
            len(resposta),
        )
        return MensagemResposta(resposta=resposta, sessao=f"{msg.canal}:{msg.canal_id}")

    @app.get("/v1/saude")
    def saude() -> dict:
        return {
            "status": "ok",
            "persona": persona.soul.nome,
            "papel": persona.soul.papel,
            "sessoes_ativas": gerenciador.ativas,
            "turnos_atendidos": contadores["turnos"],
        }

    return app
