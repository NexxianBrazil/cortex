"""Testes do Scheduler (Fase 8) — relógio injetado, sem dormir nem threads reais."""

import threading
from datetime import UTC, datetime, timedelta

from cortex.memory import InMemoryStore, Justification, Source, SourceKind
from cortex.memory.episodic import Episode
from cortex.memory.learning import ProposalStatus
from cortex.memory.models import Relationship
from cortex.ops.scheduler import Scheduler
from cortex.reflection.servico import criar_job_reflexao, executar_reflexao
from cortex.risk import RiskLevel


class _Relogio:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def _store_com_padrao():
    store = InMemoryStore()
    for nome in ("Ana", "Bia", "Caio"):
        store.add_episode(
            Episode(
                key="cliente:ACME:contato",
                asserted_value="joao@acme.com",
                source=Source(name=nome, kind=SourceKind.HUMAN),
                justification=Justification(why="dito"),
                domain="comercial",
                relationship=Relationship.REINFORCES,
                risk=RiskLevel.LOW,
                action="memorizou",
            )
        )
    return store


def test_diario_dispara_no_horario_e_rearma():
    relogio = _Relogio(datetime(2026, 1, 1, 2, 0, tzinfo=UTC))
    sched = Scheduler(agora=relogio)
    disparos = []
    sched.agendar_diario("03:00", lambda: disparos.append(1))

    sched.tick()
    assert disparos == []  # ainda 02:00

    relogio.t = datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
    sched.tick()
    assert disparos == [1]
    sched.tick()
    assert disparos == [1]  # não redispara no mesmo dia (re-armou p/ amanhã)

    relogio.t = datetime(2026, 1, 2, 3, 0, tzinfo=UTC)
    sched.tick()
    assert disparos == [1, 1]


def test_intervalo_dispara_repetidamente():
    relogio = _Relogio(datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
    sched = Scheduler(agora=relogio)
    n = []
    sched.agendar_intervalo(10, lambda: n.append(1))

    sched.tick()
    assert n == []  # próximo só em +10min
    relogio.t += timedelta(minutes=10)
    sched.tick()
    assert len(n) == 1
    relogio.t += timedelta(minutes=10)
    sched.tick()
    assert len(n) == 2


def test_job_de_reflexao_pega_o_lock_e_grava():
    store = _store_com_padrao()

    class _LockSpy:
        def __init__(self) -> None:
            self.acquires = 0
            self._real = threading.Lock()

        def __enter__(self):
            self.acquires += 1
            return self._real.__enter__()

        def __exit__(self, *a):
            return self._real.__exit__(*a)

    spy = _LockSpy()
    job = criar_job_reflexao(store, janela_dias=1, lock=spy)
    job()

    assert spy.acquires == 1  # o job adquiriu o lock global
    assert len(store.proposals(ProposalStatus.PENDENTE)) >= 1  # e gravou a proposta


def test_dry_run_nao_grava_sem_dry_run_grava():
    store = _store_com_padrao()
    relatorio, criadas = executar_reflexao(store, 1, dry_run=True)
    assert relatorio.propostas  # detectou o padrão
    assert criadas == []
    assert store.proposals(ProposalStatus.PENDENTE) == []  # nada gravado

    relatorio2, criadas2 = executar_reflexao(store, 1, dry_run=False)
    assert len(criadas2) == len(relatorio2.propostas) >= 1
    assert len(store.proposals(ProposalStatus.PENDENTE)) >= 1
