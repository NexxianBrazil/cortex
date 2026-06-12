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


# ---------------------------------------------------------------------------
# Fase 3c × persistência real: a Mariana lembra ENTRE EXECUÇÕES (em disco)
# ---------------------------------------------------------------------------


def test_mariana_lembra_com_persistencia_real_em_disco(tmp_path, promove_cotacao):
    """Aprende numa sessão+store, reabre o store noutra sessão, e lembra.

    Une a ponte da 3c (promoção+recuperação) à persistência da 3b (Kuzu):
    o fato promovido sobrevive ao fechar e reabrir o arquivo. Promove a última
    cotação (extrator de teste) — preço é dado vivo e nunca se memoriza (5b).
    """
    from cortex.identity import carregar_persona
    from cortex.memory import HeuristicClassifier, MemoryEngine
    from cortex.runtime import (
        AgentLoop,
        LLMResponse,
        Session,
        StubProvider,
        ToolCall,
        criar_registry_mock,
    )

    personas_dir = __import__("pathlib").Path(__file__).resolve().parent.parent / "personas"
    persona = carregar_persona(personas_dir)
    registry = criar_registry_mock(persona.tools)
    db = tmp_path / "lembranca.kuzu"

    def _engine(store):
        return MemoryEngine(
            store=store,
            classifier=HeuristicClassifier(),
            authority_map=DictAuthorityMap({}),
            source_of_truth=DictSourceOfTruth({}),
        )

    # --- Execução 1: aprende via tool e promove; fecha o store (em disco). ---
    store1 = GraphitiStore(db)
    eng1 = _engine(store1)
    stub1 = StubProvider(
        roteiro=[
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="t1",
                        nome="emitir_cotacao",
                        argumentos={
                            "cliente_id": "CLI-001",
                            "itens": [{"codigo": "PRD-001", "qtd": 1}],
                            "condicao_pagamento": "28 DDL",
                        },
                    )
                ]
            ),
            LLMResponse(texto="Cotação COT-CLI-001-0001 emitida."),
        ]
    )
    AgentLoop(stub1, registry, memory=eng1).executar_turno(
        Session(persona), "emita uma cotação para o CLI-001"
    )
    assert eng1.active("cliente:CLI-001:ultima_cotacao") is not None
    store1.close()

    # --- Execução 2: reabre o MESMO arquivo; a Mariana recupera o fato. ---
    store2 = GraphitiStore(db)
    try:
        eng2 = _engine(store2)
        stub2 = StubProvider(roteiro=[LLMResponse(texto="Foi a COT-CLI-001-0001.")])
        loop2 = AgentLoop(stub2, registry, memory=eng2)
        loop2.executar_turno(Session(persona), "você lembra a última cotação do CLI-001?")
        system_usado = stub2.chamadas[0][0]
        assert "cliente:CLI-001:ultima_cotacao" in system_usado
        assert "COT-CLI-001-0001" in system_usado
    finally:
        store2.close()


# ---------------------------------------------------------------------------
# Regressão (Correção 1): colisão de IDs entre sessões corrompia o banco
# ---------------------------------------------------------------------------


def test_ids_nao_colidem_apos_reabrir_em_processo_novo(tmp_path):
    """Simula um PROCESSO NOVO (contadores zerados) reabrindo o banco.

    Antes da correção, a 1ª ESCRITA da segunda sessão quebrava o checkpoint
    com 'duplicated primary key' (contador recomeçava do 1 e colidia com os
    ids hidratados) — e o DETACH DELETE prévio corrompia o banco. Agora a
    hidratação avança os contadores para além do maior id persistido.
    """
    from cortex.memory import entity as entity_mod
    from cortex.memory import episodic as episodic_mod
    from cortex.memory import semantic as semantic_mod
    from cortex.memory.models import Contador

    db = tmp_path / "ids.kuzu"

    # --- Sessão 1: grava 2 beliefs e fecha. ---
    store1 = GraphitiStore(db)
    eng1 = _novo_engine(store1)
    eng1.observe(
        "k:a", "v1", Source(name="t", kind=SourceKind.TOOL),
        Justification(), domain="comercial",
    )
    eng1.observe(
        "k:b", "v2", Source(name="t", kind=SourceKind.TOOL),
        Justification(), domain="comercial",
    )
    store1.close()

    # --- "Processo novo": zera os três contadores. ---
    semantic_mod.ids_crenca = Contador()
    episodic_mod.ids_episodio = Contador()
    entity_mod.ids_entidade = Contador()

    # --- Sessão 2: hidrata (deve avançar os contadores) e ESCREVE. ---
    store2 = GraphitiStore(db)
    try:
        eng2 = _novo_engine(store2)
        # A escrita abaixo quebrava aqui antes da correção.
        eng2.observe(
            "k:c", "v3", Source(name="t", kind=SourceKind.TOOL),
            Justification(), domain="comercial",
        )
        ids = {b.id for b in store2.all_beliefs()}
        assert len(ids) == 3  # nenhum id colidiu
    finally:
        store2.close()

    # --- Sessão 3: reabre e confirma os 3 beliefs íntegros. ---
    store3 = GraphitiStore(db)
    try:
        chaves = {b.key for b in store3.all_beliefs()}
        ids3 = {b.id for b in store3.all_beliefs()}
        assert chaves == {"k:a", "k:b", "k:c"}
        assert len(ids3) == 3  # ids únicos preservados
    finally:
        store3.close()


# ---------------------------------------------------------------------------
# Learning Queue persistida (Fase 4b): sobrevive a restart
# ---------------------------------------------------------------------------


def test_proposta_sobrevive_e_decisao_persiste(tmp_path):
    """Restart não perde a fila: proposta PENDENTE sobrevive; aprovar na 2ª
    sessão funciona; o estado decidido + episódio sobrevivem à 3ª reabertura."""
    from cortex.memory import DictAuthorityMap, ProposalStatus

    db = tmp_path / "fila.kuzu"
    gestor = "CFO Denilson"

    def _eng(store):
        return MemoryEngine(
            store=store,
            classifier=HeuristicClassifier(),
            authority_map=DictAuthorityMap({"comercial": {gestor}}),
            source_of_truth=DictSourceOfTruth({}),
        )

    # --- Sessão 1: escala (cria proposta pendente) e fecha. ---
    store1 = GraphitiStore(db)
    eng1 = _eng(store1)
    eng1.observe(
        "cliente:X:limite", "R$ 50.000", Source(name=gestor, kind=H),
        Justification(why="crédito"), domain="comercial",
    )
    eng1.observe(
        "cliente:X:limite", "R$ 500.000", Source(name="Estagiário", kind=H),
        Justification(), domain="comercial",
    )
    pid = eng1.pending_approvals[-1].id
    store1.close()

    # --- Sessão 2: a proposta sobreviveu; aprovar funciona. ---
    store2 = GraphitiStore(db)
    try:
        eng2 = _eng(store2)
        pend = eng2.store.proposals(ProposalStatus.PENDENTE)
        assert len(pend) == 1 and pend[0].id == pid
        eng2.aprovar(pid, autor=gestor, razao="aprovado em comitê")
        assert eng2.active("cliente:X:limite").value == "R$ 500.000"
    finally:
        store2.close()

    # --- Sessão 3: o estado decidido e o episódio sobreviveram. ---
    store3 = GraphitiStore(db)
    try:
        eng3 = _eng(store3)
        p = eng3.store.proposal_by_id(pid)
        assert p.status is ProposalStatus.APROVADA
        assert p.decided_by == gestor
        assert eng3.active("cliente:X:limite").value == "R$ 500.000"
        # O episódio da decisão (autor como fonte) sobreviveu.
        eps = eng3.store.episodes_for("cliente:X:limite")
        assert any("APROVOU" in e.action and e.source.name == gestor for e in eps)
    finally:
        store3.close()


def test_ids_proposta_nao_colidem_apos_reabrir_em_processo_novo(tmp_path):
    """ids_proposta avança na hidratação (mesma classe de bug da colisão de PK)."""
    from cortex.memory import DictAuthorityMap
    from cortex.memory import learning as learning_mod
    from cortex.memory.models import Contador

    db = tmp_path / "fila_ids.kuzu"
    gestor = "CFO Denilson"

    def _eng(store):
        return MemoryEngine(
            store=store,
            classifier=HeuristicClassifier(),
            authority_map=DictAuthorityMap({"comercial": {gestor}}),
            source_of_truth=DictSourceOfTruth({}),
        )

    store1 = GraphitiStore(db)
    eng1 = _eng(store1)
    eng1.observe(
        "k", "R$ 50.000", Source(name=gestor, kind=H), Justification(why="x"), domain="comercial",
    )
    eng1.observe(
        "k", "R$ 500.000", Source(name="Estagiário", kind=H), Justification(), domain="comercial",
    )
    store1.close()

    # "Processo novo": zera o contador de propostas.
    learning_mod.ids_proposta = Contador()

    store2 = GraphitiStore(db)
    try:
        eng2 = _eng(store2)
        # Nova escala → nova proposta; não pode colidir com a hidratada.
        eng2.observe(
            "k2", "R$ 10.000", Source(name=gestor, kind=H),
            Justification(why="y"), domain="comercial",
        )
        eng2.observe(
            "k2", "R$ 100.000", Source(name="Estagiário", kind=H),
            Justification(), domain="comercial",
        )
        ids = {p.id for p in eng2.store.proposals()}
        assert len(ids) == 2  # nenhuma colisão
    finally:
        store2.close()


def test_proposta_acao_sobrevive_reabertura(tmp_path):
    """Proposta kind=ACAO (com consumed_at) sobrevive a fechar/reabrir o store."""
    from cortex.memory import DictAuthorityMap, ProposalKind, propor_acao
    from cortex.risk import RiskLevel

    db = tmp_path / "acao.kuzu"

    def _eng(store):
        return MemoryEngine(
            store=store,
            classifier=HeuristicClassifier(),
            authority_map=DictAuthorityMap({"comercial": {"CFO Denilson"}}),
            source_of_truth=DictSourceOfTruth({}),
        )

    args = {"destinatario": "cliente@gmail.com", "assunto": "x", "corpo": "y"}
    store1 = GraphitiStore(db)
    eng1 = _eng(store1)
    prop = propor_acao("enviar_email", args, RiskLevel.HIGH, ["externo"], "Mariana", "comercial")
    eng1.store.add_proposal(prop)
    eng1.aprovar(prop.id, autor="CFO Denilson", razao="ok")
    pid = prop.id
    store1.close()

    store2 = GraphitiStore(db)
    try:
        eng2 = _eng(store2)
        p = eng2.store.proposal_by_id(pid)
        assert p is not None
        assert p.kind is ProposalKind.ACAO
        assert p.status.value == "aprovada"
        assert p.consumed_at is None  # aprovada mas ainda não consumida
        # A exceção sobrevivente é consumível na nova sessão.
        import json

        aj = json.dumps(args, ensure_ascii=False, sort_keys=True)
        assert eng2.consumir_excecao("enviar_email", aj) is not None
    finally:
        store2.close()
