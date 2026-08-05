"""Montagem do runtime a partir da config (Fase 3c).

Costura, num único ponto, tudo que as fases anteriores já oferecem via
factories: o provider de LLM (Fase 2), o classificador e o store da memória
(3a/3b), o motor de memória, o registry de tools e o loop. Aqui a config
decide provider + classificador + store de forma coerente — e o loop nasce já
conectado à memória.

Mantém o acoplamento na direção certa: o runtime conhece a memória; a memória
não conhece o runtime (o LLMProvider para o LLMClassifier é injetado aqui).

Fase 5b: os símbolos de `cortex.sor` são importados DENTRO das funções de
montagem. `cortex.sor.tools` depende de `cortex.runtime.tools` (ToolError/
ToolRegistry) — importar sor no topo aqui fecharia um ciclo runtime↔sor. O
import tardio mantém o sor como camada inferior, importada só na montagem.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cortex.config import CortexConfig
from cortex.governance.audit import AuditTrail
from cortex.governance.engine import (
    DecisionEngine,
    DecisionMode,
    InvarianteSemLinhagemError,
)
from cortex.governance.example_policy import construir_policy_exemplo
from cortex.identity.models import Persona
from cortex.knowledge.factory import criar_embedder
from cortex.knowledge.index import KnowledgeBase
from cortex.knowledge.tool import ConsultarKBTool
from cortex.memory.engine import MemoryEngine
from cortex.memory.factory import criar_classifier, criar_store
from cortex.memory.seams import AuthorityMap, DictAuthorityMap
from cortex.runtime.loop import AgentLoop
from cortex.runtime.mock_tools import criar_registry_mock
from cortex.runtime.promotion import DOMINIO_PADRAO
from cortex.runtime.providers import criar_provider

if TYPE_CHECKING:
    from cortex.sor.gateway import SORGateway

logger = logging.getLogger("cortex.runtime")


def autoridade_da_persona(persona: Persona) -> AuthorityMap:
    """Deriva o authority map do USER.md: o GESTOR é autoritativo no domínio.

    Doutrina (§3): o USER.md é a base do authority map com nomes concretos.
    Quem MANDA (o gestor do bloco autoridade) tem autoridade plena no domínio
    da persona — sem isso, em runtime nenhum humano seria autoritativo (toda
    correção humana cairia em 0.5) e a correção legítima do chefe escalaria
    para... o próprio chefe.

    Colegas NÃO entram: relacionamento ≠ autoridade (com quem TRABALHA não é
    quem MANDA). SEAM: na Fase 6 isto vira configuração do Data Plane por
    cliente, possivelmente com mais de um domínio e fontes de sistema.
    """
    return DictAuthorityMap({DOMINIO_PADRAO: {persona.user.autoridade.gestor.nome}})


def montar_engine(
    config: CortexConfig,
    provider=None,
    authority_map: AuthorityMap | None = None,
    gateway: SORGateway | None = None,
) -> MemoryEngine:
    """Constrói o MemoryEngine segundo a config (classificador + store).

    `authority_map=None` mantém o mapa vazio (compatibilidade). O runtime
    completo passa o mapa derivado do USER.md (ver montar_runtime). A
    SourceOfTruth agora é VIVA (Fase 5b): o cético confere o system of record
    via GatewaySourceOfTruth — `gateway=None` cria o gateway da config.
    """
    from cortex.sor.factory import criar_gateway
    from cortex.sor.truth import GatewaySourceOfTruth

    if provider is None:
        provider = criar_provider(config)
    if gateway is None:
        gateway = criar_gateway(config)
    classifier = criar_classifier(config, provider)
    store = criar_store(config)
    return MemoryEngine(
        store=store,
        classifier=classifier,
        authority_map=authority_map or DictAuthorityMap({}),
        source_of_truth=GatewaySourceOfTruth(gateway),
    )


def _validar_linhagem_invariantes(policy, persona: Persona) -> None:
    """Todo invariante deve citar um comportamento que EXISTE no SOUL.

    A formação é a fonte; a policy é a materialização. Invariante órfão é erro
    de config — falha alto na montagem (doutrina §6.1).
    """
    ids_soul = {c.id for c in persona.soul.comportamentos}
    for inv in policy.invariantes:
        if inv.soul_behavior_id not in ids_soul:
            raise InvarianteSemLinhagemError(
                f"invariante da tool '{inv.tool}' cita comportamento "
                f"'{inv.soul_behavior_id}' inexistente no SOUL de {persona.soul.nome}"
            )


def montar_decision_engine(
    config: CortexConfig,
    persona: Persona,
    buscar_excecao=None,
    audit=None,
) -> DecisionEngine:
    """Constrói o Decision Engine com a policy de exemplo e o modo da config.

    A policy de exemplo deriva o risco base de cada ToolDeclaration da persona
    e anexa os escaladores + invariantes seed (ver example_policy.py). Valida a
    LINHAGEM dos invariantes ao SOUL. O modo (observe/enforce) vem da config.
    `buscar_excecao` (consumir_excecao da memória) e `audit` são injetados pelo
    runtime — a governança não importa a memória nem o audit diretamente.
    """
    policy = construir_policy_exemplo(persona.tools)
    _validar_linhagem_invariantes(policy, persona)
    return DecisionEngine(
        policy,
        mode=DecisionMode(config.decision_mode),
        buscar_excecao=buscar_excecao,
        audit=audit,
    )


def registrar_kb(registry, config: CortexConfig, persona: Persona) -> None:
    """Registra a tool REAL `consultar_kb` quando a persona a declara.

    Aqui mora a fronteira entre os dois planos da memória: a KB é o Plano 2 (a
    verdade DECLARADA da empresa) e é consultada via RAG — método de ACESSO, não
    de memória. Por isso `consultar_kb` fica de fora dos extratores de promoção
    (ver promotion.py): consultar não memoriza. O embedder é SEAM por config
    (stub no CI; OpenAI-compat/Ollama on-prem — soberania sem trocar código).

    Não indexa aqui (indexar é ato deliberado do curador, `cortex kb indexar`):
    se a KB não estiver indexada, a própria tool degrada para um aviso e a
    persona segue operando — um deploy sem KB curada não pode travar.
    """
    if "consultar_kb" not in persona.tools:
        return
    embedder = criar_embedder(config)
    kb = KnowledgeBase(config.kb_path, embedder)
    registry.registrar("consultar_kb", ConsultarKBTool(kb))


def montar_runtime(
    config: CortexConfig, persona: Persona
) -> tuple[AgentLoop, MemoryEngine]:
    """Monta o loop JÁ conectado à memória e à governança; devolve (loop, engine).

    O mesmo engine serve várias Sessions no processo — é o que faz a persona
    lembrar de uma conversa para outra (e, com store=graphiti, entre
    execuções). O provider é compartilhado entre o loop e o LLMClassifier.

    `provider="claude_code"` (DEV/uso próprio) troca o loop nativo pelo
    executor sobre o Claude Agent SDK — mesma interface de alto nível, mesmas
    costuras (Session, recall, promoção, AuditTrail, governança REUSADA).
    """
    from cortex.runtime.comandos_fila import ContextoTurno, registrar_gerenciar_fila
    from cortex.runtime.extracao_conversa import criar_extrator_conversa
    from cortex.sor.factory import criar_gateway
    from cortex.sor.tools import registrar_tools_sor

    if config.provider == "claude_code":
        return _montar_runtime_claude_code(config, persona)

    provider = criar_provider(config)
    # Um único gateway do SOR serve o cético (GatewaySourceOfTruth) E as tools
    # vivas — a mesma fonte de dado vivo do Data Plane para conferir e consultar.
    gateway = criar_gateway(config)
    engine = montar_engine(
        config, provider, authority_map=autoridade_da_persona(persona), gateway=gateway
    )
    audit = AuditTrail(config.audit_path) if config.audit else None
    decision = montar_decision_engine(
        config, persona, buscar_excecao=engine.consumir_excecao, audit=audit
    )
    registry = criar_registry_mock(persona.tools)
    registrar_kb(registry, config, persona)
    registrar_tools_sor(registry, gateway)

    # Aprendizado conversacional (7b): extrator de fatos da conversa + a tool
    # gerenciar_fila, que decide a Learning Queue com a autoridade do CANAL. O
    # ContextoTurno é o canal pelo qual o loop publica a identidade do turno.
    extrator = (
        criar_extrator_conversa(config, provider)
        if config.aprendizado_conversacional
        else None
    )
    contexto = ContextoTurno()
    registrar_gerenciar_fila(registry, engine, contexto, audit=audit, dominio=DOMINIO_PADRAO)

    loop = AgentLoop(
        provider,
        registry,
        max_iteracoes=config.max_iteracoes,
        memory=engine,
        recall_limite=config.memoria_recall_max,
        decision=decision,
        audit=audit,
        extrator_conversa=extrator,
        contexto_turno=contexto,
    )
    return loop, engine


def _montar_runtime_claude_code(config: CortexConfig, persona: Persona):
    """Composição do executor SDK (provider=claude_code) — DEV/uso próprio.

    Tudo em volta permanece igual ao caminho nativo (engine, governança REUSADA,
    registry, fila, extrator, audit); só o processador de turno muda: o loop do
    agente vive no Claude Agent SDK, cercado pelos hooks de governança.

    Não há LLMProvider neste caminho (o modelo é o do Claude Code logado), então
    os SEAMs que exigem provider (classifier=llm, extrator_conversa=llm) não são
    suportados — falha alto e claro, em vez de degradar em silêncio.
    """
    from cortex.runtime.comandos_fila import ContextoTurno, registrar_gerenciar_fila
    from cortex.runtime.executor_claude_code import ExecutorClaudeCode
    from cortex.runtime.extracao_conversa import criar_extrator_conversa
    from cortex.runtime.providers import ConfiguracaoProviderError
    from cortex.sor.factory import criar_gateway
    from cortex.sor.tools import registrar_tools_sor
    from cortex.sor.truth import GatewaySourceOfTruth

    if config.classifier == "llm" or config.extrator_conversa == "llm":
        raise ConfiguracaoProviderError(
            "provider=claude_code não fornece LLMProvider interno: use "
            "classifier=heuristic e extrator_conversa=heuristico"
        )

    gateway = criar_gateway(config)
    engine = MemoryEngine(
        store=criar_store(config),
        classifier=criar_classifier(config),
        authority_map=autoridade_da_persona(persona),
        source_of_truth=GatewaySourceOfTruth(gateway),
    )
    audit = AuditTrail(config.audit_path) if config.audit else None
    # O DecisionEngine é REUSADO tal e qual, mas SEM audit próprio: quem grava a
    # decisão é o hook PreToolUse, no mesmo formato + executor="claude_code".
    decision = montar_decision_engine(
        config, persona, buscar_excecao=engine.consumir_excecao, audit=None
    )
    registry = criar_registry_mock(persona.tools)
    registrar_kb(registry, config, persona)
    registrar_tools_sor(registry, gateway)

    extrator = (
        criar_extrator_conversa(config, None) if config.aprendizado_conversacional else None
    )
    contexto = ContextoTurno()
    registrar_gerenciar_fila(registry, engine, contexto, audit=audit, dominio=DOMINIO_PADRAO)

    executor = ExecutorClaudeCode(
        registry=registry,
        declaracoes=persona.tools,
        max_iteracoes=config.max_iteracoes,
        memory=engine,
        recall_limite=config.memoria_recall_max,
        decision=decision,
        audit=audit,
        extrator_conversa=extrator,
        contexto_turno=contexto,
        modelo=config.modelo,
    )
    return executor, engine
