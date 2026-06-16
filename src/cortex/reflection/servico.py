"""Camada de serviço da reflexão (Fase 8) — orquestra refletir + aplicar + avisar.

Separa a POLÍTICA (o que detectar — engine.py) da ORQUESTRAÇÃO (rodar, gravar,
notificar, pegar o lock). A reflexão não conhece o canal nem o lock; quem os
conhece é esta camada (mesmo padrão da 7c: a memória não conhece a rede).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from cortex.memory.learning import Proposal
from cortex.memory.store import MemoryStore
from cortex.reflection.engine import ReflectionEngine, RelatorioReflexao, aplicar_reflexao

logger = logging.getLogger("cortex.reflection")


def executar_reflexao(
    store: MemoryStore,
    janela_dias: int,
    *,
    agora: datetime | None = None,
    audit=None,
    notificador=None,
    dry_run: bool = False,
) -> tuple[RelatorioReflexao, list[Proposal]]:
    """Roda a reflexão; grava as propostas (salvo dry_run) e notifica o gestor.

    Devolve (relatório, propostas criadas). `dry_run=True` apenas detecta — não
    grava nem notifica (é o `cortex refletir --dry-run`).
    """
    relatorio = ReflectionEngine(store, janela_dias).refletir(agora)
    if dry_run:
        return relatorio, []

    criadas = aplicar_reflexao(store, relatorio, audit=audit)
    if notificador is not None:
        for proposta in criadas:
            notificador.notificar_nova_proposta(proposta)
    return relatorio, criadas


def criar_job_reflexao(
    store: MemoryStore,
    janela_dias: int,
    lock,
    *,
    audit=None,
    notificador=None,
) -> Callable[[], None]:
    """Fábrica do callback do Scheduler: roda a reflexão SOB o lock global.

    A reflexão escreve propostas no store — não pode correr com um turno (engine
    não é thread-safe). Segura o lock durante o batch (de madrugada, turnos
    esperam — aceitável; dívida anotada para volume).
    """

    def job() -> None:
        with lock:
            relatorio, criadas = executar_reflexao(
                store, janela_dias, audit=audit, notificador=notificador
            )
        logger.info(
            "job de reflexão: %d episódio(s) lido(s), %d proposta(s) emitida(s)",
            relatorio.episodios_lidos,
            len(criadas),
        )

    return job
