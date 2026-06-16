"""App FastAPI do Cortex (Fases 7a + 7c).

Dois caminhos de ENTRADA, um pipeline:
- POST /v1/mensagens (7a): bridge genérico, resposta SÍNCRONA no corpo.
- POST /v1/webhook/evolution (7c): webhook do WhatsApp (Evolution API); resposta
  ASSÍNCRONA pelo canal de SAÍDA. Resolve a assimetria síncrono/assíncrono.

Ambos passam pelo MESMO `processar_turno` — a identidade vem do canal
(canais.yaml), nunca do texto; o apikey/token autentica o TRANSPORTE. Turnos
serializados por lock global (engine não é thread-safe; worker = dívida).
"""

import logging
import re
import secrets
import threading
from collections.abc import Callable
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from cortex.identity.models import Persona
from cortex.runtime import AgentLoop, LoopLimiteExcedidoError
from cortex.server.canal_saida import CanalSaida, CanalSaidaError
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


def _normalizar_telefone(jid: str) -> str:
    """remoteJid da Evolution ('5511999990000@s.whatsapp.net') → só dígitos com DDI."""
    return re.sub(r"\D", "", jid.split("@", 1)[0])


def _extrair_mensagem_evolution(payload: dict) -> tuple[str, str] | None:
    """Extrai (telefone, texto) de um messages.upsert; None se deve ser ignorado.

    Ignora eco das próprias respostas (fromMe) e tipos não-texto (áudio/imagem
    são fase futura) — defensivo contra payload incompleto.
    """
    data = payload.get("data") or {}
    key = data.get("key") or {}
    if key.get("fromMe"):
        return None
    telefone = _normalizar_telefone(str(key.get("remoteJid") or ""))
    if not telefone:
        return None
    msg = data.get("message") or {}
    texto = msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text")
    if not texto or not str(texto).strip():
        return None
    return telefone, str(texto)


def criar_app(
    *,
    persona: Persona,
    loop: AgentLoop,
    mapa_identidades: dict[ChaveCanal, str],
    token: str | None,
    ttl_minutos: int = 30,
    agora: Callable[[], datetime] | None = None,
    engine=None,
    canal_saida: CanalSaida | None = None,
    notificador=None,
) -> FastAPI:
    """Monta o app FastAPI sobre um runtime já montado (testável com StubProvider).

    `engine`/`notificador`/`canal_saida` são opcionais: sem eles o app funciona
    como na 7a (sem notificação nem webhook útil) — é o que os testes da 7a usam.
    """
    app = FastAPI(title=f"Cortex — {persona.soul.nome}", version="7c")
    gerenciador = GerenciadorSessoes(persona, ttl_minutos, agora=agora)
    lock = threading.Lock()
    contadores = {"turnos": 0}

    def _conferir_token(recebido: str | None) -> None:
        # Autentica o TRANSPORTE (o bridge/Evolution), não o remetente. Sem token
        # configurado, o gateway é fechado — não se serve sem token.
        if token is None or recebido is None or not secrets.compare_digest(recebido, token):
            raise HTTPException(status_code=401, detail="token de transporte ausente ou inválido")

    def _ids_pendentes() -> set[int]:
        return {p.id for p in engine.pending_approvals} if engine is not None else set()

    def processar_turno(canal: str, canal_id: str, texto: str) -> str:
        """Pipeline único dos dois endpoints: resolve identidade, roda o turno,
        e notifica o gestor sobre propostas NOVAS daquele turno (diff de pendentes).
        """
        identidade = resolver_identidade(canal, canal_id, mapa_identidades, persona)
        with lock:  # um turno por vez (engine não é thread-safe)
            sessao = gerenciador.obter(canal, canal_id, identidade)
            pend_antes = _ids_pendentes()
            try:
                resposta = loop.executar_turno(sessao, texto)
            except LoopLimiteExcedidoError as exc:
                raise HTTPException(status_code=503, detail=f"turno abortado: {exc}") from exc
            contadores["turnos"] += 1
            novas = (
                [p for p in engine.pending_approvals if p.id not in pend_antes]
                if engine is not None
                else []
            )
        # Notificação FORA do lock: é I/O de rede best-effort (não segura o turno).
        if notificador is not None:
            for proposta in novas:
                notificador.notificar_nova_proposta(proposta)
        logger.info(
            "turno canal=%s id=%s identidade=%s procedencia=%s resp_chars=%d novas_propostas=%d",
            canal,
            canal_id,
            identidade.nome,
            identidade.procedencia.value,
            len(resposta),
            len(novas),
        )
        return resposta

    @app.post("/v1/mensagens", response_model=MensagemResposta)
    def receber_mensagem(
        msg: MensagemEntrada, x_cortex_token: str | None = Header(default=None)
    ) -> MensagemResposta:
        _conferir_token(x_cortex_token)
        resposta = processar_turno(msg.canal, msg.canal_id, msg.texto)
        return MensagemResposta(resposta=resposta, sessao=f"{msg.canal}:{msg.canal_id}")

    @app.post("/v1/webhook/evolution")
    def webhook_evolution(payload: dict, apikey: str | None = Header(default=None)) -> dict:
        _conferir_token(apikey)
        extraido = _extrair_mensagem_evolution(payload)
        if extraido is None:
            return {"ignorado": True}  # fromMe / não-texto não quebram o webhook
        telefone, texto = extraido
        resposta = processar_turno("whatsapp", telefone, texto)
        # WhatsApp é assíncrono: a resposta volta pelo canal de SAÍDA, não no corpo.
        if canal_saida is not None:
            try:
                canal_saida.enviar(telefone, resposta)
            except CanalSaidaError as exc:
                logger.error("não consegui responder no WhatsApp a %s: %s", telefone, exc)
        else:
            logger.warning("webhook sem canal de saída — resposta de %s não enviada", telefone)
        return {"ok": True}

    @app.get("/v1/saude")
    def saude() -> dict:
        return {
            "status": "ok",
            "persona": persona.soul.nome,
            "papel": persona.soul.papel,
            "canal_saida": canal_saida.nome_canal if canal_saida is not None else None,
            "sessoes_ativas": gerenciador.ativas,
            "turnos_atendidos": contadores["turnos"],
        }

    return app
