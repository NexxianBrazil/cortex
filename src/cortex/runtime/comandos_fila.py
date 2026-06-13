"""Gerenciar a Learning Queue PELO CANAL — tool `gerenciar_fila` (Fase 7b).

O gestor não abre terminal: responde no WhatsApp. Mas a governança da 4b NÃO
muda — só quem é AUTORITATIVO decide, a autoridade segue o CANAL autenticado
(não o texto), a decisão exige razão e vira episódio com autor. A tool lê a
identidade do turno via ContextoTurno (turnos são serializados, então um campo
mutável é seguro); tentativa externa/não-autoritativa é recusada e auditada,
sem vazar o conteúdo das propostas.
"""

import logging
from dataclasses import dataclass

from cortex.memory.engine import (
    AutoridadeInsuficienteError,
    MemoryEngine,
    PropostaJaDecididaError,
)
from cortex.memory.models import Procedencia
from cortex.runtime.identidade import Identidade
from cortex.runtime.tools import ToolRegistry

logger = logging.getLogger("cortex.runtime")


@dataclass
class ContextoTurno:
    """Identidade do turno corrente, partilhada entre o loop e a tool de fila.

    O loop a atualiza no início de cada turno. Seguro por um único campo mutável
    porque os turnos são SERIALIZADOS (lock do servidor / chat single-thread).
    """

    identidade: Identidade | None = None


class GerenciarFilaTool:
    """Tool plugável: lista/aprova/rejeita propostas com a autoridade do CANAL."""

    def __init__(
        self,
        engine: MemoryEngine,
        contexto: ContextoTurno,
        *,
        dominio: str,
        audit=None,
    ) -> None:
        self._engine = engine
        self._contexto = contexto
        self._dominio = dominio
        self._audit = audit

    def __call__(
        self, acao: str, proposta_id: int | None = None, razao: str | None = None
    ) -> dict:
        ident = self._contexto.identidade
        if not self._autoritativo(ident):
            self._auditar_recusa(acao, ident)
            return {
                "permitido": False,
                "motivo": (
                    "apenas o gestor autenticado pode gerenciar a fila por este canal; "
                    "não posso listar nem decidir propostas para você"
                ),
            }

        acao_norm = (acao or "").strip().lower()
        if acao_norm in ("listar", "fila", "pendentes"):
            return self._listar()
        if acao_norm in ("aprovar", "rejeitar"):
            return self._decidir_ou_pedir_razao(acao_norm, proposta_id, razao, ident)
        return {"erro": f"ação desconhecida: {acao!r} (use listar, aprovar ou rejeitar)"}

    # ---- autoridade (segue o canal, não o texto) ------------------------- #

    def _autoritativo(self, ident: Identidade | None) -> bool:
        return (
            ident is not None
            and not ident.externa
            and self._engine.authority_map.is_authoritative(ident.nome, self._dominio)
        )

    # ---- ações ----------------------------------------------------------- #

    def _listar(self) -> dict:
        pendentes = self._engine.pending_approvals
        return {
            "pendentes": [self._resumo(p) for p in pendentes],
            "total": len(pendentes),
        }

    def _resumo(self, p) -> dict:
        externa = p.source.procedencia is Procedencia.EXTERNA
        return {
            "id": p.id,
            "tipo": p.kind.value,
            "chave": p.key,
            "proposto": p.proposed_value,
            "vigente": p.current_value,
            "fonte": p.source.name,
            "porque_escalou": p.reason,
            "atencao": "⚠ fonte externa não autenticada" if externa else None,
        }

    def _decidir_ou_pedir_razao(
        self, acao: str, proposta_id: int | None, razao: str | None, ident: Identidade
    ) -> dict:
        if proposta_id is None:
            return {"erro": f"informe o número da proposta para {acao}"}
        if not razao or not razao.strip():
            # Decisão precisa de autor E porquê: pergunta a razão, NÃO decide.
            return {
                "precisa_razao": True,
                "mensagem": (
                    f"para {acao} a proposta {proposta_id}, qual a razão? "
                    "(a decisão fica registrada com o seu nome e o motivo)"
                ),
            }
        try:
            ep = (
                self._engine.aprovar(int(proposta_id), ident.nome, razao.strip())
                if acao == "aprovar"
                else self._engine.rejeitar(int(proposta_id), ident.nome, razao.strip())
            )
        except (AutoridadeInsuficienteError, PropostaJaDecididaError, ValueError) as exc:
            return {"ok": False, "erro": str(exc)}
        return {
            "ok": True,
            "acao": acao,
            "proposta": int(proposta_id),
            "por": ident.nome,
            "resultado": ep.action,
        }

    # ---- audit da tentativa recusada ------------------------------------- #

    def _auditar_recusa(self, acao: str, ident: Identidade | None) -> None:
        if self._audit is not None:
            self._audit.registrar(
                "comando_fila_recusado",
                acao=acao,
                identidade=ident.nome if ident is not None else None,
                procedencia=ident.procedencia.value if ident is not None else None,
            )
        logger.warning(
            "gerenciar_fila recusado para identidade=%s (não autoritativa/externa)",
            ident.nome if ident is not None else None,
        )


def registrar_gerenciar_fila(
    registry: ToolRegistry,
    engine: MemoryEngine,
    contexto: ContextoTurno,
    *,
    dominio: str,
    audit=None,
) -> None:
    """Registra a tool `gerenciar_fila` se a persona a declarar no catálogo."""
    if registry.declarada("gerenciar_fila"):
        registry.registrar(
            "gerenciar_fila",
            GerenciarFilaTool(engine, contexto, dominio=dominio, audit=audit),
        )
