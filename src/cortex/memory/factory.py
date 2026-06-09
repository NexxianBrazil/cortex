"""Factory do classificador de memória a partir da config (Fase 3a — Ajuste 3).

Espelha o `criar_provider` da Fase 2: a escolha de qual classificador usar é
decisão de CONFIGURAÇÃO, não de código. O motor recebe um `Classifier` pronto
e nunca pergunta qual é.
"""

from typing import TYPE_CHECKING

from cortex.config import CortexConfig
from cortex.memory.classifier import Classifier, HeuristicClassifier, LLMClassifier
from cortex.memory.store import InMemoryStore, MemoryStore

if TYPE_CHECKING:
    from cortex.runtime.providers.base import LLMProvider


class ConfiguracaoClassifierError(Exception):
    """Config de classificador inválida ou ainda não suportada nesta fase."""


class ConfiguracaoStoreError(Exception):
    """Config de persistência inválida ou dependência ausente."""


def criar_classifier(
    config: CortexConfig, provider: "LLMProvider | None" = None
) -> Classifier:
    """Instancia o classificador ativo segundo a config — heurística é o default.

    'llm' exige um LLMProvider já construído (injetado pelo wiring do runtime,
    para a memória não importar o runtime). Sem provider, falha alto e claro.
    """
    if config.classifier == "heuristic":
        return HeuristicClassifier()

    if config.classifier == "llm":
        if provider is None:
            raise ConfiguracaoClassifierError(
                "classifier=llm exige um LLMProvider (injetado pelo runtime). "
                "Construa o provider e passe-o a criar_classifier."
            )
        return LLMClassifier(provider)

    raise ConfiguracaoClassifierError(f"classifier desconhecido: {config.classifier!r}")


def criar_store(config: CortexConfig) -> MemoryStore:
    """Instancia a persistência ativa segundo a config — store é trocável.

    Default em dev/CI é o InMemoryStore (rápido, sem dependências). 'graphiti'
    pluga o Kuzu embarcado; se o pacote opcional não estiver instalado, falha
    alto e claro (como o provider factory faz quando falta chave).
    """
    if config.store == "memory":
        return InMemoryStore()

    if config.store == "graphiti":
        try:
            # Import preguiçoso: a stack do Graphiti/Kuzu é opcional (extra
            # 'graphiti'); só é exigida quando o store é realmente o graphiti.
            from cortex.memory.graphiti_store import GraphitiStore
        except ImportError as exc:
            raise ConfiguracaoStoreError(
                "store=graphiti exige o extra opcional: instale com "
                "`uv pip install -e \".[graphiti]\"` (graphiti-core[kuzu])."
            ) from exc
        return GraphitiStore(config.kuzu_db_path)

    raise ConfiguracaoStoreError(f"store desconhecido: {config.store!r}")
