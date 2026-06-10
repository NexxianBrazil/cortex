"""Testes da ponte memória↔runtime (Fase 3c).

Determinísticos e no CI: StubProvider + InMemoryStore + HeuristicClassifier.
O teste central prova a fase — a Mariana LEMBRA de uma sessão para outra.
"""

from pathlib import Path

import pytest

from cortex.config import CortexConfig
from cortex.identity import carregar_persona
from cortex.memory import (
    Belief,
    ConfiguracaoClassifierError,
    DictAuthorityMap,
    DictSourceOfTruth,
    HeuristicClassifier,
    InMemoryStore,
    Justification,
    MemoryEngine,
    Relationship,
    Source,
    SourceKind,
    criar_classifier,
)
from cortex.memory.classifier import LLMClassifier
from cortex.runtime import (
    AgentLoop,
    LLMResponse,
    Session,
    ToolCall,
    autoridade_da_persona,
    extrair_candidatos,
    recuperar_beliefs,
)
from cortex.runtime.promotion import DOMINIO_PADRAO
from cortex.runtime.recall import formatar_beliefs

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"


@pytest.fixture(scope="module")
def persona():
    return carregar_persona(PERSONAS_DIR)


def _novo_engine() -> MemoryEngine:
    return MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap({}),
        source_of_truth=DictSourceOfTruth({}),
    )


def _stub_consulta_preco() -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCall(id="t1", nome="consultar_preco", argumentos={"codigo_produto": "PRD-001"})
        ]
    )


# ---------------------------------------------------------------------------
# O TESTE QUE PROVA A FASE: a Mariana lembra entre sessões
# ---------------------------------------------------------------------------


def test_mariana_lembra_entre_sessoes(persona):
    """Sessão 1 aprende um fato (via tool) e promove; sessão 2 o recupera.

    Mesmo engine/store entre as duas sessões (runtime efêmero, memória não).
    """
    from cortex.runtime import StubProvider, criar_registry_mock

    engine = _novo_engine()
    registry = criar_registry_mock(persona.tools)

    # --- Sessão 1: stub pede a tool, recebe o preço, conclui em texto. ---
    stub1 = StubProvider(
        roteiro=[_stub_consulta_preco(), LLMResponse(texto="O preço do PRD-001 é R$ 1250,00.")]
    )
    loop1 = AgentLoop(stub1, registry, memory=engine)
    s1 = Session(persona)
    loop1.executar_turno(s1, "Qual o preço do PRD-001?")

    # O fato foi promovido à memória (passou pelo observe()).
    ativo = engine.active("produto:PRD-001:preco")
    assert ativo is not None
    assert "1250" in ativo.value
    assert ativo.source.name == "consultar_preco"

    # --- Sessão 2: nova Session/stub/loop, MESMO engine. ---
    stub2 = StubProvider(roteiro=[LLMResponse(texto="O preço que tenho é R$ 1250,00.")])
    loop2 = AgentLoop(stub2, registry, memory=engine)
    s2 = Session(persona)
    assert s2.historico == []  # runtime efêmero: a sessão nasce vazia

    loop2.executar_turno(s2, "Você lembra o preço do PRD-001?")

    # O belief foi RECUPERADO e injetado no system prompt da sessão 2.
    system_usado = stub2.chamadas[0][0]
    assert "produto:PRD-001:preco" in system_usado
    assert "1250" in system_usado
    assert "Memória — o que você já sabe" in system_usado


# ---------------------------------------------------------------------------
# Promoção conservadora
# ---------------------------------------------------------------------------


def test_promocao_conservadora_conversa_generica_nao_vira_belief(persona):
    """Conversa sem tool/candidato claro NÃO gera nenhum belief."""
    from cortex.runtime import StubProvider, criar_registry_mock

    engine = _novo_engine()
    registry = criar_registry_mock(persona.tools)
    stub = StubProvider(roteiro=[LLMResponse(texto="Olá! Como posso ajudar hoje?")])
    loop = AgentLoop(stub, registry, memory=engine)

    loop.executar_turno(Session(persona), "bom dia, tudo bem por aí?")

    assert engine.store.all_beliefs() == []  # nada promovido


def test_extrair_candidatos_ignora_texto_e_erros(persona):
    """A regra de candidatos só pega resultado de tool com extrator; resto não."""
    from cortex.runtime import Message, Role

    mensagens = [
        Message(role=Role.USER, content="me vê um preço"),
        Message(role=Role.ASSISTANT, content="claro"),
        # tool com erro → ignorada
        Message(role=Role.TOOL, content="ERRO: x", nome_tool="consultar_preco", erro=True),
        # tool sem extrator → ignorada
        Message(role=Role.TOOL, content='{"x":1}', nome_tool="enviar_email"),
        # tool válida → 1 candidato
        Message(
            role=Role.TOOL,
            content='{"codigo_produto":"PRD-002","preco_unitario":380.5,"moeda":"BRL"}',
            nome_tool="consultar_preco",
        ),
    ]
    candidatos = extrair_candidatos(mensagens)
    assert len(candidatos) == 1
    assert candidatos[0].key == "produto:PRD-002:preco"
    assert candidatos[0].source.kind is SourceKind.TOOL


# ---------------------------------------------------------------------------
# Recuperação enxuta e relevante
# ---------------------------------------------------------------------------


def test_recuperacao_e_enxuta_e_salientes_primeiro():
    """Recall respeita o limite e ranqueia por relevância/saliência."""
    engine = _novo_engine()
    # 8 beliefs independentes; um deles reforçado (mais saliente).
    for i in range(8):
        engine.observe(
            f"produto:P{i}:preco", f"R$ {i}", Source(name="t", kind=SourceKind.TOOL),
            Justification(), domain="comercial",
        )
    for _ in range(4):  # reforça P3 → saliência alta
        engine.observe(
            "produto:P3:preco", "R$ 3", Source(name="t", kind=SourceKind.TOOL),
            Justification(), domain="comercial",
        )

    # Entrada sem palavra-chave casável → cai no desempate por saliência.
    top = recuperar_beliefs(engine, "qualquer coisa", limite=3)
    assert len(top) == 3  # ENXUTO: não despeja os 8
    assert top[0].key == "produto:P3:preco"  # o mais saliente primeiro

    # Entrada com chave relacionada → o belief correspondente sobe.
    rel = recuperar_beliefs(engine, "quanto custa o P5?", limite=3)
    assert any(b.key == "produto:P5:preco" for b in rel)


def test_recuperacao_injeta_enxuto_no_contexto():
    """A injeção no system prompt não inclui a memória inteira."""
    engine = _novo_engine()
    for i in range(10):
        engine.observe(
            f"produto:P{i}:preco", f"R$ {i}", Source(name="t", kind=SourceKind.TOOL),
            Justification(), domain="comercial",
        )
    beliefs = recuperar_beliefs(engine, "qualquer", limite=3)
    texto = formatar_beliefs(beliefs)
    assert texto.count("(fonte:") == 3  # só 3 linhas, não 10


# ---------------------------------------------------------------------------
# Runtime efêmero continua morrendo; só o promovido sobrevive
# ---------------------------------------------------------------------------


def test_runtime_efemero_morre_so_promovido_sobrevive(persona):
    from cortex.runtime import StubProvider, criar_registry_mock

    engine = _novo_engine()
    registry = criar_registry_mock(persona.tools)
    stub = StubProvider(
        roteiro=[_stub_consulta_preco(), LLMResponse(texto="pronto")]
    )
    loop = AgentLoop(stub, registry, memory=engine)

    s1 = Session(persona)
    loop.executar_turno(s1, "preço do PRD-001?")
    assert s1.historico  # a sessão 1 acumulou histórico durante o turno

    s2 = Session(persona)
    assert s2.historico == []  # ...mas uma sessão nova nasce sem nada
    # O promovido, sim, sobrevive no motor de memória.
    assert engine.active("produto:PRD-001:preco") is not None


# ---------------------------------------------------------------------------
# LLMClassifier — ligável, determinístico via StubProvider (sem rede)
# ---------------------------------------------------------------------------


def test_llm_classifier_interpreta_resposta_do_provider():
    from cortex.runtime import StubProvider

    base = Belief(
        key="cliente:ABC:prazo", value="30 dias", source=Source(name="A", kind=SourceKind.HUMAN),
        justification=Justification(), domain="comercial", confidence=0.5,
    )
    # Curto-circuito: sem crença vigente, nem chama o LLM.
    clf_vazio = LLMClassifier(StubProvider(roteiro=[LLMResponse(texto="ignorado")]))
    assert clf_vazio.classify(None, "x") is Relationship.INDEPENDENT

    # O stub devolve "contradiz" → o classificador mapeia para CONTRADICTS.
    clf = LLMClassifier(StubProvider(roteiro=[LLMResponse(texto="contradiz")]))
    assert clf.classify(base, "trimestral") is Relationship.CONTRADICTS

    clf2 = LLMClassifier(StubProvider(roteiro=[LLMResponse(texto="reforça")]))
    assert clf2.classify(base, "um mês") is Relationship.REINFORCES


def test_criar_classifier_llm_com_provider():
    from cortex.runtime import StubProvider

    clf = criar_classifier(
        CortexConfig(classifier="llm"),
        StubProvider(roteiro=[LLMResponse(texto="ok")]),
    )
    assert isinstance(clf, LLMClassifier)


def test_criar_classifier_llm_sem_provider_falha():
    with pytest.raises(ConfiguracaoClassifierError, match="LLMProvider"):
        criar_classifier(CortexConfig(classifier="llm"))


# ---------------------------------------------------------------------------
# Correção 4: AuthorityMap derivado do USER.md (gestor é autoritativo)
# ---------------------------------------------------------------------------


def test_authority_map_deriva_do_user_md(persona):
    """O gestor do USER.md é autoritativo no domínio; colegas não."""
    amap = autoridade_da_persona(persona)
    gestor = persona.user.autoridade.gestor.nome
    assert amap.is_authoritative(gestor, DOMINIO_PADRAO)
    # Relacionamento ≠ autoridade: nenhum colega é fonte autoritativa.
    for colega in persona.user.relacionamento:
        assert not amap.is_authoritative(colega.nome, DOMINIO_PADRAO)
