"""Factory do embedder a partir da config (Fase 5a) — padrão dos providers.

A escolha do embedder é decisão de CONFIGURAÇÃO (como o provider de LLM e o
store). Default 'stub' em dev/CI (determinístico, sem rede). 'openai_compat'
reusa o endpoint OpenAI-compat — apontando para Ollama, o embedding é local.
"""

from cortex.config import CortexConfig
from cortex.knowledge.embeddings import (
    MODELO_EMBEDDING_PADRAO,
    EmbeddingProvider,
    OpenAICompatEmbedder,
    StubEmbedder,
)


class ConfiguracaoEmbedderError(Exception):
    """Config de embedder inválida ou dependência ausente."""


def criar_embedder(config: CortexConfig) -> EmbeddingProvider:
    """Instancia o embedder ativo segundo a config."""
    if config.embedding_provider == "stub":
        return StubEmbedder()

    if config.embedding_provider == "openai_compat":
        return OpenAICompatEmbedder(
            modelo=config.embedding_model or MODELO_EMBEDDING_PADRAO,
            api_key=(
                config.openai_api_key.get_secret_value()
                if config.openai_api_key
                else None
            ),
            base_url=config.openai_base_url,
        )

    raise ConfiguracaoEmbedderError(
        f"embedding_provider desconhecido: {config.embedding_provider!r}"
    )
