"""Parser de documentos da KB (frontmatter + corpo) — Fase 5a.

Curadoria é REQUISITO, não opção: frontmatter inválido ou campo obrigatório
ausente é erro ALTO e CLARO na indexação, com o caminho do arquivo. Um doc sem
autoridade/vigência declaradas não entra na KB — senão a "verdade declarada"
deixaria de ser declarada.
"""

from pathlib import Path

import frontmatter
import yaml
from pydantic import ValidationError

from cortex.knowledge.models import KBDocument


class KBParseError(Exception):
    """Erro de leitura/validação de um documento da KB (frontmatter ou campos)."""


def carregar_documento(caminho: Path | str) -> KBDocument:
    """Lê um .md da KB e devolve o KBDocument validado (ou erro explicativo)."""
    caminho = Path(caminho)
    if not caminho.is_file():
        raise KBParseError(f"documento da KB não encontrado: {caminho}")
    try:
        post = frontmatter.load(str(caminho))
    except yaml.YAMLError as exc:
        raise KBParseError(
            f"{caminho}: frontmatter YAML malformado — corrija a sintaxe entre os "
            f"'---' do topo. Detalhe: {exc}"
        ) from exc
    if not post.metadata:
        raise KBParseError(
            f"{caminho}: frontmatter ausente — todo documento da KB precisa de "
            "frontmatter curado (titulo, autoridade, dominio, vigente_desde)."
        )
    try:
        return KBDocument(
            **post.metadata, arquivo=caminho.name, corpo=post.content.strip()
        )
    except ValidationError as exc:
        raise KBParseError(
            f"{caminho}: frontmatter inválido para KBDocument — verifique os campos "
            f"obrigatórios (titulo, autoridade, dominio, vigente_desde).\n{exc}"
        ) from exc
