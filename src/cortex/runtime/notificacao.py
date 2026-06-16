"""Notificação da Learning Queue pelo canal (Fase 7c) — o Cortex AVISA.

Uma proposta que entra na fila não pode esperar o gestor adivinhar. Quando um
turno cria uma proposta PENDENTE, o servidor avisa o gestor PELO CANAL dele,
com o id e o como-decidir. O aviso sai por canal INTERNO (o gestor é mapeado) e
é mensagem do SISTEMA — não entra no pipeline de turno, então nunca vira dado
externo nem candidato de aprendizado (anti-loop por construção).

`canal_saida` é duck-typed (tem `.enviar`) de propósito: a notificação é
conceito de runtime e não importa a camada de servidor — evita inversão de
dependência (server depende de runtime, não o contrário).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cortex.memory.models import Procedencia

if TYPE_CHECKING:
    from cortex.memory.learning import Proposal

logger = logging.getLogger("cortex.runtime")


class NotificadorFila:
    """Avisa o gestor sobre propostas novas, pelo canal de saída dele."""

    def __init__(self, canal_saida, gestor_canal_id: str | None) -> None:
        self._canal = canal_saida
        self._gestor_id = gestor_canal_id

    def notificar_nova_proposta(self, proposta: Proposal) -> None:
        if self._gestor_id is None:
            logger.warning(
                "proposta #%s entrou na fila, mas o gestor não está mapeado no "
                "canais.yaml — não há para quem notificar",
                proposta.id,
            )
            return
        try:
            self._canal.enviar(self._gestor_id, self._mensagem(proposta))
        except Exception as exc:  # noqa: BLE001 — entrega é best-effort, nunca propaga
            logger.error(
                "falha ao notificar o gestor sobre a proposta #%s: %s", proposta.id, exc
            )

    @staticmethod
    def _mensagem(p: Proposal) -> str:
        externa = p.source.procedencia is Procedencia.EXTERNA
        atencao = "\n⚠ fonte externa não autenticada" if externa else ""
        vigente = p.current_value if p.current_value is not None else "(assunto novo)"
        return (
            f"🔔 Nova proposta na fila (#{p.id}, {p.kind.value}).\n"
            f"{p.key}: {vigente} → {p.proposed_value}{atencao}\n"
            f"Para decidir, responda aqui: aprovar {p.id} porque ...  |  "
            f"rejeitar {p.id} porque ..."
        )
