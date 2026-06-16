"""Scaffolding de uma ACP auto-contida — `cortex novo` (Fase 7a).

Gera um deploy on-prem completo (persona com a formação universal parametrizada,
KB, canais.yaml, cortex.toml com token de bridge) a partir dos templates
empacotados. O deploy NASCE VALIDADO: o scaffold termina carregando a persona e
a config gerados — um Cortex que nasce quebrado é bug do scaffold, não do
operador.
"""

import importlib.resources as resources
import secrets
from pathlib import Path
from string import Template

from cortex.config import carregar_config
from cortex.identity import carregar_persona

# destino relativo no deploy  →  nome do template empacotado
_MAPA_ARQUIVOS = {
    "cortex.toml": "cortex.toml",
    "README.md": "README.md",
    "canais.yaml": "canais.yaml",
    "personas/SOUL.md": "SOUL.md",
    "personas/USER.md": "USER.md",
    "personas/AGENTS.md": "AGENTS.md",
    "personas/tools.yaml": "tools.yaml",
    "personas/playbooks/exemplo_operacao.md": "playbooks/exemplo_operacao.md",
    "kb/README.md": "kb/README.md",
}


class ScaffoldError(Exception):
    """Falha ao gerar o deploy (destino inválido ou persona gerada não carrega)."""


def _ler_template(rel: str) -> str:
    return resources.files("cortex.templates").joinpath(rel).read_text(encoding="utf-8")


def gerar_deploy(
    destino: Path | str,
    *,
    nome: str,
    funcao: str,
    gestor: str,
    dominio: str = "geral",
    token: str | None = None,
    painel_senha: str | None = None,
) -> Path:
    """Cria o deploy em `destino` e o valida. Devolve o caminho do deploy.

    `token`/`painel_senha=None` geram segredos novos (`secrets.token_urlsafe`);
    os parâmetros existem para testes determinísticos. Destino inexistente é
    criado; destino com conteúdo é recusado (não sobrescrevemos um Cortex).
    """
    destino = Path(destino)
    if destino.exists() and any(destino.iterdir()):
        raise ScaffoldError(
            f"destino não está vazio: {destino}. Aponte para um diretório novo."
        )

    subs = {
        "nome": nome,
        "papel": funcao,
        "gestor": gestor,
        "dominio": dominio,
        "token": token or secrets.token_urlsafe(32),
        "painel_senha": painel_senha or secrets.token_urlsafe(18),
    }

    for destino_rel, template_rel in _MAPA_ARQUIVOS.items():
        conteudo = Template(_ler_template(template_rel)).safe_substitute(subs)
        alvo = destino / destino_rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(conteudo, encoding="utf-8")

    # Deploy nasce validado: persona carrega e config resolve, ou é bug do scaffold.
    try:
        carregar_persona(destino / "personas")
        carregar_config(destino)
    except Exception as exc:  # noqa: BLE001 — re-empacota qualquer falha de validação
        raise ScaffoldError(
            f"o deploy gerado em {destino} não carregou — isto é um bug do scaffold "
            f"(templates inválidos), não um erro seu. Detalhe: {exc}"
        ) from exc

    return destino


def proximos_passos(destino: Path) -> str:
    """Texto de 'próximos passos' impresso após gerar o deploy."""
    return (
        f"Cortex criado em {destino}\n\n"
        "Próximos passos:\n"
        f"  1. Curar a formação:  {destino}/personas/ (SOUL.md, USER.md, playbooks/)\n"
        f"  2. Curar a KB:        {destino}/kb/  (ver kb/README.md) e depois\n"
        f"     cortex kb indexar --deploy {destino}\n"
        f"  3. Mapear canais:     {destino}/canais.yaml (contatos → pessoas do USER.md)\n"
        f"  4. Subir o servidor:  cortex servir --deploy {destino}\n"
        f"  5. Plugar o bridge no POST /v1/mensagens (token em {destino}/cortex.toml)"
    )
