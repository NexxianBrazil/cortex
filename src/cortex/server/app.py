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
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from cortex.identity.models import Persona
from cortex.runtime import AgentLoop, LoopLimiteExcedidoError
from cortex.runtime.identidade import Identidade, identidade_interna
from cortex.runtime.messages import Role
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
    config=None,
    kb=None,
    audit=None,
    painel_operador: str | None = None,
    lock=None,
    deploy_dir=None,
) -> FastAPI:
    """Monta o app FastAPI sobre um runtime já montado (testável com StubProvider).

    `engine`/`notificador`/`canal_saida` são opcionais: sem eles o app funciona
    como na 7a (sem notificação nem webhook útil) — é o que os testes da 7a usam.
    O painel (7d) só é montado com `config` habilitado + senha + engine/kb/operador.
    """
    app = FastAPI(title=f"Cortex — {persona.soul.nome}", version="8")
    gerenciador = GerenciadorSessoes(persona, ttl_minutos, agora=agora)
    app.state.gerenciador_sessoes = gerenciador   # inspeção (testes/diagnóstico)
    # Lock GLOBAL do deploy: serializa turnos E o job de reflexão (Fase 8). Pode
    # ser injetado por `cortex servir` para compartilhar com o Scheduler.
    lock = lock or threading.Lock()
    contadores: dict = {"turnos": 0, "ultimo_turno": None}

    # Heartbeat (Fase 8): saúde LOCAL exposta em /v1/saude, lendo contadores vivos.
    from cortex.ops import Heartbeat

    db_path = config.kuzu_db_path if config is not None and config.store == "graphiti" else None
    heartbeat = Heartbeat(
        db_path=db_path,
        sessoes_ativas=lambda: gerenciador.ativas,
        pendentes=lambda: len(engine.pending_approvals) if engine is not None else 0,
        ultimo_turno=lambda: contadores["ultimo_turno"],
    )

    def _conferir_token(recebido: str | None) -> None:
        # Autentica o TRANSPORTE (o bridge/Evolution), não o remetente. Sem token
        # configurado, o gateway é fechado — não se serve sem token.
        if token is None or recebido is None or not secrets.compare_digest(recebido, token):
            raise HTTPException(status_code=401, detail="token de transporte ausente ou inválido")

    def _ids_pendentes() -> set[int]:
        return {p.id for p in engine.pending_approvals} if engine is not None else set()

    def processar_turno(
        canal: str, canal_id: str, texto: str, identidade: Identidade | None = None
    ) -> str:
        """Pipeline único dos endpoints: resolve identidade, roda o turno,
        e notifica o gestor sobre propostas NOVAS daquele turno (diff de pendentes).

        `identidade` pode vir PRONTA de um canal que já autenticou a pessoa —
        é o caso do chat do painel, onde a senha do operador já provou quem é.
        Nos bridges (WhatsApp/HTTP) ela segue sendo resolvida pelo mapa.
        """
        identidade = identidade or resolver_identidade(canal, canal_id, mapa_identidades, persona)
        with lock:  # um turno por vez (engine não é thread-safe)
            sessao = gerenciador.obter(canal, canal_id, identidade)
            pend_antes = _ids_pendentes()
            try:
                resposta = loop.executar_turno(sessao, texto)
            except LoopLimiteExcedidoError as exc:
                raise HTTPException(status_code=503, detail=f"turno abortado: {exc}") from exc
            contadores["turnos"] += 1
            contadores["ultimo_turno"] = datetime.now(UTC)
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
            "heartbeat": heartbeat.coletar(),
        }

    # Painel do operador (7d): mesma porta, auth separada. Fail-safe: sem senha
    # (ou sem engine/kb/operador), o painel NÃO sobe — a API de bridge segue.
    if config is not None and config.painel_habilitado:
        senha = config.painel_senha.get_secret_value() if config.painel_senha else None
        if senha and engine is not None and kb is not None and painel_operador:
            from cortex.server.painel import montar_painel

            # ---- chat do painel -------------------------------------------
            # Canal PRÓPRIO ("painel"), com identidade INTERNA já autenticada
            # pela senha — o operador existe no USER.md (validado no montar).
            # Reusa o MESMO pipeline dos bridges: lock, contadores, promoção,
            # governança e notificação de proposta nova saem de graça.
            CANAL_PAINEL = "painel"

            def _identidade_painel() -> Identidade:
                return identidade_interna(
                    persona, painel_operador, canal=CANAL_PAINEL, canal_id=painel_operador
                )

            def chat_enviar(texto: str) -> str:
                return processar_turno(
                    CANAL_PAINEL, painel_operador, texto, identidade=_identidade_painel()
                )

            def chat_historico() -> list[dict]:
                """Conversa da sessão viva (só usuário/persona; tool fica fora)."""
                sessao = gerenciador.espiar(CANAL_PAINEL, painel_operador)
                if sessao is None:
                    return []
                fala = []
                for m in sessao.historico:
                    if m.role is Role.USER:
                        fala.append({"quem": "voce", "texto": m.content})
                    elif m.role is Role.ASSISTANT and m.content and not m.tool_calls:
                        fala.append({"quem": "persona", "texto": m.content})
                return fala

            def chat_novo() -> None:
                gerenciador.descartar(CANAL_PAINEL, painel_operador)

            montar_painel(
                app,
                persona=persona,
                engine=engine,
                kb=kb,
                kb_path=config.kb_path,
                audit=audit,
                lock=lock,
                senha=senha,
                operador=painel_operador,
                sessao_horas=config.painel_sessao_horas,
                # cortex.toml do deploy: sem ele a troca de senha pelo painel
                # responde 409 explicando como trocar à mão (dev roda do CWD).
                toml_path=(Path(deploy_dir) / "cortex.toml") if deploy_dir else None,
                # Modo mestre (edição de formação): sem senha aqui, não existe.
                senha_mestre=(
                    config.painel_senha_mestre.get_secret_value()
                    if config.painel_senha_mestre
                    else None
                ),
                personas_dir=config.personas_dir,
                chat_enviar=chat_enviar,
                chat_historico=chat_historico,
                chat_novo=chat_novo,
            )
            logger.info("painel do operador em /painel (operador=%s)", painel_operador)
        else:
            logger.warning(
                "painel habilitado mas falta senha/engine/kb/operador — NÃO montado (fail-safe)"
            )

    return app
