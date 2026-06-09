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
from cortex.identity.models import Persona
from cortex.memory.engine import MemoryEngine
from cortex.memory.factory import criar_classifier, criar_store
from cortex.memory.seams import DictAuthorityMap, DictSourceOfTruth
from cortex.runtime.loop import AgentLoop
from cortex.runtime.mock_tools import criar_registry_mock
from cortex.runtime.providers import criar_provider

logger = logging.getLogger("cortex.runtime")


def montar_engine(config: CortexConfig, provider=None) -> MemoryEngine:
    """Constrói o MemoryEngine segundo a config (classificador + store).

    AuthorityMap e SourceOfTruth entram VAZIOS nesta fase — são SEAMs do Data
    Plane (Fase 6) e do system of record / SAP (Fase 5). Sem eles, fontes de
    tool ainda têm autoridade de sistema (0.9), suficiente para a 3c.
    """
    if provider is None:
        provider = criar_provider(config)
    classifier = criar_classifier(config, provider)
    store = criar_store(config)
    return MemoryEngine(
        store=store,
        classifier=classifier,
        authority_map=DictAuthorityMap({}),
        source_of_truth=DictSourceOfTruth({}),
    )


def montar_runtime(
    config: CortexConfig, persona: Persona
) -> tuple[AgentLoop, MemoryEngine]:
    """Monta o loop JÁ conectado à memória e devolve (loop, engine).

    O mesmo engine serve várias Sessions no processo — é o que faz a persona
    lembrar de uma conversa para outra (e, com store=graphiti, entre
    execuções). O provider é compartilhado entre o loop e o LLMClassifier.
    """
    provider = criar_provider(config)
    engine = montar_engine(config, provider)
    registry = criar_registry_mock(persona.tools)
    loop = AgentLoop(
        provider,
        registry,
        max_iteracoes=config.max_iteracoes,
        memory=engine,
        recall_limite=config.memoria_recall_max,
    )
    return loop, engine
