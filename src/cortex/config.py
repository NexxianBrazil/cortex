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

import tomllib
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

    # --- escolha do classificador de memória (mesma ideia do provider) ---
    classifier: Literal["heuristic", "llm"] = Field(
        default="heuristic",
        description="Classificador de relação da memória; 'llm' é SEAM (Fase 3c)",
    )

    # --- escolha da persistência da memória (Fase 3b) ---
    store: Literal["memory", "graphiti"] = Field(
        default="memory",
        description="Persistência da memória; 'memory' (default dev/CI) ou 'graphiti' (Kuzu)",
    )
    kuzu_db_path: Path = Field(
        default=Path("cortex_memory.kuzu"),
        description="Arquivo do banco Kuzu embarcado (usado quando store='graphiti')",
    )
    memoria_recall_max: int = Field(
        default=5,
        ge=0,
        description="Máximo de beliefs injetados no contexto por turno (recuperação enxuta)",
    )

    # --- governança (Fase 4) ---
    decision_mode: Literal["observe", "enforce"] = Field(
        default="observe",
        description="Decision Engine: 'observe' (dry-run, default) ou 'enforce' (barra MEDIUM+)",
    )
    audit: bool = Field(
        default=True, description="Liga a trilha de auditoria (Audit Engine, Fase 4c)"
    )
    audit_path: Path = Field(
        default=Path("audit/decisions.jsonl"),
        description="Arquivo JSONL append-only da trilha de auditoria (Data Plane local)",
    )

    # --- knowledge base / RAG (Fase 5a) ---
    kb_path: Path = Field(
        default=Path("kb"),
        description="Diretório da Knowledge Base (.md com frontmatter curado)",
    )
    embedding_provider: Literal["stub", "openai_compat"] = Field(
        default="stub",
        description="Embedder: 'stub' (CI, feature hashing) ou 'openai_compat' (OpenAI/Ollama)",
    )
    embedding_model: str | None = Field(
        default=None,
        description="Modelo de embedding (ex.: nomic-embed-text no Ollama); None usa o padrão",
    )

    # --- system of record / gateway (Fase 5b) ---
    sor_provider: Literal["mock", "http"] = Field(
        default="mock",
        description="Gateway do system of record: 'mock' (CI/dev) ou 'http' (API intermediária)",
    )
    sor_base_url: str | None = Field(
        default=None,
        description="URL base da API intermediária do SOR (obrigatório quando sor_provider='http')",
    )
    sor_token: SecretStr | None = Field(
        default=None,
        description="Token Bearer da API do SOR (de env/.env, nunca versionado)",
    )

    # --- aprendizado conversacional (Fase 7b) ---
    aprendizado_conversacional: bool = Field(
        default=True,
        description="Liga a extração de fatos da conversa no fim do turno (passa pelo observe())",
    )
    extrator_conversa: Literal["heuristico", "llm"] = Field(
        default="heuristico",
        description="Extrator de fatos: 'heuristico' (CI, sem rede) ou 'llm' (exige provider real)",
    )

    # --- servidor HTTP (Fase 7a) ---
    server_token: SecretStr | None = Field(
        default=None,
        description=(
            "Token do bridge (header X-Cortex-Token); autentica o TRANSPORTE, não o "
            "remetente (a identidade do remetente vem do canal/canais.yaml)"
        ),
    )
    server_host: str = Field(
        default="127.0.0.1", description="Host do `cortex servir` (default loopback)"
    )
    server_porta: int = Field(default=8420, description="Porta do `cortex servir`")
    session_ttl_minutos: int = Field(
        default=30,
        ge=1,
        description="Inatividade (min) após a qual a próxima mensagem do contato abre Session nova",
    )

    # --- canal de saída / WhatsApp (Fase 7c) ---
    canal_saida: Literal["log", "evolution"] = Field(
        default="log",
        description="Canal de SAÍDA: 'log' (CI/dev, sem rede) ou 'evolution' (WhatsApp on-prem)",
    )
    evolution_base_url: str | None = Field(
        default=None, description="URL base da Evolution API (ex.: http://localhost:8080)"
    )
    evolution_instancia: str | None = Field(
        default=None, description="Nome da instância da Evolution (o número pareado)"
    )
    evolution_api_key: SecretStr | None = Field(
        default=None, description="API key da Evolution (de env/.env, nunca versionada)"
    )
    notificar_gestor: bool = Field(
        default=True,
        description="Notifica o gestor pelo canal quando entra proposta nova na Learning Queue",
    )

    # --- reflexão batch (Fase 8) ---
    reflexao_habilitada: bool = Field(
        default=True, description="Agenda a reflexão batch ('revisar o dia') no `cortex servir`"
    )
    reflexao_horario: str = Field(
        default="03:00", description="Horário diário (HH:MM) da reflexão batch"
    )
    reflexao_janela_dias: int = Field(
        default=1, ge=1, description="Janela (dias) de episódios que a reflexão revê"
    )

    # --- painel HTML de operação (Fase 7d) ---
    painel_habilitado: bool = Field(
        default=True, description="Liga o painel HTML do operador (na mesma porta do servidor)"
    )
    painel_senha: SecretStr | None = Field(
        default=None,
        description="Senha do operador no painel (de env/.env). Sem senha, o painel NÃO sobe",
    )
    painel_operador: str | None = Field(
        default=None,
        description="Nome do operador do painel (autor das decisões); None usa o gestor do USER.md",
    )
    painel_sessao_horas: int = Field(
        default=8, ge=1, description="Validade (h) do cookie de sessão do painel"
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


# Campos que são CAMINHOS: num deploy auto-contido (Fase 7a), o cortex.toml os
# escreve relativos ao diretório do deploy; carregar_config os resolve contra ele.
_CAMPOS_DE_CAMINHO = ("personas_dir", "kb_path", "audit_path", "kuzu_db_path")


def carregar_config(deploy: Path | str | None = None) -> CortexConfig:
    """Carrega a config — do deploy (Fase 7a) ou do diretório atual (dev/Mariana).

    `deploy=None` mantém o comportamento histórico: lê config.toml/.env do CWD
    (o repo é o deploy de desenvolvimento da Mariana). Com um diretório, lê o
    `cortex.toml` de lá e resolve TODOS os caminhos contra o deploy — um Cortex
    é um deploy auto-contido on-prem, sem estado compartilhado entre Cortexes.

    Os valores do toml entram como `init_settings` (precedência máxima); chaves
    de API seguem vindo do ambiente/.env — segredo nunca mora no toml versionado
    (o server_token, gerado pelo scaffold, é a exceção local do deploy).
    """
    if deploy is None:
        return CortexConfig()

    deploy = Path(deploy).resolve()
    toml = deploy / "cortex.toml"
    if not toml.is_file():
        raise FileNotFoundError(
            f"deploy sem cortex.toml: {toml}. Gere um Cortex com `cortex novo <dir>`."
        )
    dados = tomllib.loads(toml.read_text(encoding="utf-8"))
    for campo in _CAMPOS_DE_CAMINHO:
        if campo in dados:
            caminho = Path(dados[campo])
            dados[campo] = caminho if caminho.is_absolute() else deploy / caminho
    return CortexConfig(**dados)
