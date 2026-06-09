"""Testes da Decision Engine (Fase 4a) — risk-by-scope, policy-as-data, dois modos.

Determinísticos, no CI: avaliam a policy de exemplo e a integração com o loop
via StubProvider. Nada de rede.
"""

from pathlib import Path

import pytest

from cortex.governance import (
    Condition,
    DecisionEngine,
    DecisionMode,
    Operador,
    RiskEscalator,
    RiskPolicy,
    ToolRiskPolicy,
    Verdict,
    construir_policy_exemplo,
)
from cortex.identity import carregar_persona
from cortex.risk import RiskLevel
from cortex.runtime import (
    AgentLoop,
    LLMResponse,
    Session,
    StubProvider,
    ToolCall,
    criar_registry_mock,
)

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"

EMAIL_INTERNO = "colega@nexxian.com"
EMAIL_EXTERNO = "cliente@gmail.com"


@pytest.fixture(scope="module")
def persona():
    return carregar_persona(PERSONAS_DIR)


@pytest.fixture()
def policy(persona) -> RiskPolicy:
    return construir_policy_exemplo(persona.tools)


def _email_args(destinatario: str, anexos: list | None = None) -> dict:
    args = {"destinatario": destinatario, "assunto": "oi", "corpo": "texto"}
    if anexos is not None:
        args["anexos"] = anexos
    return args


# ---------------------------------------------------------------------------
# Risk-by-scope: a MESMA tool, args diferentes, riscos diferentes
# ---------------------------------------------------------------------------


def test_risk_by_scope_mesma_tool_riscos_diferentes(policy):
    """enviar_email interno=LOW vs externo=HIGH — avalia por escopo, não por tipo."""
    engine = DecisionEngine(policy)  # observe (default)

    interno = engine.avaliar("enviar_email", _email_args(EMAIL_INTERNO))
    externo = engine.avaliar("enviar_email", _email_args(EMAIL_EXTERNO))

    assert interno.risco is RiskLevel.LOW
    assert externo.risco is RiskLevel.HIGH
    # É a mesma tool — só os argumentos mudaram.
    assert interno.tool == externo.tool == "enviar_email"


def test_escalador_composto_externo_com_anexo_e_critical(policy):
    """Externo + anexo (dado de cliente saindo) → CRITICAL (AND de condições)."""
    engine = DecisionEngine(policy)
    d = engine.avaliar("enviar_email", _email_args(EMAIL_EXTERNO, anexos=["cotacao.pdf"]))
    assert d.risco is RiskLevel.CRITICAL
    assert any("anexo" in m for m in d.motivos)


def test_consultar_preco_sempre_low(policy):
    """Tool somente-leitura: sem escaladores, sempre o risco base (LOW)."""
    engine = DecisionEngine(policy)
    d = engine.avaliar("consultar_preco", {"codigo_produto": "PRD-001"})
    assert d.risco is RiskLevel.LOW
    assert d.bloqueavel is False


def test_escalador_de_valor_acima_do_teto(policy):
    """emitir_cotacao: base MEDIUM; acima do teto → CRITICAL (predicado numérico)."""
    engine = DecisionEngine(policy)
    base = {"cliente_id": "ABC", "itens": [], "condicao_pagamento": "30 dias"}

    dentro = engine.avaliar("emitir_cotacao", {**base, "valor_total": 10_000})
    acima = engine.avaliar("emitir_cotacao", {**base, "valor_total": 60_000})

    assert dentro.risco is RiskLevel.MEDIUM  # só o base
    assert acima.risco is RiskLevel.CRITICAL  # escalador do teto disparou
    assert any("teto" in m for m in acima.motivos)


# ---------------------------------------------------------------------------
# Policy-as-data: adicionar regra é editar dados, não o engine
# ---------------------------------------------------------------------------


def test_policy_as_data_regra_nova_sem_tocar_engine():
    """Uma policy montada à mão (dados) é avaliada pelo MESMO engine genérico."""
    policy = RiskPolicy(
        tools={
            "tool_x": ToolRiskPolicy(
                risco_base=RiskLevel.LOW,
                escaladores=[
                    RiskEscalator(
                        condicoes=[Condition(param="qtd", op=Operador.GT, value=100)],
                        eleva_para=RiskLevel.HIGH,
                        motivo="quantidade alta",
                    )
                ],
            )
        }
    )
    engine = DecisionEngine(policy)
    assert engine.avaliar("tool_x", {"qtd": 10}).risco is RiskLevel.LOW
    assert engine.avaliar("tool_x", {"qtd": 500}).risco is RiskLevel.HIGH


def test_tool_sem_policy_e_cautelosa(policy):
    """Deny-by-default: tool sem policy declarada é tratada como HIGH."""
    engine = DecisionEngine(policy)
    d = engine.avaliar("tool_desconhecida", {})
    assert d.risco is RiskLevel.HIGH
    assert any("sem policy" in m for m in d.motivos)


# ---------------------------------------------------------------------------
# Dois modos: observe (default) vs enforce
# ---------------------------------------------------------------------------


def test_modo_observe_permite_mas_marca_bloqueavel(policy):
    """Observe: risco alto AINDA é permitido, mas marcado como bloqueável."""
    engine = DecisionEngine(policy, mode=DecisionMode.OBSERVE)
    d = engine.avaliar("enviar_email", _email_args(EMAIL_EXTERNO))
    assert d.risco is RiskLevel.HIGH
    assert d.verdict is Verdict.PERMITIDO
    assert d.executou is True
    assert d.bloqueavel is True  # seria barrada em enforce


def test_modo_enforce_barra_medium_ou_mais(policy):
    """Enforce: MEDIUM+ não executa, vira pedido de aprovação; LOW passa."""
    engine = DecisionEngine(policy, mode=DecisionMode.ENFORCE)

    alto = engine.avaliar("enviar_email", _email_args(EMAIL_EXTERNO))
    assert alto.verdict is Verdict.PRECISA_APROVACAO
    assert alto.executou is False

    baixo = engine.avaliar("enviar_email", _email_args(EMAIL_INTERNO))
    assert baixo.verdict is Verdict.PERMITIDO
    assert baixo.executou is True


def test_trilha_de_auditoria_registra_toda_decisao(policy):
    """Toda decisão é registrada (semente do Audit Engine da 4c)."""
    engine = DecisionEngine(policy)
    engine.avaliar("consultar_preco", {"codigo_produto": "X"})
    engine.avaliar("enviar_email", _email_args(EMAIL_EXTERNO))
    assert len(engine.decisoes) == 2
    assert [d.tool for d in engine.decisoes] == ["consultar_preco", "enviar_email"]


# ---------------------------------------------------------------------------
# Integração com o loop nos dois modos
# ---------------------------------------------------------------------------


def _stub_envia_email_externo() -> StubProvider:
    return StubProvider(
        roteiro=[
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="e1",
                        nome="enviar_email",
                        argumentos=_email_args(EMAIL_EXTERNO),
                    )
                ]
            ),
            LLMResponse(texto="pronto"),
        ]
    )


def test_loop_observe_executa_mas_loga_veredito(persona):
    """Modo observe: a tool de risco alto AINDA executa; o veredito fica registrado."""
    registry = criar_registry_mock(persona.tools)
    policy = construir_policy_exemplo(persona.tools)
    decision = DecisionEngine(policy, mode=DecisionMode.OBSERVE)
    loop = AgentLoop(_stub_envia_email_externo(), registry, decision=decision)

    s = Session(persona)
    loop.executar_turno(s, "manda um e-mail pro cliente")

    # A tool executou: há um TOOL com o resultado do envio (não bloqueado).
    msg_tool = [m for m in s.historico if m.role.value == "tool"][0]
    assert msg_tool.erro is False
    assert "enviado" in msg_tool.content
    # Mas o veredito diz que seria barrada em enforce.
    d = decision.decisoes[-1]
    assert d.risco is RiskLevel.HIGH
    assert d.executou is True
    assert d.bloqueavel is True


def test_loop_enforce_barra_e_devolve_resultado_tratavel(persona):
    """Modo enforce: a mesma tool NÃO executa; o loop recebe 'precisa aprovação'."""
    registry = criar_registry_mock(persona.tools)
    policy = construir_policy_exemplo(persona.tools)
    decision = DecisionEngine(policy, mode=DecisionMode.ENFORCE)
    loop = AgentLoop(_stub_envia_email_externo(), registry, decision=decision)

    s = Session(persona)
    resposta = loop.executar_turno(s, "manda um e-mail pro cliente")

    # O loop não caiu — devolveu a resposta final do LLM.
    assert resposta == "pronto"
    # A tool foi bloqueada: o TOOL é a mensagem de governança, não o envio.
    msg_tool = [m for m in s.historico if m.role.value == "tool"][0]
    assert msg_tool.erro is True
    assert "AÇÃO BLOQUEADA" in msg_tool.content
    assert "aprovação" in msg_tool.content
    assert "enviado" not in msg_tool.content  # NÃO enviou de verdade


def test_loop_sem_decision_engine_se_comporta_como_fase2(persona):
    """Sem DecisionEngine, o loop é idêntico à Fase 2 (nada de governança)."""
    registry = criar_registry_mock(persona.tools)
    loop = AgentLoop(_stub_envia_email_externo(), registry)  # decision=None

    s = Session(persona)
    resposta = loop.executar_turno(s, "manda um e-mail")
    assert resposta == "pronto"
    msg_tool = [m for m in s.historico if m.role.value == "tool"][0]
    assert msg_tool.erro is False
    assert "enviado" in msg_tool.content
