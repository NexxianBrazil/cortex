"""Testes de persistência da memória (Fase 3b) — GraphitiStore + Kuzu.

Pulam-se automaticamente se graphiti-core[kuzu] não estiver instalado, igual
aos testes live de LLM da Fase 2 — o CI nunca falha por ausência de Kuzu.

Cobrem duas coisas:
  - PARIDADE: os 5 cenários da 3a, rodados contra o GraphitiStore, produzem o
    MESMO resultado observável que contra o InMemoryStore. O store é trocável;
    a lógica não muda.
  - PERSISTÊNCIA REAL: escreve, fecha o store, reabre apontando para o mesmo
    arquivo Kuzu e confirma que ativo + histórico superado + lineage
    sobreviveram. Esse é o ponto da fase.
"""

import pytest

# Portão de disponibilidade: sem o pacote, o módulo inteiro é pulado.
pytest.importorskip("graphiti_core")
pytest.importorskip("kuzu")

from cortex.memory import (  # noqa: E402
    DictAuthorityMap,
    DictSourceOfTruth,
    HeuristicClassifier,
    InMemoryStore,
    Justification,
    MemoryEngine,
    Source,
    SourceKind,
    Status,
)
from cortex.memory.graphiti_store import GraphitiStore  # noqa: E402

H, S = SourceKind.HUMAN, SourceKind.SYSTEM


def _novo_engine(store) -> MemoryEngine:
    """Motor com os mesmos dados de autoridade/fonte de verdade da 3a."""
    return MemoryEngine(
        store=store,
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap(
            {
                "comercial": {"CFO Denilson", "Fonte de verdade"},
                "estoque": {"Líder de Estoque"},
            }
        ),
        source_of_truth=DictSourceOfTruth({"pedido:4471:total": "R$ 12.000"}),
    )


def _rodar_os_5_cenarios(engine: MemoryEngine) -> None:
    """Executa exatamente a mesma sequência dos cenários A–E da 3a."""
    # A) reforço
    for _ in range(3):
        engine.observe(
            "cliente:ABC:prazo", "30 dias", Source(name="Vendedor João", kind=H),
            Justification(why="histórico de pedidos"), domain="comercial",
        )
    # B) correção autoritativa não-verificável (supera)
    engine.observe(
        "cliente:ABC:prazo", "60 dias", Source(name="CFO Denilson", kind=H),
        Justification(why="renegociação contratual fechada ontem"), domain="comercial",
    )
    # C) magnitude absurda + autoridade menor (escala, não muda)
    engine.observe(
        "cliente:X:limite", "R$ 50.000", Source(name="CFO Denilson", kind=H),
        Justification(why="análise de crédito"), domain="comercial",
    )
    engine.observe(
        "cliente:X:limite", "R$ 500.000", Source(name="Estagiário", kind=H),
        Justification(), domain="comercial",
    )
    # D) conflito verificável (confere na fonte de verdade)
    engine.observe(
        "pedido:4471:total", "R$ 10.000", Source(name="Vendedor João", kind=H),
        Justification(verifiable=True, proof_pointer="SAP:VBAK/4471"), domain="comercial",
    )
    engine.observe(
        "pedido:4471:total", "R$ 12.000", Source(name="Mariana", kind=S),
        Justification(verifiable=True, proof_pointer="SAP:VBAK/4471"), domain="comercial",
    )
    # E) correção que preserva quem corrigiu e por quê
    engine.observe(
        "objeto:7:nome", "pratinho", Source(name="Colega Ana", kind=H),
        Justification(why="foi o que me falaram"), domain="comercial",
    )
    engine.observe(
        "objeto:7:nome", "pires", Source(name="CFO Denilson", kind=H),
        Justification(why="nome técnico correto da peça", evidence="catálogo"),
        domain="comercial",
    )


def _snapshot(engine: MemoryEngine) -> dict:
    """Resultado observável comparável entre stores (sem ids voláteis)."""
    chaves = ["cliente:ABC:prazo", "cliente:X:limite", "pedido:4471:total", "objeto:7:nome"]
    snap = {}
    for k in chaves:
        ativo = engine.active(k)
        snap[k] = {
            "ativo": ativo.value if ativo else None,
            "ativo_status": ativo.status.value if ativo else None,
            "reason_for_change": ativo.reason_for_change if ativo else None,
            "historico": [(b.value, b.status.value) for b in engine.history(k)],
            "episodios": len(engine.store.episodes_for(k)),
        }
    snap["_pendentes"] = len(engine.pending_approvals)
    return snap


# ---------------------------------------------------------------------------
# Paridade: GraphitiStore == InMemoryStore na semântica observável
# ---------------------------------------------------------------------------


def test_paridade_5_cenarios_graphiti_vs_inmemory(tmp_path):
    eng_mem = _novo_engine(InMemoryStore())
    _rodar_os_5_cenarios(eng_mem)

    store = GraphitiStore(tmp_path / "paridade.kuzu")
    try:
        eng_graph = _novo_engine(store)
        _rodar_os_5_cenarios(eng_graph)
        assert _snapshot(eng_graph) == _snapshot(eng_mem)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Persistência real: sobrevive ao fechar e reabrir o arquivo Kuzu
# ---------------------------------------------------------------------------


def test_persistencia_real_sobrevive_reabertura(tmp_path):
    db = tmp_path / "persistente.kuzu"

    # 1) escreve uma correção autoritativa (supera) e fecha o store.
    store1 = GraphitiStore(db)
    eng = _novo_engine(store1)
    eng.observe(
        "cliente:ABC:prazo", "30 dias", Source(name="Vendedor João", kind=H),
        Justification(why="histórico"), domain="comercial",
    )
    eng.observe(
        "cliente:ABC:prazo", "60 dias", Source(name="CFO Denilson", kind=H),
        Justification(why="renegociação"), domain="comercial",
    )
    store1.close()  # checkpoint final + fecha o arquivo

    # 2) REABRE outro store no MESMO arquivo: a memória sobreviveu?
    store2 = GraphitiStore(db)
    try:
        eng2 = _novo_engine(store2)

        # Crença ativa correta após reabrir.
        ativo = eng2.active("cliente:ABC:prazo")
        assert ativo is not None
        assert ativo.value == "60 dias"
        assert ativo.reason_for_change == "renegociação"

        # Histórico superado preservado (nada apagado), bi-temporal intacto.
        historia = eng2.history("cliente:ABC:prazo")
        assert [(b.value, b.status.value) for b in historia] == [
            ("30 dias", Status.SUPERSEDED.value),
            ("60 dias", Status.ACTIVE.value),
        ]
        superada = [b for b in historia if b.status is Status.SUPERSEDED][0]
        assert superada.invalid_at is not None

        # Lineage da supersessão sobreviveu (a nova aponta para a antiga).
        assert ativo.supersedes == superada.id

        # Episódica preservada: as 2 observações continuam registradas.
        assert len(eng2.store.episodes_for("cliente:ABC:prazo")) == 2
    finally:
        store2.close()


def test_reabrir_arquivo_vazio_nao_quebra(tmp_path):
    """Abrir um arquivo Kuzu novo (sem dados) hidrata para um store vazio."""
    store = GraphitiStore(tmp_path / "vazio.kuzu")
    try:
        assert store.beliefs_for("qualquer") == []
        assert store.episodes() == []
        assert store.entities() == []
    finally:
        store.close()
