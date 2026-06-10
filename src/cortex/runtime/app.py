"""Montagem do runtime a partir da config (Fase 3c).

Costura, num único ponto, tudo que as fases anteriores já oferecem via
factories: o provider de LLM (Fase 2), o classificador e o store da memória
(3a/3b), o motor de memória, o registry de tools e o loop. Aqui a config
decide provider + classificador + store de forma coerente — e o loop nasce já
conectado à memória.

Mantém o acoplamento na direção certa: o runtime conhece a memória; a memória
não conhece o runtime (o LLMProvider para o LLMClassifier é injetado aqui).
"""

import logging

from cortex.config import CortexConfig
from cortex.governance.audit import AuditTrail
from cortex.governance.engine import (
    DecisionEngine,
    DecisionMode,
    InvarianteSemLinhagemError,
)
from cortex.governance.example_policy import construir_policy_exemplo
from cortex.identity.models import Persona
from cortex.memory.engine import MemoryEngine
from cortex.memory.factory import criar_classifier, criar_store
from cortex.memory.seams import AuthorityMap, DictAuthorityMap, DictSourceOfTruth
from cortex.runtime.loop import AgentLoop
from cortex.runtime.mock_tools import criar_registry_mock
from cortex.runtime.promotion import DOMINIO_PADRAO
from cortex.runtime.providers import criar_provider

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
) -> MemoryEngine:
    """Constrói o MemoryEngine segundo a config (classificador + store).

    `authority_map=None` mantém o mapa vazio (compatibilidade). O runtime
    completo passa o mapa derivado do USER.md (ver montar_runtime). A
    SourceOfTruth entra VAZIA — SEAM do system of record / SAP (Fase 5).
    """
    if provider is None:
        provider = criar_provider(config)
    classifier = criar_classifier(config, provider)
    store = criar_store(config)
    return MemoryEngine(
        store=store,
        classifier=classifier,
        authority_map=authority_map or DictAuthorityMap({}),
        source_of_truth=DictSourceOfTruth({}),
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


def montar_runtime(
    config: CortexConfig, persona: Persona
) -> tuple[AgentLoop, MemoryEngine]:
    """Monta o loop JÁ conectado à memória e à governança; devolve (loop, engine).

    O mesmo engine serve várias Sessions no processo — é o que faz a persona
    lembrar de uma conversa para outra (e, com store=graphiti, entre
    execuções). O provider é compartilhado entre o loop e o LLMClassifier.
    """
    provider = criar_provider(config)
    engine = montar_engine(config, provider, authority_map=autoridade_da_persona(persona))
    audit = AuditTrail(config.audit_path) if config.audit else None
    decision = montar_decision_engine(
        config, persona, buscar_excecao=engine.consumir_excecao, audit=audit
    )
    registry = criar_registry_mock(persona.tools)
    loop = AgentLoop(
        provider,
        registry,
        max_iteracoes=config.max_iteracoes,
        memory=engine,
        recall_limite=config.memoria_recall_max,
        decision=decision,
        audit=audit,
    )
    return loop, engine
