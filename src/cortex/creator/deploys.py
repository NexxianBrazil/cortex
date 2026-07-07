"""Operações de deploy do criador — criar, listar e carregar config com o .env local.

Casca, não motor: criar é `cortex.scaffold.gerar_deploy` (o mesmo do `cortex
novo`); o que o criador acrescenta é só CONFIG DE ALOCAÇÃO — o provider
escolhido no cortex.toml e a chave de API no `.env` DO DEPLOY (nunca em arquivo
versionável). Nada aqui toca SOUL/formação nem memória.
"""

import re
import tomllib
from pathlib import Path

from pydantic import SecretStr

from cortex.config import CortexConfig, carregar_config
from cortex.identity import carregar_persona
from cortex.knowledge.index import NOME_INDICE
from cortex.scaffold import gerar_deploy

PROVIDERS = {
    "stub": "Teste (offline, sem custo)",
    "claude": "Claude (API Anthropic, completo)",
    "openai": "Local/OpenAI (Ollama soberano ou API OpenAI)",
}

_NOME_DIR_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class CriacaoInvalidaError(Exception):
    """Pedido de criação inválido — vira 400 com a mensagem legível."""


def validar_nome_dir(nome: str) -> str:
    """Nome de diretório de deploy: sem separadores, sem '..' — barra o traversal."""
    if not _NOME_DIR_VALIDO.match(nome) or ".." in nome:
        raise CriacaoInvalidaError(
            f"nome de pasta inválido: {nome!r} (use letras, números, '.', '_' ou '-')"
        )
    return nome


def _ler_env(caminho: Path) -> dict[str, str]:
    """Parse mínimo de um .env (KEY=VALUE por linha; '#' comenta)."""
    valores: dict[str, str] = {}
    if not caminho.is_file():
        return valores
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valores[chave.strip()] = valor.strip().strip("'\"")
    return valores


def config_do_deploy(deploy: Path) -> CortexConfig:
    """Config do deploy com o `.env` DELE aplicado (o criador roda em outro CWD)."""
    config = carregar_config(deploy)
    env = _ler_env(deploy / ".env")
    if env.get("ANTHROPIC_API_KEY") and config.anthropic_api_key is None:
        config.anthropic_api_key = SecretStr(env["ANTHROPIC_API_KEY"])
    if env.get("OPENAI_API_KEY") and config.openai_api_key is None:
        config.openai_api_key = SecretStr(env["OPENAI_API_KEY"])
    if env.get("OPENAI_BASE_URL"):
        config.openai_base_url = env["OPENAI_BASE_URL"]
    return config


def _validar_provider(provider: str, api_key: str | None, base_url: str | None) -> None:
    if provider not in PROVIDERS:
        raise CriacaoInvalidaError(
            f"provider desconhecido: {provider!r} (opções: {', '.join(sorted(PROVIDERS))})"
        )
    if provider == "claude" and not api_key:
        raise CriacaoInvalidaError(
            "o provider Claude exige uma chave de API (ANTHROPIC_API_KEY). "
            "Cole a chave ou escolha o provider de teste (stub) para começar sem custo."
        )
    if provider == "openai" and not api_key and not base_url:
        raise CriacaoInvalidaError(
            "o provider Local/OpenAI exige uma chave de API OU a URL de um servidor "
            "local (ex.: http://localhost:11434/v1 para Ollama)."
        )


def criar_cortex(
    base_dir: Path,
    *,
    nome: str,
    funcao: str,
    gestor: str,
    dominio: str = "geral",
    destino: str = "",
    provider: str = "stub",
    api_key: str | None = None,
    base_url: str | None = None,
) -> Path:
    """Valida, chama o scaffold EXISTENTE e ajusta a config de alocação.

    `destino` é um nome de pasta DENTRO do base_dir (o criador visual só
    gerencia deploys do diretório-base); vazio usa o nome da persona. Chave de
    API vai para o `.env` do deploy — nunca para o cortex.toml.
    """
    faltando = [
        rotulo
        for rotulo, valor in (("nome", nome), ("função", funcao), ("gestor", gestor))
        if not (valor or "").strip()
    ]
    if faltando:
        raise CriacaoInvalidaError(f"campos obrigatórios ausentes: {', '.join(faltando)}")
    _validar_provider(provider, api_key, base_url)

    pasta = validar_nome_dir((destino or "").strip() or nome.strip().lower().replace(" ", "-"))
    caminho = gerar_deploy(  # ScaffoldError (destino não-vazio etc.) sobe para a rota
        base_dir / pasta,
        nome=nome.strip(),
        funcao=funcao.strip(),
        gestor=gestor.strip(),
        dominio=(dominio or "geral").strip() or "geral",
    )

    if provider != "stub":
        toml = caminho / "cortex.toml"
        toml.write_text(
            toml.read_text(encoding="utf-8").replace(
                'provider = "stub"', f'provider = "{provider}"', 1
            ),
            encoding="utf-8",
        )

    linhas = []
    if api_key:
        variavel = "ANTHROPIC_API_KEY" if provider == "claude" else "OPENAI_API_KEY"
        linhas.append(f"{variavel}={api_key}")
    if base_url and provider == "openai":
        linhas.append(f"OPENAI_BASE_URL={base_url}")
    if linhas:
        (caminho / ".env").write_text("\n".join(linhas) + "\n", encoding="utf-8")

    return caminho


def listar_deploys(base_dir: Path) -> list[dict]:
    """Varre o diretório-base: cada pasta com `cortex.toml` é um deploy."""
    deploys = []
    if not base_dir.is_dir():
        return deploys
    for pasta in sorted(base_dir.iterdir()):
        if not (pasta / "cortex.toml").is_file():
            continue
        info: dict = {"pasta": pasta.name, "caminho": str(pasta)}
        try:
            dados = tomllib.loads((pasta / "cortex.toml").read_text(encoding="utf-8"))
            persona = carregar_persona(pasta / dados.get("personas_dir", "personas"))
            info.update(
                nome=persona.soul.nome,
                funcao=persona.soul.papel,
                gestor=persona.user.autoridade.gestor.nome,
                provider=dados.get("provider", "stub"),
                store=dados.get("store", "memory"),
                kb_indexada=(pasta / dados.get("kb_path", "kb") / NOME_INDICE).is_file(),
            )
        except Exception as exc:  # noqa: BLE001 — deploy quebrado é informação, não crash
            info["erro"] = f"deploy não carrega: {exc}"
        deploys.append(info)
    return deploys
