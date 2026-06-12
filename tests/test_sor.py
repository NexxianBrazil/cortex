"""Testes da Fase 5b — systems of record via gateway (a verdade viva).

Determinísticos e no CI: MockSORGateway por default (sem rede) e httpx.MockTransport
para o caminho HTTP. Cobrem o gateway, as tools vivas, o cético conferindo a fonte
viva (dois ramos) e o lineage SEM cópia (evento consulta_sor no audit).
"""

from pathlib import Path

import httpx
import pytest

from cortex.governance import AuditTrail
from cortex.identity import carregar_persona
from cortex.memory import (
    DictAuthorityMap,
    HeuristicClassifier,
    InMemoryStore,
    Justification,
    MemoryEngine,
    Source,
    SourceKind,
)
from cortex.memory.text import normalizar
from cortex.runtime import (
    AgentLoop,
    LLMResponse,
    Role,
    Session,
    StubProvider,
    ToolCall,
    criar_registry_mock,
)
from cortex.runtime.tools import ToolError
from cortex.sor import (
    ConsultarClienteTool,
    ConsultarPrecoTool,
    GatewaySourceOfTruth,
    HTTPSORGateway,
    MockSORGateway,
    SORGateway,
    SORIndisponivelError,
    registrar_tools_sor,
)

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"


@pytest.fixture(scope="module")
def persona():
    return carregar_persona(PERSONAS_DIR)


class _GatewayQuebrado(SORGateway):
    """Simula o SAP fora do ar — toda consulta levanta SORIndisponivelError."""

    def preco(self, codigo_produto):
        raise SORIndisponivelError("SAP fora do ar")

    def cliente(self, cliente_id):
        raise SORIndisponivelError("SAP fora do ar")


def _pedido_consultar_preco(codigo: str = "PRD-001") -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCall(id="t1", nome="consultar_preco", argumentos={"codigo_produto": codigo})
        ]
    )


# ---------------------------------------------------------------------------
# MockSORGateway
# ---------------------------------------------------------------------------


def test_mock_gateway_preco_cliente_e_inexistentes():
    g = MockSORGateway()
    preco = g.preco("PRD-001")
    assert preco is not None and preco.preco_unitario == 1250.00 and preco.moeda == "BRL"
    assert g.preco("INEXISTENTE") is None  # None = não há registro (≠ erro)

    cli = g.cliente("CLI-002")
    assert cli is not None and cli.bloqueado is True
    assert g.cliente("NAO-EXISTE") is None


# ---------------------------------------------------------------------------
# Tools vivas via gateway
# ---------------------------------------------------------------------------


def test_tool_consultar_preco_shape_e_nao_encontrado():
    tool = ConsultarPrecoTool(MockSORGateway())
    ok = tool("PRD-001", quantidade=3)
    assert ok == {
        "encontrado": True,
        "codigo_produto": "PRD-001",
        "preco_unitario": 1250.00,
        "moeda": "BRL",
        "disponivel": True,
        "quantidade_consultada": 3,
    }
    nao = tool("INEXISTENTE")
    assert nao == {"encontrado": False, "codigo_produto": "INEXISTENTE"}


def test_tool_consultar_cliente_bloqueado():
    tool = ConsultarClienteTool(MockSORGateway())
    cli = tool("CLI-002")
    assert cli["encontrado"] is True and cli["bloqueado"] is True
    assert tool("NAO-EXISTE") == {"encontrado": False, "cliente_id": "NAO-EXISTE"}


def test_tool_indisponivel_vira_toolerror():
    """SOR fora do ar → ToolError (tratável), não exceção crua."""
    tool = ConsultarPrecoTool(_GatewayQuebrado())
    with pytest.raises(ToolError, match="indisponível"):
        tool("PRD-001")


def test_loop_segue_quando_sor_indisponivel(persona):
    """No loop, a indisponibilidade do SOR vira resultado de erro e o turno segue."""
    registry = criar_registry_mock(persona.tools)
    registrar_tools_sor(registry, _GatewayQuebrado())
    stub = StubProvider(
        roteiro=[_pedido_consultar_preco(), LLMResponse(texto="Avisei: SAP indisponível agora.")]
    )
    loop = AgentLoop(stub, registry)
    session = Session(persona)

    resposta = loop.executar_turno(session, "qual o preço do PRD-001?")

    assert resposta == "Avisei: SAP indisponível agora."
    msg_tool = next(m for m in session.historico if m.role is Role.TOOL)
    assert msg_tool.erro is True
    assert "indisponível" in msg_tool.content


# ---------------------------------------------------------------------------
# HTTPSORGateway com transporte fake (sem rede)
# ---------------------------------------------------------------------------


def _http_gateway(token: str | None = "segredo") -> HTTPSORGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        # O Bearer token tem de viajar no header (auth da API intermediária).
        assert request.headers.get("Authorization") == "Bearer segredo"
        path = request.url.path
        if path == "/v1/precos/PRD-001":
            return httpx.Response(
                200,
                json={
                    "codigo_produto": "PRD-001",
                    "preco_unitario": 1250.0,
                    "moeda": "BRL",
                    "disponivel": True,
                },
            )
        if path == "/v1/precos/NADA":
            return httpx.Response(404)
        if path == "/v1/precos/BOOM":
            return httpx.Response(500)
        return httpx.Response(404)

    return HTTPSORGateway(
        "http://sor.local", token=token, transport=httpx.MockTransport(handler)
    )


def test_http_gateway_200_404_500_e_bearer():
    gw = _http_gateway()
    preco = gw.preco("PRD-001")  # 200 → modelo (e o handler valida o Bearer)
    assert preco is not None and preco.preco_unitario == 1250.0
    assert gw.preco("NADA") is None  # 404 → None
    with pytest.raises(SORIndisponivelError, match="500"):
        gw.preco("BOOM")  # 5xx → erro tratável


# ---------------------------------------------------------------------------
# O cético confere a fonte VIVA — os dois ramos do cenário do Bloco 3
# ---------------------------------------------------------------------------


def _engine_com_gateway(gateway: SORGateway) -> MemoryEngine:
    return MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap({"comercial": {"Carlos"}}),
        source_of_truth=GatewaySourceOfTruth(gateway),
    )


def _seed_preco_vigente(engine: MemoryEngine, valor: str = "R$ 1.250,00") -> None:
    engine.observe(
        "produto:PRD-001:preco",
        valor,
        Source(name="consultar_preco", kind=SourceKind.TOOL),
        Justification(why="preço de tabela", verifiable=True),
        domain="comercial",
    )


def test_ceticismo_gateway_confirma_o_novo_supersede_sem_escalar():
    # SAP diz 1180 == afirmação nova → aceita e supersede, sem fila.
    engine = _engine_com_gateway(MockSORGateway(precos={"PRD-001": 1180.0}))
    _seed_preco_vigente(engine)
    ep = engine.observe(
        "produto:PRD-001:preco",
        "R$ 1.180,00",
        Source(name="Carlos", kind=SourceKind.HUMAN),
        Justification(why="cliente alegou novo preço"),
        domain="comercial",
    )
    assert engine.active("produto:PRD-001:preco").value == "R$ 1.180,00"
    assert ep.escalated is False
    assert ep.source_of_truth_consulted is True


def test_ceticismo_gateway_nega_o_novo_mantem_vigente():
    # SAP diz 1250 (≠ afirmação nova 1180) → mantém a vigente e registra a rejeição.
    engine = _engine_com_gateway(MockSORGateway())  # PRD-001 → 1250
    _seed_preco_vigente(engine)
    ep = engine.observe(
        "produto:PRD-001:preco",
        "R$ 1.180,00",
        Source(name="Carlos", kind=SourceKind.HUMAN),
        Justification(why="cliente alegou novo preço"),
        domain="comercial",
    )
    assert engine.active("produto:PRD-001:preco").value == "R$ 1.250,00"
    assert ep.escalated is False
    assert "REJEITOU" in ep.action


def test_ceticismo_chave_fora_dos_padroes_escala():
    # Chave que o gateway não sabe traduzir → found=False → o cético escala.
    engine = _engine_com_gateway(MockSORGateway())
    engine.observe(
        "cliente:CLI-001:nome",
        "ABC Comércio Ltda",
        Source(name="cadastro", kind=SourceKind.DOCUMENT),
        Justification(why="cadastro", verifiable=True),
        domain="comercial",
    )
    ep = engine.observe(
        "cliente:CLI-001:nome",
        "Outra Razão Social",
        Source(name="Carlos", kind=SourceKind.HUMAN),
        Justification(why="cliente mudou de nome", verifiable=True),
        domain="comercial",
    )
    assert ep.escalated is True


def test_normalizacao_reais_do_humano_casa_com_gateway():
    """'R$ 1.180,00' do humano normaliza igual ao valor formatado do gateway."""
    truth = GatewaySourceOfTruth(MockSORGateway(precos={"PRD-001": 1180.0})).lookup(
        "produto:PRD-001:preco"
    )
    assert truth.found is True
    assert normalizar(truth.value) == normalizar("R$ 1.180,00")


# ---------------------------------------------------------------------------
# Lineage sem cópia: consulta_sor no audit
# ---------------------------------------------------------------------------


def test_consulta_sor_aparece_no_audit(tmp_path, persona):
    """Turno com uma tool de SOR grava o evento consulta_sor (decisão ← consulta)."""
    registry = criar_registry_mock(persona.tools)
    registrar_tools_sor(registry, MockSORGateway())
    audit = AuditTrail(tmp_path / "audit" / "sor.jsonl")
    stub = StubProvider(
        roteiro=[_pedido_consultar_preco(), LLMResponse(texto="O preço atual é R$ 1.250,00.")]
    )
    loop = AgentLoop(stub, registry, audit=audit)
    loop.executar_turno(Session(persona), "preço do PRD-001?")

    eventos = [ln for ln in audit.ultimos(50) if ln.get("tipo") == "consulta_sor"]
    assert len(eventos) == 1
    assert eventos[0]["tool"] == "consultar_preco"
    assert eventos[0]["argumentos"] == {"codigo_produto": "PRD-001"}
    assert "1250.0" in eventos[0]["resumo"]


def test_consultar_preco_nao_promove_memoria(persona):
    """RAG... não: dado vivo. consultar_preco executado NÃO vira crença alguma."""
    from cortex.memory import DictSourceOfTruth

    engine = MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap({}),
        source_of_truth=DictSourceOfTruth({}),
    )
    registry = criar_registry_mock(persona.tools)
    registrar_tools_sor(registry, MockSORGateway())
    stub = StubProvider(
        roteiro=[_pedido_consultar_preco(), LLMResponse(texto="É R$ 1.250,00.")]
    )
    loop = AgentLoop(stub, registry, memory=engine)
    loop.executar_turno(Session(persona), "preço do PRD-001?")

    # Nenhuma crença criada — preço é dado vivo (Plano 4), não memória.
    assert engine.store.all_beliefs() == []
