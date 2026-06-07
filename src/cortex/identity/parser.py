"""Parser dos arquivos de formação → modelos pydantic (Fase 1).

Princípio: NUNCA falhar em silêncio. Os arquivos de formação são editados por
humanos; todo erro (YAML malformado, campo faltando, tool inexistente) vira
uma exceção com o caminho do arquivo e a explicação do problema — para o
curador conseguir corrigir sem ler stack trace de biblioteca.
"""

from pathlib import Path
from typing import TypeVar

import frontmatter
import yaml
from pydantic import ValidationError

from cortex.identity.models import Persona, Playbook, Soul, ToolDeclaration, User

M = TypeVar("M", Soul, Playbook, ToolDeclaration, User)

# Nomes canônicos dentro da pasta de uma persona.
ARQUIVO_SOUL = "SOUL.md"
ARQUIVO_USER = "USER.md"
ARQUIVO_TOOLS = "tools.yaml"
PASTA_PLAYBOOKS = "playbooks"


class PersonaParseError(Exception):
    """Erro de leitura ou validação de um arquivo de formação.

    Base de todos os erros do parser — quem carrega uma persona pode capturar
    só esta exceção e ter certeza de que pegou qualquer problema de formação.
    """


class ReferenciaInvalidaError(PersonaParseError):
    """Quebra de integridade referencial entre arquivos de formação.

    Ex.: um playbook referencia uma tool que não existe no catálogo. É uma
    classe própria (e não só uma mensagem) porque fases futuras vão querer
    tratar quebra de referência diferente de arquivo malformado.
    """


def _ler_hibrido(caminho: Path) -> tuple[dict, str]:
    """Lê um arquivo híbrido (frontmatter YAML + prosa) com erros explicativos."""
    if not caminho.is_file():
        raise PersonaParseError(f"arquivo de formação não encontrado: {caminho}")
    try:
        post = frontmatter.load(str(caminho))
    except yaml.YAMLError as exc:
        raise PersonaParseError(
            f"{caminho}: YAML malformado no frontmatter — corrija a sintaxe "
            f"entre os '---' do topo do arquivo. Detalhe: {exc}"
        ) from exc
    if not post.metadata:
        raise PersonaParseError(
            f"{caminho}: frontmatter YAML ausente — o arquivo precisa começar "
            "com um bloco entre '---' contendo os campos estruturados."
        )
    return dict(post.metadata), post.content.strip()


def _validar(modelo: type[M], dados: dict, caminho: Path) -> M:
    """Converte dict → modelo pydantic, traduzindo o erro para o curador."""
    try:
        return modelo.model_validate(dados)
    except ValidationError as exc:
        raise PersonaParseError(
            f"{caminho}: conteúdo inválido para {modelo.__name__} — "
            f"verifique os campos apontados abaixo.\n{exc}"
        ) from exc


def carregar_soul(caminho: Path | str) -> Soul:
    """Carrega e valida um SOUL.md (comportamentos no frontmatter + prosa)."""
    caminho = Path(caminho)
    meta, prosa = _ler_hibrido(caminho)
    return _validar(Soul, {**meta, "prosa": prosa}, caminho)


def carregar_user(caminho: Path | str) -> User:
    """Carrega e valida um USER.md (blocos autoridade/relacionamento + prosa)."""
    caminho = Path(caminho)
    meta, prosa = _ler_hibrido(caminho)
    return _validar(User, {**meta, "prosa": prosa}, caminho)


def carregar_playbook(caminho: Path | str) -> Playbook:
    """Carrega e valida um playbook .md (operação estruturada + manual em prosa)."""
    caminho = Path(caminho)
    meta, prosa = _ler_hibrido(caminho)
    return _validar(Playbook, {**meta, "prosa": prosa}, caminho)


def carregar_tools(caminho: Path | str) -> dict[str, ToolDeclaration]:
    """Carrega o catálogo de tools (tools.yaml) indexado por nome.

    O catálogo é a fonte única de verdade das tools: cada uma é declarada
    aqui UMA vez e os playbooks apenas a referenciam pelo nome.
    """
    caminho = Path(caminho)
    if not caminho.is_file():
        raise PersonaParseError(f"catálogo de tools não encontrado: {caminho}")
    try:
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PersonaParseError(
            f"{caminho}: YAML malformado no catálogo de tools. Detalhe: {exc}"
        ) from exc
    if not isinstance(dados, dict) or not isinstance(dados.get("tools"), list):
        raise PersonaParseError(
            f"{caminho}: estrutura inesperada — esperado um mapeamento com a "
            "chave 'tools' contendo a lista de declarações."
        )

    catalogo: dict[str, ToolDeclaration] = {}
    for item in dados["tools"]:
        tool = _validar(ToolDeclaration, item, caminho)
        if tool.nome in catalogo:
            raise PersonaParseError(
                f"{caminho}: tool '{tool.nome}' declarada mais de uma vez — "
                "cada tool deve ser declarada uma única vez no catálogo."
            )
        catalogo[tool.nome] = tool
    return catalogo


def carregar_persona(pasta: Path | str) -> Persona:
    """Carrega a formação completa de uma pasta e valida a integridade.

    Esta é a porta de entrada da camada de identidade: lê SOUL.md, USER.md,
    o catálogo de tools e todos os playbooks de `playbooks/`, e só devolve a
    Persona se a integridade referencial fechar (todo playbook referencia
    apenas tools existentes no catálogo).
    """
    pasta = Path(pasta)
    if not pasta.is_dir():
        raise PersonaParseError(f"pasta de persona não encontrada: {pasta}")

    soul = carregar_soul(pasta / ARQUIVO_SOUL)
    user = carregar_user(pasta / ARQUIVO_USER)
    tools = carregar_tools(pasta / ARQUIVO_TOOLS)

    playbooks: dict[str, Playbook] = {}
    pasta_playbooks = pasta / PASTA_PLAYBOOKS
    arquivos = sorted(pasta_playbooks.glob("*.md")) if pasta_playbooks.is_dir() else []
    for arquivo in arquivos:
        playbook = carregar_playbook(arquivo)
        if playbook.operacao in playbooks:
            raise PersonaParseError(
                f"{arquivo}: operação '{playbook.operacao}' já definida em outro "
                "playbook — cada operação deve ter um único arquivo."
            )
        playbooks[playbook.operacao] = playbook

    # Integridade referencial: nenhum playbook pode apontar para tool fantasma.
    for nome, playbook in playbooks.items():
        faltantes = sorted(playbook.tools_referenciadas - tools.keys())
        if faltantes:
            raise ReferenciaInvalidaError(
                f"playbook '{nome}' referencia tools inexistentes no catálogo: "
                f"{faltantes}. Declare-as em {ARQUIVO_TOOLS} ou corrija o playbook."
            )

    return Persona(soul=soul, user=user, tools=tools, playbooks=playbooks)
