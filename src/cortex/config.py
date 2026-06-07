"""Configuração do Cortex (Fase 2).

Princípio: o LOOP nunca sabe qual provedor de LLM está por baixo — trocar de
Stub para Claude (ou para uma LLM interna via protocolo OpenAI) é só mudar a
configuração. A configuração é lida de duas fontes, nesta ordem de
precedência:

1. variáveis de ambiente (e o arquivo .env local, NUNCA versionado);
2. o arquivo config.toml (versionável: provider ativo, modelo, limites).

As CHAVES de API vêm SEMPRE de variável de ambiente / .env — nunca de
arquivo versionado. Por isso são `SecretStr`: não vazam em logs nem em repr.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class CortexConfig(BaseSettings):
    """Configuração completa do runtime.

    Campos próprios do Cortex usam o prefixo CORTEX_ no ambiente
    (ex.: CORTEX_PROVIDER=claude); chaves de API usam os nomes padrão de
    mercado (ANTHROPIC_API_KEY, OPENAI_API_KEY) via alias explícito.
    """

    model_config = SettingsConfigDict(
        env_prefix="CORTEX_",
        env_file=".env",
        env_file_encoding="utf-8",
        toml_file="config.toml",
        extra="ignore",
    )

    # --- escolha do provedor (trocar aqui NÃO muda o loop) ---
    provider: Literal["stub", "claude", "openai"] = "stub"
    modelo: str | None = Field(
        default=None,
        description="Modelo do provedor ativo; None usa o padrão do provedor",
    )

    # --- guardrails do loop ---
    max_iteracoes: int = Field(
        default=10, ge=1, description="Teto de voltas do loop por turno (guardrail de custo)"
    )

    # --- identidade ---
    personas_dir: Path = Field(
        default=Path("personas"), description="Pasta com a formação da persona"
    )

    # --- credenciais (SEMPRE de env/.env, nunca de arquivo versionado) ---
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias="ANTHROPIC_API_KEY"
    )
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias="OPENAI_BASE_URL",
        description="Permite apontar para Ollama/vLLM/LLM interna sem mudar código",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Adiciona o config.toml como fonte, abaixo de env/.env na precedência."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
