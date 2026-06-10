"""Testes do runtime (Fase 2) — SEM rede, SEM chave: só StubProvider.

O stub roteirizado permite testar o circuito completo do loop de forma
determinística: pedido de tool → execução mockada → resultado de volta ao
LLM → resposta final em texto.
"""

from pathlib import Path

import pytest

from cortex.identity import carregar_persona
from cortex.runtime import (
    AgentLoop,
    ArgumentosInvalidosError,
    LLMResponse,
    LoopLimiteExcedidoError,
    Role,
    Session,
    StubProvider,
    ToolCall,
    ToolNaoEncontradaError,
    criar_registry_mock,
    montar_system_prompt,
)

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"


@pytest.fixture(scope="module")
def persona():
    return carregar_persona(PERSONAS_DIR)


@pytest.fixture()
def registry(persona):
    return criar_registry_mock(persona.tools)


def _pedido_consultar_preco(id_: str = "tc-1") -> LLMResponse:
    return LLMResponse(
        tool_calls=[
            ToolCall(id=id_, nome="consultar_preco", argumentos={"codigo_produto": "PRD-001"})
        ]
    )


# ---------------------------------------------------------------------------
# O loop completo fecha o circuito
# ---------------------------------------------------------------------------


def test_loop_completo_com_tool(persona, registry):
    """Stub pede tool → loop executa o mock → resultado volta → stub responde."""
    stub = StubProvider(
        roteiro=[
            _pedido_consultar_preco(),
            LLMResponse(texto="O preço do PRD-001 é R$ 1.250,00."),
        ]
    )
    loop = AgentLoop(stub, registry)
    session = Session(persona)

    resposta = loop.executar_turno(session, "Qual o preço do PRD-001?")

    assert resposta == "O preço do PRD-001 é R$ 1.250,00."
    # O histórico registra o circuito inteiro, na ordem.
    roles = [m.role for m in session.historico]
    assert roles == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    # O resultado da tool mockada chegou de fato ao histórico...
    msg_tool = session.historico[2]
    assert msg_tool.nome_tool == "consultar_preco"
    assert "1250.0" in msg_tool.content
    assert msg_tool.erro is False
    # ...e foi enviado de volta ao LLM na segunda chamada (circuito fechado).
    assert len(stub.chamadas) == 2
    _, mensagens_2a_chamada, _ = stub.chamadas[1]
    assert any(m.role is Role.TOOL and "1250.0" in m.content for m in mensagens_2a_chamada)


def test_loop_passa_system_prompt_e_tools(persona, registry):
    """O contexto (SOUL + playbook) e as tools declaradas chegam ao provedor."""
    stub = StubProvider(roteiro=[LLMResponse(texto="ok")])
    loop = AgentLoop(stub, registry)
    loop.executar_turno(Session(persona), "olá")

    system, _, tools = stub.chamadas[0]
    assert "Mariana" in system  # SOUL na persona
    assert "emitir_cotacao" in system  # playbook relevante incluído
    assert "conferir_antes_de_enviar" in system  # comportamento sob risco
    assert {t.nome for t in tools} == set(persona.tools)


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


def test_teto_de_iteracoes(persona, registry):
    """Stub que pede tool para sempre → loop para no teto com erro claro."""
    stub = StubProvider(roteiro=[_pedido_consultar_preco()], repetir_ultimo=True)
    loop = AgentLoop(stub, registry, max_iteracoes=3)

    with pytest.raises(LoopLimiteExcedidoError, match="3 iterações"):
        loop.executar_turno(Session(persona), "loop infinito")
    # Provou que parou exatamente no teto, não antes nem depois.
    assert len(stub.chamadas) == 3


def test_tool_inexistente_nao_derruba_o_loop(persona, registry):
    """LLM pede tool fantasma → erro tratável volta ao LLM, loop continua."""
    stub = StubProvider(
        roteiro=[
            LLMResponse(tool_calls=[ToolCall(id="tc-x", nome="tool_fantasma", argumentos={})]),
            LLMResponse(texto="Desculpe, não tenho essa capacidade."),
        ]
    )
    loop = AgentLoop(stub, registry)
    session = Session(persona)

    resposta = loop.executar_turno(session, "use a tool fantasma")

    assert resposta == "Desculpe, não tenho essa capacidade."
    msg_tool = session.historico[2]
    assert msg_tool.erro is True
    assert "tool_fantasma" in msg_tool.content
    assert msg_tool.content.startswith("ERRO:")


def test_argumentos_invalidos_viram_erro_tratavel(persona, registry):
    """Argumento obrigatório faltando → erro volta ao LLM em vez de exceção."""
    stub = StubProvider(
        roteiro=[
            LLMResponse(
                tool_calls=[ToolCall(id="tc-y", nome="consultar_preco", argumentos={})]
            ),
            LLMResponse(texto="Preciso do código do produto."),
        ]
    )
    loop = AgentLoop(stub, registry)
    session = Session(persona)

    resposta = loop.executar_turno(session, "consulte o preço")

    assert resposta == "Preciso do código do produto."
    assert session.historico[2].erro is True
    assert "codigo_produto" in session.historico[2].content


# ---------------------------------------------------------------------------
# Sessão efêmera
# ---------------------------------------------------------------------------


def test_sessao_esquece(persona, registry):
    """Nada persiste entre sessões: a segunda nasce sem nenhum resquício."""
    stub = StubProvider(roteiro=[LLMResponse(texto="anotado")], repetir_ultimo=True)
    loop = AgentLoop(stub, registry)

    sessao_1 = Session(persona)
    loop.executar_turno(sessao_1, "lembre que meu nome é Carlos")
    assert len(sessao_1.historico) == 2

    sessao_2 = Session(persona)
    assert sessao_2.historico == []  # efêmero por design: morreu, esqueceu
    assert sessao_2.historico is not sessao_1.historico


# ---------------------------------------------------------------------------
# Registry de tools (unidade)
# ---------------------------------------------------------------------------


def test_registry_executa_mock_deterministico(registry):
    resultado = registry.executar("consultar_preco", {"codigo_produto": "PRD-001"})
    assert resultado["preco_unitario"] == 1250.00
    assert resultado["moeda"] == "BRL"
    # Determinístico: a mesma chamada devolve sempre o mesmo resultado.
    assert registry.executar("consultar_preco", {"codigo_produto": "PRD-001"}) == resultado


def test_registry_tool_inexistente(registry):
    with pytest.raises(ToolNaoEncontradaError, match="tool_fantasma"):
        registry.executar("tool_fantasma", {})


def test_registry_argumento_desconhecido(registry):
    with pytest.raises(ArgumentosInvalidosError, match="argumento_surpresa"):
        registry.executar(
            "consultar_preco",
            {"codigo_produto": "PRD-001", "argumento_surpresa": 1},
        )


def test_registry_nao_registra_tool_fora_do_catalogo(persona):
    registry = criar_registry_mock(persona.tools)
    with pytest.raises(ValueError, match="não está declarada"):
        registry.registrar("tool_nao_declarada", lambda: {})


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def test_system_prompt_cobre_a_formacao_completa(persona):
    system = montar_system_prompt(persona)
    assert "Mariana" in system
    assert "Carlos Menezes" in system  # autoridade
    assert "Paula Andrade" in system  # relacionamento
    assert "Playbook: emitir_cotacao" in system
    assert "escalar_quando_incerto" in system


# ---------------------------------------------------------------------------
# Fase 4c — loop em enforce: proposta de ação vs proibido por formação
# ---------------------------------------------------------------------------


def _engine_memoria_com_gestor(persona):
    from cortex.memory import (
        DictAuthorityMap,
        DictSourceOfTruth,
        HeuristicClassifier,
        InMemoryStore,
        MemoryEngine,
    )

    return MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap({"governanca": {"Carlos Menezes"}}),
        source_of_truth=DictSourceOfTruth({}),
    )


def test_loop_enforce_bloqueio_cria_proposta_e_cita_id(persona, registry):
    """Tool bloqueada em enforce → mensagem ao LLM contém o id da proposta criada."""
    from cortex.governance import DecisionEngine, DecisionMode, construir_policy_exemplo

    engine_mem = _engine_memoria_com_gestor(persona)
    decision = DecisionEngine(
        construir_policy_exemplo(persona.tools), mode=DecisionMode.ENFORCE
    )
    stub = StubProvider(
        roteiro=[
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="e1",
                        nome="enviar_email",
                        argumentos={
                            "destinatario": "cliente@gmail.com",
                            "assunto": "x",
                            "corpo": "y",
                        },
                    )
                ]
            ),
            LLMResponse(texto="avisei o usuário"),
        ]
    )
    loop = AgentLoop(stub, registry, memory=engine_mem, decision=decision)
    loop.executar_turno(Session(persona), "manda e-mail pro cliente")

    # A proposta de ação foi criada na fila...
    propostas = engine_mem.store.proposals()
    assert len(propostas) == 1 and propostas[0].key == "acao:enviar_email"
    # ...e a mensagem ao LLM cita o id dela.
    pid = propostas[0].id
    tool_msgs = [c for c in stub.chamadas[-1][1] if c.role is Role.TOOL]
    assert any(f"#{pid}" in m.content and "aguarda aprovação" in m.content for m in tool_msgs)


def test_loop_enforce_invariante_recusa_sem_proposta(persona, registry):
    """Invariante de formação → mensagem de FORMAÇÃO ao LLM, SEM criar proposta."""
    from cortex.governance import DecisionEngine, DecisionMode, construir_policy_exemplo

    engine_mem = _engine_memoria_com_gestor(persona)
    decision = DecisionEngine(
        construir_policy_exemplo(persona.tools), mode=DecisionMode.ENFORCE
    )
    stub = StubProvider(
        roteiro=[
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="e1",
                        nome="enviar_email",
                        argumentos={
                            "destinatario": "colega@nexxian.com",
                            "assunto": "desconto",
                            "corpo": "fechado 20%",
                            "promete_condicao": True,
                            "cotacao_emitida": False,
                        },
                    )
                ]
            ),
            LLMResponse(texto="recusei educadamente"),
        ]
    )
    loop = AgentLoop(stub, registry, memory=engine_mem, decision=decision)
    loop.executar_turno(Session(persona), "promete 20% pro cliente por e-mail")

    # Nenhuma proposta foi criada (formação não é aprovável pela fila).
    assert engine_mem.store.proposals() == []
    tool_msgs = [c for c in stub.chamadas[-1][1] if c.role is Role.TOOL]
    assert any("RECUSADA POR FORMAÇÃO" in m.content for m in tool_msgs)
    assert any("nao_prometer_sem_lastro" in m.content for m in tool_msgs)


# ---------------------------------------------------------------------------
# Fase 4c — AuditTrail
# ---------------------------------------------------------------------------


def test_audit_trail_grava_jsonl_valido(tmp_path):
    import json

    from cortex.governance import AuditTrail

    at = AuditTrail(tmp_path / "audit" / "d.jsonl")
    at.registrar("decisao_tool", tool="enviar_email", risco="high", verdict="precisa_aprovacao")
    at.registrar("llm_request", iteracao=1, input_tokens=10, output_tokens=3)
    at.registrar("turno", iteracoes=2, tools=["enviar_email"], input_tokens=10, output_tokens=3)

    linhas = (tmp_path / "audit" / "d.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 3
    objs = [json.loads(ln) for ln in linhas]
    assert {o["tipo"] for o in objs} == {"decisao_tool", "llm_request", "turno"}
    assert all("ts" in o for o in objs)  # timestamp em toda linha


def test_audit_trail_falha_de_escrita_nao_derruba(tmp_path):
    from cortex.governance import AuditTrail

    # path impossível (um arquivo no lugar do diretório) → escrita falha.
    arquivo = tmp_path / "bloqueio"
    arquivo.write_text("x")
    at = AuditTrail(arquivo / "sub" / "d.jsonl")
    # Não deve levantar — só loga warning.
    at.registrar("decisao_tool", tool="x")
    assert at.ultimos(5) == []
