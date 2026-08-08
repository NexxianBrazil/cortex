"""Testes do provider `claude_code` (executor sobre o Claude Agent SDK).

Mesmo padrão do graphiti: o CI NÃO tem o SDK instalado, então tudo que depende
dele se pula (importorskip) — e as partes PURAS (geração das tools MCP a
partir do registro, o hook PreToolUse como função, o evento de auditoria e o
erro de montagem sem o extra) rodam sempre, sem SDK.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from cortex.config import CortexConfig
from cortex.governance.audit import AuditTrail
from cortex.governance.engine import DecisionEngine, DecisionMode, Verdict
from cortex.governance.example_policy import construir_policy_exemplo
from cortex.identity import carregar_persona
from cortex.risk import RiskLevel
from cortex.runtime import montar_runtime
from cortex.runtime.executor_claude_code import (
    BUILTINS_NEGADAS,
    EXECUTOR,
    MENSAGEM_EXTRA_AUSENTE,
    PREFIXO_MCP,
    RAZAO_FALHA_GOVERNANCA,
    ClaudeCodeIndisponivelError,
    EstadoTurno,
    allowed_tools_do_registro,
    avaliar_pre_tool_use,
    criar_hook_post,
    criar_hook_pre,
    nome_do_registro,
    nome_mcp,
    registrar_post_tool_use,
    schema_json_da_tool,
)

PERSONAS_DIR = Path(__file__).parent.parent / "personas"


@pytest.fixture()
def persona():
    return carregar_persona(PERSONAS_DIR)


@pytest.fixture()
def engine_enforce(persona):
    """DecisionEngine REAL (policy de exemplo) em enforce — MEDIUM+ bloqueia."""
    policy = construir_policy_exemplo(persona.tools)
    return DecisionEngine(policy, mode=DecisionMode.ENFORCE)


def _tool_por_risco(persona, minimo: RiskLevel, maximo: RiskLevel | None = None) -> str:
    for decl in persona.tools.values():
        if decl.risco_base.ordem >= minimo.ordem and (
            maximo is None or decl.risco_base.ordem <= maximo.ordem
        ):
            return decl.nome
    pytest.skip(f"persona de exemplo sem tool com risco >= {minimo.value}")


def _argumentos_minimos(persona, nome: str) -> dict:
    decl = persona.tools[nome]
    exemplo: dict = {
        "string": "x", "integer": 1, "number": 1.0, "boolean": True, "array": [], "object": {}
    }
    return {p.nome: exemplo.get(p.tipo, "x") for p in decl.parametros if p.obrigatorio}


# ---------------------------------------------------------------------------
# 1. Geração dinâmica das tools MCP a partir do REGISTRO (sem SDK)
# ---------------------------------------------------------------------------


def test_allowed_tools_sao_1_para_1_com_o_registro(persona):
    allowed = allowed_tools_do_registro(persona.tools)
    assert allowed == [f"{PREFIXO_MCP}{nome}" for nome in persona.tools]
    # nomes redondos: prefixo aplicado e removível sem perda
    for nome in persona.tools:
        assert nome_do_registro(nome_mcp(nome)) == nome


def test_nenhuma_builtin_em_allowed_tools(persona):
    allowed = set(allowed_tools_do_registro(persona.tools))
    assert allowed.isdisjoint(set(BUILTINS_NEGADAS))
    # e as builtins conhecidas do Claude Code estão TODAS na lista de negação
    for builtin in ("Bash", "Read", "Write", "Edit", "WebSearch", "WebFetch"):
        assert builtin in BUILTINS_NEGADAS


def test_schema_json_espelha_a_declaracao(persona):
    nome = next(n for n, d in persona.tools.items() if d.parametros)
    decl = persona.tools[nome]
    schema = schema_json_da_tool(decl)
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {p.nome for p in decl.parametros}
    assert set(schema["required"]) == {p.nome for p in decl.parametros if p.obrigatorio}
    for p in decl.parametros:
        assert schema["properties"][p.nome]["description"] == p.descricao


# ---------------------------------------------------------------------------
# 2. Hook PreToolUse como função pura
# ---------------------------------------------------------------------------


def test_hook_permite_chamada_de_risco_baixo(persona, engine_enforce):
    nome = _tool_por_risco(persona, RiskLevel.LOW, RiskLevel.LOW)
    evento = {
        "tool_name": nome_mcp(nome),
        "tool_input": _argumentos_minimos(persona, nome),
    }
    saida = avaliar_pre_tool_use(evento, engine_enforce)
    assert saida == {}


def test_hook_nega_chamada_bloqueada_com_razao(persona, engine_enforce):
    nome = _tool_por_risco(persona, RiskLevel.MEDIUM)
    estado = EstadoTurno()
    evento = {
        "tool_name": nome_mcp(nome),
        "tool_input": _argumentos_minimos(persona, nome),
    }
    saida = avaliar_pre_tool_use(evento, engine_enforce, estado=estado)
    hso = saida["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"]  # a razão volta ao modelo
    assert "governança" in hso["permissionDecisionReason"]
    assert estado.bloqueios == 1
    # o MESMO Decision Engine registrou o veredito (reusado, não adaptado)
    assert engine_enforce.decisoes[-1].verdict is Verdict.PRECISA_APROVACAO


def test_hook_nega_builtin_fora_do_catalogo(engine_enforce):
    """Deny-by-default absoluto: tool sem o prefixo MCP do Cortex é negada."""
    saida = avaliar_pre_tool_use(
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}, engine_enforce
    )
    assert saida["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# 2b. FAIL-CLOSED: exceção na governança NEGA a ação (nunca fail-open)
# ---------------------------------------------------------------------------


class _EngineQueExplode:
    """DecisionEngine cujo avaliar levanta — simula governança quebrada."""

    mode = DecisionMode.ENFORCE

    def avaliar(self, tool, argumentos):
        raise RuntimeError("boom na governança")


def test_hook_pre_e_fail_closed_quando_a_avaliacao_explode(tmp_path):
    estado = EstadoTurno()
    audit = AuditTrail(tmp_path / "audit.jsonl")
    hook = criar_hook_pre(_EngineQueExplode(), audit=audit, estado=estado)

    saida = asyncio.run(hook({"tool_name": nome_mcp("x"), "tool_input": {"a": 1}}, None, None))

    hso = saida["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"] == RAZAO_FALHA_GOVERNANCA
    assert estado.bloqueios == 1

    linhas = [json.loads(li) for li in (tmp_path / "audit.jsonl").read_text().splitlines()]
    ev = next(li for li in linhas if li["tipo"] == "decisao_tool")
    assert ev["verdict"] == "erro_governanca"
    assert ev["executou"] is False
    assert ev["executor"] == "claude_code"
    assert "boom" in ev["motivos"][0]


def test_hook_pre_nega_mesmo_com_audit_quebrado():
    """Audit indisponível não pode reabrir a porta: a negação se mantém."""

    class _AuditQueExplode:
        def registrar(self, *a, **kw):
            raise OSError("disco cheio")

    hook = criar_hook_pre(_EngineQueExplode(), audit=_AuditQueExplode())
    saida = asyncio.run(hook({"tool_name": nome_mcp("x"), "tool_input": {}}, None, None))
    assert saida["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_post_nao_derruba_o_turno_em_falha():
    class _AuditQueExplode:
        def registrar(self, *a, **kw):
            raise OSError("disco cheio")

    hook = criar_hook_post(audit=_AuditQueExplode())
    saida = asyncio.run(hook({"tool_name": nome_mcp("x"), "tool_response": {}}, "id1", None))
    assert saida == {}


# ---------------------------------------------------------------------------
# 3. Evento de auditoria com executor="claude_code"
# ---------------------------------------------------------------------------


def test_hook_audita_decisao_com_marca_do_executor(tmp_path, persona, engine_enforce):
    audit = AuditTrail(tmp_path / "audit.jsonl")
    nome = _tool_por_risco(persona, RiskLevel.MEDIUM)
    evento = {
        "tool_name": nome_mcp(nome),
        "tool_input": _argumentos_minimos(persona, nome),
    }
    avaliar_pre_tool_use(evento, engine_enforce, audit=audit)

    linhas = [json.loads(li) for li in (tmp_path / "audit.jsonl").read_text().splitlines()]
    decisoes = [li for li in linhas if li["tipo"] == "decisao_tool"]
    assert len(decisoes) == 1
    ev = decisoes[0]
    # mesmo formato do loop nativo + a marca do executor
    assert ev["executor"] == EXECUTOR == "claude_code"
    assert ev["tool"] == nome
    assert ev["verdict"] == "precisa_aprovacao"
    assert ev["executou"] is False
    assert ev["motivos"]


def test_post_tool_use_audita_resultado_correlacionado(tmp_path, persona):
    audit = AuditTrail(tmp_path / "audit.jsonl")
    nome = next(iter(persona.tools))
    registrar_post_tool_use(
        {"tool_name": nome_mcp(nome), "tool_input": {}, "tool_response": {"ok": True}},
        "toolu_123",
        audit=audit,
        declaracoes=persona.tools,
    )
    linhas = [json.loads(li) for li in (tmp_path / "audit.jsonl").read_text().splitlines()]
    ev = next(li for li in linhas if li["tipo"] == "tool_result")
    assert ev["executor"] == "claude_code"
    assert ev["tool_use_id"] == "toolu_123"
    assert ev["tool"] == nome


# ---------------------------------------------------------------------------
# 4. Montagem sem o extra instalado → erro claro, não traceback cru
# ---------------------------------------------------------------------------


def test_montar_runtime_sem_extra_da_erro_claro(monkeypatch, persona):
    # simula a ausência do pacote mesmo se ele estiver instalado na máquina
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    config = CortexConfig(provider="claude_code", audit=False)
    with pytest.raises(ClaudeCodeIndisponivelError) as exc:
        montar_runtime(config, persona)
    assert "pip install -e .[claudecode]" in str(exc.value)
    assert str(exc.value) == MENSAGEM_EXTRA_AUSENTE


def test_montar_runtime_claude_code_rejeita_seams_que_exigem_provider(persona):
    from cortex.runtime.providers import ConfiguracaoProviderError

    config = CortexConfig(provider="claude_code", classifier="llm", audit=False)
    with pytest.raises(ConfiguracaoProviderError):
        montar_runtime(config, persona)


# ---------------------------------------------------------------------------
# 5. Paridade com o loop nativo — exige o SDK real + Claude Code logado
# ---------------------------------------------------------------------------


def test_paridade_turno_simples_com_loop_nativo(tmp_path, persona, monkeypatch):
    """Mesmo turno simples → resposta em texto e trilha com os mesmos campos.

    Live de verdade (SDK + assinatura logada): roda só com o extra instalado E
    CORTEX_TEST_CLAUDE_CODE=1 no ambiente — nunca no CI.
    """
    pytest.importorskip("claude_agent_sdk")
    import os

    if not os.environ.get("CORTEX_TEST_CLAUDE_CODE"):
        pytest.skip("CORTEX_TEST_CLAUDE_CODE não definido — teste live pulado")

    from cortex.runtime import Session

    config = CortexConfig(
        provider="claude_code", audit=True, audit_path=tmp_path / "audit.jsonl"
    )
    executor, _engine = montar_runtime(config, persona)
    session = Session(persona)
    resposta = executor.executar_turno(session, "Responda apenas 'ok'.")
    assert isinstance(resposta, str) and resposta.strip()

    linhas = [json.loads(li) for li in (tmp_path / "audit.jsonl").read_text().splitlines()]
    turno = next(li for li in linhas if li["tipo"] == "turno")
    # mesmos campos do resumo de turno do loop nativo + a marca do executor
    for campo in (
        "tools",
        "input_tokens",
        "output_tokens",
        "houve_bloqueio",
        "identidade",
        "procedencia",
    ):
        assert campo in turno
    assert turno["executor"] == "claude_code"
    # efemeridade: sessão nova não carrega histórico da anterior
    assert Session(persona).historico == []
