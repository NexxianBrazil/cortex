"""Testes do motor de reflexão batch (Fase 8) — destila padrões em PROPOSTAS.

A reflexão nunca muta: tudo vira Proposal PENDENTE. Frequência vira saliência
(não autoridade); contradição crônica vira pedido de REVISÃO (sem valor); o
detector procedural existe mas é inerte sem sinal de sucesso; SOR é intocado.
"""

from cortex.governance import AuditTrail
from cortex.memory import (
    DictAuthorityMap,
    DictSourceOfTruth,
    HeuristicClassifier,
    InMemoryStore,
    Justification,
    MemoryEngine,
    Source,
    SourceKind,
)
from cortex.memory.episodic import Episode
from cortex.memory.learning import ProposalKind, ProposalStatus
from cortex.memory.models import Relationship
from cortex.reflection import ReflectionEngine, aplicar_reflexao
from cortex.risk import RiskLevel


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


def test_reforco_recorrente_propoe_consolidacao():
    store = InMemoryStore()
    for nome in ("Ana", "Bia", "Caio"):  # 3 fontes INTERNAS distintas
        _ep(store, "cliente:ACME:contato", "joao@acme.com", nome)

    rl = ReflectionEngine(store, janela_dias=1).refletir()
    props = [p for p in rl.propostas if p.detector == "reforco_recorrente"]
    assert len(props) == 1
    p = props[0]
    assert p.key == "cliente:ACME:contato" and p.proposed_value == "joao@acme.com"
    assert p.saliencia >= 3  # frequência → saliência
    assert p.kind is ProposalKind.MEMORIA

    # Materializada: NÃO nasce autoritativa — a fonte é a reflexão (AGENT), não o gestor.
    criadas = aplicar_reflexao(store, rl)
    prop = next(c for c in criadas if c.key == "cliente:ACME:contato")
    assert prop.source.kind is SourceKind.AGENT
    assert prop.source.name == "reflexão"
    assert "reflexão" in prop.reason


def test_menos_que_o_minimo_de_fontes_nao_propoe():
    store = InMemoryStore()
    _ep(store, "cliente:X:y", "v", "Ana")
    _ep(store, "cliente:X:y", "v", "Bia")  # só 2 fontes
    assert ReflectionEngine(store, janela_dias=1).refletir().propostas == []


def test_contradicao_cronica_propoe_revisar_sem_valor():
    store = InMemoryStore()
    for i in range(3):
        _ep(
            store,
            "cliente:ACME:prazo",
            f"{30 + i * 10} dias",
            f"Fonte{i}",
            rel=Relationship.CONTRADICTS,
            escalated=True,
        )
    rl = ReflectionEngine(store, janela_dias=1).refletir()
    revs = [p for p in rl.propostas if p.detector == "contradicao_cronica"]
    assert len(revs) == 1
    assert revs[0].kind is ProposalKind.REVISAR
    # Não propõe um valor — propõe ATENÇÃO.
    assert "revisar" in revs[0].proposed_value.lower()


def test_reflexao_idempotente_por_janela():
    store = InMemoryStore()
    for nome in ("Ana", "Bia", "Caio"):
        _ep(store, "cliente:ACME:contato", "joao@acme.com", nome)

    rl1 = ReflectionEngine(store, janela_dias=1).refletir()
    aplicar_reflexao(store, rl1)
    n = len(store.proposals(ProposalStatus.PENDENTE))
    assert n >= 1

    # 2ª rodada: o que já está PENDENTE não é reproposto.
    rl2 = ReflectionEngine(store, janela_dias=1).refletir()
    assert rl2.propostas == []
    aplicar_reflexao(store, rl2)
    assert len(store.proposals(ProposalStatus.PENDENTE)) == n


def test_nao_repropoe_o_que_ja_e_crenca_ativa():
    # Via observe(), as 3 afirmações JÁ consolidam a crença ativa → nada a propor.
    engine = MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap({}),
        source_of_truth=DictSourceOfTruth({}),
    )
    for nome in ("Ana", "Bia", "Caio"):
        engine.observe(
            "cliente:ACME:contato",
            "joao@acme.com",
            Source(name=nome, kind=SourceKind.HUMAN),
            Justification(why="dito"),
            domain="comercial",
        )
    assert engine.active("cliente:ACME:contato").value == "joao@acme.com"
    rl = ReflectionEngine(engine.store, janela_dias=1).refletir()
    assert [p for p in rl.propostas if p.detector == "reforco_recorrente"] == []


def test_eficacia_procedural_inerte_sem_sinal_de_sucesso():
    store = InMemoryStore()
    for i in range(5):
        _ep(store, f"operacao:{i}", "feita", f"S{i}")
    rl = ReflectionEngine(store, janela_dias=1).refletir()
    assert rl.por_detector["eficacia_procedural"] == 0  # existe, não inventa


def test_reflexao_nao_propoe_sobre_system_of_record():
    # Preço é dado VIVO (Plano 4): a reflexão NÃO o memoriza, por mais frequente.
    store = InMemoryStore()
    for nome in ("Ana", "Bia", "Caio", "Dora"):
        _ep(store, "produto:PRD-001:preco", "R$ 99", nome)
    assert ReflectionEngine(store, janela_dias=1).refletir().propostas == []


def test_aplicar_grava_propostas_e_audita(tmp_path):
    store = InMemoryStore()
    for nome in ("Ana", "Bia", "Caio"):
        _ep(store, "cliente:ACME:contato", "joao@acme.com", nome)
    audit = AuditTrail(tmp_path / "audit.jsonl")

    rl = ReflectionEngine(store, janela_dias=1).refletir()
    criadas = aplicar_reflexao(store, rl, audit=audit)

    assert len(criadas) == 1
    assert len(store.proposals(ProposalStatus.PENDENTE)) == 1
    tipos = {ln["tipo"] for ln in audit.ultimos(20)}
    assert "reflexao" in tipos and "reflexao_proposta" in tipos
