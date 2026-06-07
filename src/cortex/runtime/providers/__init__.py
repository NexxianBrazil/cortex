"""Provedores de LLM e a factory que os instancia a partir da config.

A factory é o ÚNICO lugar que conhece os três provedores ao mesmo tempo —
o loop recebe um LLMProvider pronto e nunca pergunta qual é.
"""

from cortex.config import CortexConfig
from cortex.runtime.messages import LLMResponse, ToolCall
from cortex.runtime.providers.base import LLMProvider
from cortex.runtime.providers.claude import MODELO_PADRAO as MODELO_PADRAO_CLAUDE
from cortex.runtime.providers.claude import ClaudeProvider
from cortex.runtime.providers.openai_compat import MODELO_PADRAO as MODELO_PADRAO_OPENAI
from cortex.runtime.providers.openai_compat import OpenAICompatProvider
from cortex.runtime.providers.stub import StubProvider


class ConfiguracaoProviderError(Exception):
    """Config de provedor inválida (ex.: falta a chave de API no ambiente)."""


def _roteiro_demo() -> list[LLMResponse]:
    """Roteiro do stub para o CLI: demonstra o circuito completo do loop.

    No primeiro turno o stub pede consultar_preco (exercitando tool mockada e
    volta do resultado); depois responde em texto para sempre.
    """
    return [
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="demo-1",
                    nome="consultar_preco",
                    argumentos={"codigo_produto": "PRD-001", "quantidade": 10},
                )
            ]
        ),
        LLMResponse(
            texto=(
                "(stub) Consultei o preço do PRD-001 com a tool mockada — o circuito "
                "completo do loop funcionou. Configure provider=claude ou "
                "provider=openai no config.toml para falar com um LLM real."
            )
        ),
    ]


def criar_provider(config: CortexConfig) -> LLMProvider:
    """Instancia o provedor ativo segundo a config — o loop não muda nunca."""
    if config.provider == "stub":
        return StubProvider(roteiro=_roteiro_demo(), repetir_ultimo=True)

    if config.provider == "claude":
        if config.anthropic_api_key is None:
            raise ConfiguracaoProviderError(
                "provider=claude exige ANTHROPIC_API_KEY no ambiente ou no .env "
                "(nunca em arquivo versionado)."
            )
        return ClaudeProvider(
            modelo=config.modelo or MODELO_PADRAO_CLAUDE,
            api_key=config.anthropic_api_key.get_secret_value(),
        )

    if config.provider == "openai":
        usa_endpoint_oficial = config.openai_base_url.startswith("https://api.openai.com")
        if config.openai_api_key is None and usa_endpoint_oficial:
            raise ConfiguracaoProviderError(
                "provider=openai com o endpoint oficial exige OPENAI_API_KEY no "
                "ambiente ou no .env. Para Ollama/vLLM/LLM interna, defina "
                "OPENAI_BASE_URL (a chave passa a ser opcional)."
            )
        return OpenAICompatProvider(
            modelo=config.modelo or MODELO_PADRAO_OPENAI,
            api_key=(
                config.openai_api_key.get_secret_value() if config.openai_api_key else None
            ),
            base_url=config.openai_base_url,
        )

    raise ConfiguracaoProviderError(f"provider desconhecido: {config.provider!r}")


__all__ = [
    "ClaudeProvider",
    "ConfiguracaoProviderError",
    "LLMProvider",
    "OpenAICompatProvider",
    "StubProvider",
    "criar_provider",
]
