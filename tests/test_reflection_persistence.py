"""Persistência das propostas da reflexão (Fase 8) — GraphitiStore + Kuzu.

Pulam-se sem graphiti-core[kuzu] (o CI nunca depende do Kuzu). Confirmam que as
propostas emitidas pela reflexão (incl. a de REVISÃO) sobrevivem a fechar/reabrir
o banco, e que `ids_proposta` não colide ao reabrir em processo novo.
"""

import pytest

pytest.importorskip("graphiti_core")
pytest.importorskip("kuzu")

from cortex.memory import (  # noqa: E402
    Justification,
    Source,
    SourceKind,
)
from cortex.memory.episodic import Episode  # noqa: E402
from cortex.memory.graphiti_store import GraphitiStore  # noqa: E402
from cortex.memory.learning import (  # noqa: E402
    Proposal,
    ProposalKind,
    ProposalStatus,
)
from cortex.memory.models import Relationship  # noqa: E402
from cortex.reflection import ReflectionEngine, aplicar_reflexao  # noqa: E402
from cortex.risk import RiskLevel  # noqa: E402


def _ep(store, key, val, nome, *, rel=Relationship.REINFORCES, escalated=False):
    store.add_episode(
        Episode(
            key=key,
            asserted_value=val,
            source=Source(name=nome, kind=SourceKind.HUMAN),
            justification=Justification(why="dito"),
            domain="comercial",
            relationship=rel,
            risk=RiskLevel.LOW,
            action="memorizou",
            escalated=escalated,
        )
    )


def _semear_padroes(store):
    for nome in ("Ana", "Bia", "Caio"):  # reforço → consolidar (MEMORIA)
        _ep(store, "cliente:ACME:contato", "joao@acme.com", nome)
    for i in range(3):  # contradição crônica → revisar (REVISAR)
        _ep(store, "cliente:ACME:prazo", f"{30 + i * 10} dias", f"F{i}",
            rel=Relationship.CONTRADICTS, escalated=True)


def test_propostas_da_reflexao_sobrevivem_a_reabertura(tmp_path):
    db = tmp_path / "reflexao.kuzu"

    store1 = GraphitiStore(db)
    try:
        _semear_padroes(store1)
        rl = ReflectionEngine(store1, janela_dias=1).refletir()
        criadas = aplicar_reflexao(store1, rl)
        assert {c.kind for c in criadas} == {ProposalKind.MEMORIA, ProposalKind.REVISAR}
    finally:
        store1.close()

    # Reabre o MESMO arquivo: as propostas (e seus kinds) sobreviveram.
    store2 = GraphitiStore(db)
    try:
        pendentes = store2.proposals(ProposalStatus.PENDENTE)
        assert len(pendentes) == 2
        assert {p.kind for p in pendentes} == {ProposalKind.MEMORIA, ProposalKind.REVISAR}
    finally:
        store2.close()


def test_ids_de_proposta_nao_colidem_apos_reabrir_em_processo_novo(tmp_path):
    from cortex.memory import learning as learning_mod
    from cortex.memory.models import Contador

    db = tmp_path / "reflexao_ids.kuzu"

    store1 = GraphitiStore(db)
    try:
        _semear_padroes(store1)
        aplicar_reflexao(store1, ReflectionEngine(store1, janela_dias=1).refletir())
        ids_antes = {p.id for p in store1.proposals()}
    finally:
        store1.close()

    # "Processo novo": zera o contador de ids de proposta.
    learning_mod.ids_proposta = Contador()

    store2 = GraphitiStore(db)
    try:
        # A hidratação avança o contador para além do maior id persistido.
        nova = Proposal(
            key="cliente:NOVO:fato",
            current_value=None,
            proposed_value="x",
            source=Source(name="reflexão", kind=SourceKind.AGENT),
            justification=Justification(why="nova"),
            domain="comercial",
            risk=RiskLevel.LOW,
            reason="[reflexão] nova",
            origin_episode_id=0,
        )
        store2.add_proposal(nova)
        assert nova.id not in ids_antes  # nenhum id colidiu
        assert len(store2.proposals()) == len(ids_antes) + 1
    finally:
        store2.close()
