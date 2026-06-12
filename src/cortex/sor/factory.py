"""Factory do gateway do SOR a partir da config (Fase 5b) — padrão dos providers.

A escolha do gateway é decisão de CONFIGURAÇÃO (como o provider de LLM, o store
e o embedder). Default 'mock' em dev/CI (determinístico, sem rede). 'http' fala
com a API intermediária real — trocar não exige mudança de código.
"""

from cortex.config import CortexConfig
from cortex.sor.gateway import HTTPSORGateway, MockSORGateway, SORGateway


class ConfiguracaoGatewayError(Exception):
    """Config de gateway inválida (ex.: provider 'http' sem base_url)."""


def criar_gateway(config: CortexConfig) -> SORGateway:
    """Instancia o gateway ativo segundo a config."""
    if config.sor_provider == "mock":
        return MockSORGateway()

    if config.sor_provider == "http":
        if not config.sor_base_url:
            raise ConfiguracaoGatewayError(
                "sor_provider='http' exige sor_base_url (a URL da API intermediária)"
            )
        return HTTPSORGateway(
            base_url=config.sor_base_url,
            token=config.sor_token.get_secret_value() if config.sor_token else None,
        )

    raise ConfiguracaoGatewayError(f"sor_provider desconhecido: {config.sor_provider!r}")
