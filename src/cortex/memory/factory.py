"""Factory do classificador de memória a partir da config (Fase 3a — Ajuste 3).

Espelha o `criar_provider` da Fase 2: a escolha de qual classificador usar é
decisão de CONFIGURAÇÃO, não de código. O motor recebe um `Classifier` pronto
e nunca pergunta qual é.
"""

from cortex.config import CortexConfig
from cortex.memory.classifier import Classifier, HeuristicClassifier
from cortex.memory.store import InMemoryStore, MemoryStore


class ConfiguracaoClassifierError(Exception):
    """Config de classificador inválida ou ainda não suportada nesta fase."""


class ConfiguracaoStoreError(Exception):
    """Config de persistência inválida ou dependência ausente."""


def criar_classifier(config: CortexConfig) -> Classifier:
    """Instancia o classificador ativo segundo a config."""
    if config.classifier == "heuristic":
        return HeuristicClassifier()

    if config.classifier == "llm":
        # SEAM: a classificação por LLM depende da memória estar conectada ao
        # runtime (Fase 3c), para reusar o LLMProvider da Fase 2. Até lá,
        # falhamos alto e claro — como o provider factory faz quando falta chave.
        raise ConfiguracaoClassifierError(
            "classifier=llm ainda não é suportado: o LLMClassifier é um SEAM "
            "que será ligado na Fase 3c (memória + runtime). Use 'heuristic'."
        )

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
