"""Executor alternativo sobre o Claude Agent SDK — provider `claude_code` (DEV).

DECISÃO DE ARQUITETURA (fechada): neste caminho o loop do agente vive no SDK,
não no nosso loop.py — por isso `claude_code` NÃO implementa a ABC LLMProvider
(que é request/response). O ExecutorClaudeCode espelha a interface de alto
nível que o runtime usa (`executar_turno(session, entrada) -> str`) e alimenta
as MESMAS costuras do loop nativo: Session efêmera, recall, promoção de fim de
turno, AuditTrail e governança.

É um caminho de DESENVOLVIMENTO/uso próprio, autenticado pela assinatura do
Claude Code logado na máquina — produção segue no provider `claude` (API).

GOVERNANÇA (o ponto central): o Decision Engine é REUSADO, não adaptado. Cada
tool call do SDK passa pelo hook PreToolUse, que traduz o evento para a
chamada concreta, roda o MESMO `DecisionEngine.avaliar` e, quando o veredito
não executa, devolve `permissionDecision="deny"` com a razão — que volta ao
modelo para reformular ou escalar, como no loop nativo. Deny-by-default
absoluto: só as tools MCP do Cortex são permitidas; toda builtin do Claude
Code (Bash, Read, Write...) fica fora de allowed_tools E em disallowed_tools —
o Cortex não toca filesystem/rede por fora da governança.

O import do SDK é SEMPRE lazy: o módulo importa sem o extra instalado (os
testes de CI exercitam as partes puras); só o construtor exige o pacote.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import weakref
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from cortex.governance.engine import DecisionEngine, Verdict
from cortex.identity.models import ToolDeclaration
from cortex.memory.learning import propor_acao
from cortex.runtime.identidade import demarcar_entrada
from cortex.runtime.loop import montar_system_prompt
from cortex.runtime.messages import Message, Role, ToolCall
from cortex.runtime.promotion import DOMINIO_PADRAO, promover_fim_de_turno
from cortex.runtime.recall import formatar_beliefs, recuperar_beliefs
from cortex.runtime.tools import ToolError, ToolRegistry

if TYPE_CHECKING:
    from cortex.runtime.session import Session

logger = logging.getLogger("cortex.runtime")

# Nome do servidor MCP in-process e o prefixo que o SDK dá às tools dele.
SERVIDOR_MCP = "cortex"
PREFIXO_MCP = f"mcp__{SERVIDOR_MCP}__"

EXECUTOR = "claude_code"

MENSAGEM_EXTRA_AUSENTE = (
    "provider=claude_code exige `pip install -e .[claudecode]` e o Claude Code "
    "logado nesta máquina"
)

# Builtins do Claude Code que NUNCA podem estar disponíveis neste executor —
# cinto e suspensório: além de fora de allowed_tools, entram em
# disallowed_tools. O Cortex só age através das tools MCP governadas.
BUILTINS_NEGADAS = (
    "Bash",
    "BashOutput",
    "Edit",
    "ExitPlanMode",
    "Glob",
    "Grep",
    "KillShell",
    "MultiEdit",
    "NotebookEdit",
    "Read",
    "Task",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    "Write",
)

# Mesmo mapa tipo declarado → JSON Schema do provider claude (paridade de fio).
_TIPOS_JSON = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


class ClaudeCodeIndisponivelError(RuntimeError):
    """O extra `claudecode` não está instalado (ou o SDK não importa)."""


def _importar_sdk():
    """Import lazy do SDK com erro CLARO quando o extra não está instalado."""
    try:
        import claude_agent_sdk
    except ImportError as exc:
        raise ClaudeCodeIndisponivelError(MENSAGEM_EXTRA_AUSENTE) from exc
    return claude_agent_sdk


# ---------------------------------------------------------------------------
# Partes PURAS (sem SDK) — testáveis no CI sem o extra instalado.
# ---------------------------------------------------------------------------


def nome_mcp(nome: str) -> str:
    """Nome da tool do registro no formato exposto pelo SDK (mcp__cortex__X)."""
    return f"{PREFIXO_MCP}{nome}"


def nome_do_registro(tool_name: str) -> str:
    """Inverso de `nome_mcp`: tira o prefixo MCP para achar a tool no registro."""
    return tool_name.removeprefix(PREFIXO_MCP)


def schema_json_da_tool(decl: ToolDeclaration) -> dict:
    """ToolDeclaration (Fase 1) → JSON Schema da tool MCP (mesmo fio do claude)."""
    propriedades = {
        p.nome: {"type": _TIPOS_JSON.get(p.tipo, "string"), "description": p.descricao}
        for p in decl.parametros
    }
    obrigatorios = [p.nome for p in decl.parametros if p.obrigatorio]
    return {"type": "object", "properties": propriedades, "required": obrigatorios}


def allowed_tools_do_registro(declaracoes: Mapping[str, ToolDeclaration]) -> list[str]:
    """SOMENTE as tools MCP do Cortex — nenhuma builtin entra aqui, nunca."""
    return [nome_mcp(nome) for nome in declaracoes]


class EstadoTurno:
    """Contador partilhado com os hooks (turnos são serializados por sessão)."""

    def __init__(self) -> None:
        self.bloqueios = 0

    def zerar(self) -> None:
        self.bloqueios = 0


def _mensagem_bloqueio(decisao, nome: str, argumentos: dict, memory, persona_nome: str) -> str:
    """Razão devolvida ao modelo quando a governança não executa a chamada.

    Espelha (com o mesmo texto) o comportamento do loop nativo — duplicado aqui
    de propósito: a restrição da tarefa é NÃO tocar no loop nativo.
    """
    if decisao.verdict is Verdict.PROIBIDO_FORMACAO:
        return (
            "AÇÃO RECUSADA POR FORMAÇÃO — esta chamada viola um comportamento "
            f"inegociável da sua formação [{decisao.soul_behavior_id}]: "
            f"{'; '.join(decisao.motivos)}. Isto NÃO é aprovável pela fila de "
            "aprovação (formação só muda via Nexxian). Recuse educadamente ao "
            "usuário, explicando o porquê com base no seu caráter."
        )

    if memory is not None:
        proposta = propor_acao(
            tool=nome,
            argumentos=argumentos,
            risco=decisao.risco,
            motivos=decisao.motivos,
            autor_pedido=persona_nome,
            domain=DOMINIO_PADRAO,
        )
        memory.store.add_proposal(proposta)
        logger.warning(
            "tool=%s BLOQUEADA (hook claude_code) — proposta de ação #%d criada",
            nome,
            proposta.id,
        )
        return (
            f"AÇÃO BLOQUEADA pela governança (risco {decisao.risco.value}): "
            f"{'; '.join(decisao.motivos)}. Proposta #{proposta.id} criada na "
            "fila de aprovação — informe o usuário que a ação aguarda aprovação "
            "humana."
        )

    return (
        f"AÇÃO BLOQUEADA pela governança — precisa de aprovação "
        f"(risco {decisao.risco.value}): {'; '.join(decisao.motivos)}."
    )


def _deny(razao: str) -> dict:
    """Formato de negação do hook PreToolUse do Agent SDK."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": razao,
        }
    }


def avaliar_pre_tool_use(
    evento: dict,
    decision: DecisionEngine,
    audit=None,
    memory=None,
    persona_nome: str = "",
    estado: EstadoTurno | None = None,
) -> dict:
    """Coração do hook PreToolUse — função PURA e síncrona (testável sem SDK).

    Traduz o evento do SDK (`tool_name`/`tool_input`) para a chamada concreta,
    roda o MESMO Decision Engine do loop nativo e devolve `{}` (permitido —
    observa) ou a negação com a razão. Toda decisão vai ao AuditTrail no mesmo
    formato de evento do loop nativo, marcada com `executor="claude_code"`.
    """
    tool_name = str(evento.get("tool_name", ""))
    argumentos = dict(evento.get("tool_input") or {})

    # Deny-by-default ABSOLUTO: qualquer tool fora do servidor MCP do Cortex
    # (uma builtin que escapou da configuração, por exemplo) é negada aqui —
    # terceira camada além de allowed_tools/disallowed_tools.
    if not tool_name.startswith(PREFIXO_MCP):
        razao = (
            f"tool '{tool_name}' fora do catálogo governado do Cortex — apenas "
            "as tools MCP do Cortex são permitidas neste executor"
        )
        if estado is not None:
            estado.bloqueios += 1
        if audit is not None:
            audit.registrar(
                "decisao_tool",
                executor=EXECUTOR,
                tool=tool_name,
                risco=None,
                modo=decision.mode.value,
                verdict="fora_do_catalogo",
                executou=False,
                bloqueavel=True,
                soul_behavior_id=None,
                motivos=[razao],
                argumentos=argumentos,
            )
        logger.warning("hook claude_code negou tool fora do catálogo: %s", tool_name)
        return _deny(razao)

    nome = nome_do_registro(tool_name)
    decisao = decision.avaliar(nome, argumentos)

    # Mesmo formato de evento do loop nativo (decisao_tool), com a marca do
    # executor. O DecisionEngine deste caminho é montado SEM audit próprio —
    # o registro acontece aqui para carregar o campo `executor`.
    if audit is not None:
        audit.registrar(
            "decisao_tool",
            executor=EXECUTOR,
            tool=decisao.tool,
            risco=decisao.risco.value,
            modo=decisao.modo.value,
            verdict=decisao.verdict.value,
            executou=decisao.executou,
            bloqueavel=decisao.bloqueavel,
            soul_behavior_id=decisao.soul_behavior_id,
            motivos=decisao.motivos,
            argumentos=decisao.argumentos,
        )

    if decisao.executou:
        return {}

    if estado is not None:
        estado.bloqueios += 1
    return _deny(_mensagem_bloqueio(decisao, nome, argumentos, memory, persona_nome))


def registrar_post_tool_use(
    evento: dict,
    tool_use_id: str | None,
    audit=None,
    declaracoes: Mapping[str, ToolDeclaration] | None = None,
) -> dict:
    """Coração do hook PostToolUse — registra o resultado no AuditTrail.

    Correlaciona pelo tool_use_id. Para tools de system of record, também
    registra o evento `consulta_sor` (lineage sem cópia), como o loop nativo.
    """
    if audit is None:
        return {}
    tool_name = str(evento.get("tool_name", ""))
    nome = nome_do_registro(tool_name)
    argumentos = dict(evento.get("tool_input") or {})
    resumo = json.dumps(evento.get("tool_response"), ensure_ascii=False, default=str)
    if len(resumo) > 200:
        resumo = resumo[:197] + "..."
    audit.registrar(
        "tool_result",
        executor=EXECUTOR,
        tool=nome,
        tool_use_id=tool_use_id,
        resumo=resumo,
    )
    decl = (declaracoes or {}).get(nome)
    if decl is not None and decl.system_of_record:
        audit.registrar(
            "consulta_sor", tool=nome, argumentos=argumentos, resumo=resumo, executor=EXECUTOR
        )
    return {}


# ---------------------------------------------------------------------------
# Partes que EXIGEM o SDK (import lazy dentro das funções/da classe).
# ---------------------------------------------------------------------------


def gerar_tools_mcp(
    declaracoes: Mapping[str, ToolDeclaration], registry: ToolRegistry
) -> list:
    """Gera as tools MCP in-process a partir do REGISTRO — zero duplicação.

    Cada ToolDeclaration vira `@tool(nome, descricao, schema)` cuja função
    chama `registry.executar` — a MESMA implementação que o loop nativo
    executa. ToolError (tratável) vira resultado de erro para o modelo se
    corrigir, exatamente como no loop nativo.
    """
    sdk = _importar_sdk()
    tools = []
    for decl in declaracoes.values():

        def _criar(decl: ToolDeclaration):
            @sdk.tool(decl.nome, decl.descricao, schema_json_da_tool(decl))
            async def _impl(args: dict) -> dict:
                try:
                    resultado = registry.executar(decl.nome, dict(args))
                    conteudo = json.dumps(resultado, ensure_ascii=False)
                    return {"content": [{"type": "text", "text": conteudo}]}
                except ToolError as exc:
                    return {
                        "content": [{"type": "text", "text": f"ERRO: {exc}"}],
                        "is_error": True,
                    }

            return _impl

        tools.append(_criar(decl))
    return tools


class _LoopDedicado:
    """Event loop asyncio numa thread dedicada — o SDK client vive TODO nela.

    `executar_turno` é síncrono (mesma interface do loop nativo) e pode ser
    chamado de threads diferentes (CLI, server); todas as corrotinas do SDK
    rodam neste único loop, que é o requisito do client assíncrono.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="cortex-claude-code", daemon=True
        )
        self._thread.start()

    def rodar(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def rodar_sem_esperar(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)


class ExecutorClaudeCode:
    """Turnos de conversa com o loop do agente DENTRO do Claude Agent SDK.

    Espelha `AgentLoop.executar_turno(session, entrada) -> str`. Uma sessão
    SDK (ClaudeSDKClient) por Session do Cortex: criada no primeiro turno,
    fechada quando a Session morre (finalizer) — efemeridade preservada, sem
    resume/persistência do SDK.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        declaracoes: Mapping[str, ToolDeclaration],
        max_iteracoes: int = 10,
        memory=None,
        recall_limite: int = 5,
        decision: DecisionEngine | None = None,
        audit=None,
        extrator_conversa=None,
        contexto_turno=None,
        modelo: str | None = None,
    ) -> None:
        if max_iteracoes < 1:
            raise ValueError("max_iteracoes deve ser >= 1")
        self._sdk = _importar_sdk()  # falha CLARO na montagem, não no 1º turno
        self._registry = registry
        self._declaracoes = dict(declaracoes)
        self._max_iteracoes = max_iteracoes
        self._memory = memory
        self._recall_limite = recall_limite
        self._decision = decision
        self._audit = audit
        self._extrator_conversa = extrator_conversa
        self._contexto_turno = contexto_turno
        self._modelo = modelo
        self._loop = _LoopDedicado()
        # Session -> (client, estado). Weak: a Session morrer é o fim da
        # sessão SDK (finalizer desconecta) — nada persiste entre sessões.
        self._clients: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    # -- sessão SDK ---------------------------------------------------------

    def _client_da_sessao(self, session: Session, system: str):
        par = self._clients.get(session)
        if par is not None:
            return par

        sdk = self._sdk
        estado = EstadoTurno()
        persona_nome = session.persona.soul.nome
        decision = self._decision
        audit = self._audit
        memory = self._memory
        declaracoes = self._declaracoes

        async def _pre(input_data: dict, tool_use_id: str | None, context) -> dict:
            if decision is None:
                return {}
            return avaliar_pre_tool_use(
                input_data,
                decision,
                audit=audit,
                memory=memory,
                persona_nome=persona_nome,
                estado=estado,
            )

        async def _post(input_data: dict, tool_use_id: str | None, context) -> dict:
            return registrar_post_tool_use(
                input_data, tool_use_id, audit=audit, declaracoes=declaracoes
            )

        servidor = sdk.create_sdk_mcp_server(
            name=SERVIDOR_MCP,
            version="1.0.0",
            tools=gerar_tools_mcp(self._declaracoes, self._registry),
        )
        opcoes = sdk.ClaudeAgentOptions(
            system_prompt=system,
            mcp_servers={SERVIDOR_MCP: servidor},
            # Deny-by-default: SÓ as tools MCP do Cortex; builtins negadas
            # explicitamente (cinto e suspensório) e barradas de novo no hook.
            allowed_tools=allowed_tools_do_registro(self._declaracoes),
            disallowed_tools=list(BUILTINS_NEGADAS),
            hooks={
                "PreToolUse": [sdk.HookMatcher(matcher=None, hooks=[_pre])],
                "PostToolUse": [sdk.HookMatcher(matcher=None, hooks=[_post])],
            },
            # Teto de iterações do loop nativo vira teto de turnos do SDK.
            max_turns=self._max_iteracoes,
            # O comportamento vem do CORTEX: nunca carregar settings do
            # projeto/usuário (.claude/settings.json) da máquina.
            setting_sources=[],
            model=self._modelo,
        )
        client = sdk.ClaudeSDKClient(options=opcoes)
        self._loop.rodar(client.connect())

        # Fim da Session do Cortex = fim da sessão SDK (efemeridade). O
        # finalizer NÃO referencia a session (senão ela nunca morre).
        loop = self._loop

        def _fechar(client=client) -> None:
            try:
                loop.rodar_sem_esperar(client.disconnect())
            except RuntimeError:  # event loop já encerrado no shutdown
                pass

        weakref.finalize(session, _fechar)

        par = (client, estado)
        self._clients[session] = par
        return par

    # -- turno ----------------------------------------------------------------

    def executar_turno(self, session: Session, entrada_usuario: str) -> str:
        """Roda um turno completo via SDK e devolve a resposta final em texto.

        Mesmas costuras do loop nativo: demarcação de entrada (7a), recall de
        beliefs (3c), histórico interno alimentado com a trilha de tool calls,
        promoção de fim de turno e resumo no AuditTrail.
        """
        if self._contexto_turno is not None:
            self._contexto_turno.identidade = session.identidade

        inicio_turno = len(session.historico)
        conteudo_usuario = demarcar_entrada(entrada_usuario, session.identidade)
        session.historico.append(Message(role=Role.USER, content=conteudo_usuario))

        beliefs = (
            recuperar_beliefs(self._memory, entrada_usuario, self._recall_limite)
            if self._memory is not None
            else []
        )

        primeiro_turno = session not in self._clients
        system = montar_system_prompt(session.persona, beliefs if primeiro_turno else [])
        client, estado = self._client_da_sessao(session, system)
        estado.zerar()

        # O system prompt do client é fixo na sessão SDK; nos turnos seguintes
        # o recall entra como bloco de contexto ANTES da entrada demarcada.
        prompt = conteudo_usuario
        if beliefs and not primeiro_turno:
            prompt = f"{formatar_beliefs(beliefs)}\n\n{conteudo_usuario}"

        mensagens = self._loop.rodar(self._conversar(client, prompt))
        texto, tokens_in, tokens_out, tools_chamadas = self._processar_mensagens(
            session, mensagens
        )

        if tokens_in or tokens_out:
            self._audit_registrar(
                "llm_request",
                executor=EXECUTOR,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
            )

        session.historico.append(Message(role=Role.ASSISTANT, content=texto))
        logger.info("turno (claude_code) concluído: %d tool call(s)", len(tools_chamadas))

        if self._memory is not None:
            promover_fim_de_turno(
                self._memory,
                session.historico[inicio_turno:],
                extrator_conversa=self._extrator_conversa,
                identidade=session.identidade,
                audit=self._audit,
            )

        ident = session.identidade
        self._audit_registrar(
            "turno",
            executor=EXECUTOR,
            iteracoes=None,
            tools=tools_chamadas,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            houve_bloqueio=estado.bloqueios > 0,
            identidade=ident.nome if ident else None,
            procedencia=ident.procedencia.value if ident else None,
        )
        return texto

    async def _conversar(self, client, prompt: str) -> list:
        await client.query(prompt)
        mensagens = []
        async for mensagem in client.receive_response():
            mensagens.append(mensagem)
        return mensagens

    def _processar_mensagens(
        self, session: Session, mensagens: list
    ) -> tuple[str, int, int, list[str]]:
        """Traduz as mensagens do SDK para o histórico interno (formato do loop).

        A trilha alimenta a MESMA extração de fim de turno do loop nativo:
        pedidos de tool viram Message(ASSISTANT, tool_calls=...); resultados
        viram Message(TOOL, nome_tool=..., tool_call_id=...).
        """
        sdk = self._sdk
        texto_final = ""
        tokens_in = tokens_out = 0
        tools_chamadas: list[str] = []
        nomes_por_id: dict[str, str] = {}

        for m in mensagens:
            if isinstance(m, sdk.AssistantMessage):
                texto = "".join(
                    b.text for b in m.content if isinstance(b, sdk.TextBlock)
                )
                pedidos: list[ToolCall] = []
                for b in m.content:
                    if isinstance(b, sdk.ToolUseBlock):
                        nome = nome_do_registro(b.name)
                        nomes_por_id[b.id] = nome
                        tools_chamadas.append(nome)
                        pedidos.append(
                            ToolCall(id=b.id, nome=nome, argumentos=dict(b.input or {}))
                        )
                if pedidos:
                    session.historico.append(
                        Message(role=Role.ASSISTANT, content=texto, tool_calls=pedidos)
                    )
                elif texto:
                    texto_final = texto
            elif isinstance(m, sdk.UserMessage) and isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, sdk.ToolResultBlock):
                        session.historico.append(
                            Message(
                                role=Role.TOOL,
                                content=_conteudo_tool_result(b.content),
                                tool_call_id=b.tool_use_id,
                                nome_tool=nomes_por_id.get(b.tool_use_id),
                                erro=bool(b.is_error),
                            )
                        )
            elif isinstance(m, sdk.ResultMessage):
                uso = m.usage or {}
                tokens_in += int(uso.get("input_tokens") or 0)
                tokens_out += int(uso.get("output_tokens") or 0)
                if not texto_final and m.result:
                    texto_final = m.result
        return texto_final, tokens_in, tokens_out, tools_chamadas

    def _audit_registrar(self, tipo: str, **campos: Any) -> None:
        if self._audit is not None:
            self._audit.registrar(tipo, **campos)


def _conteudo_tool_result(conteudo) -> str:
    """Normaliza o content de um ToolResultBlock (str | lista de blocos) p/ str."""
    if conteudo is None:
        return ""
    if isinstance(conteudo, str):
        return conteudo
    partes = []
    for bloco in conteudo:
        if isinstance(bloco, dict) and bloco.get("type") == "text":
            partes.append(str(bloco.get("text", "")))
        else:
            partes.append(json.dumps(bloco, ensure_ascii=False, default=str))
    return "\n".join(partes)
