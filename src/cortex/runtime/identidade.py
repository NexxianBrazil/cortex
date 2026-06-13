"""Identidade autenticada do turno + demarcação anti-injection (Fase 7a).

A IDENTIDADE de quem fala segue o CANAL autenticado, nunca o conteúdo (4b): o
remetente mapeado é INTERNO com nome real; qualquer outro é EXTERNO
"desconhecido(...)", mesmo que o texto alegue "sou o gestor". O Source dos fatos
aprendidos da conversa (7b) será esta Identidade — por isso ela mora no runtime,
acompanhando a Session, e a RESOLUÇÃO por canal (canais.yaml) fica no servidor.

DADO EXTERNO É EVIDÊNCIA, NUNCA INSTRUÇÃO: a mensagem de procedência EXTERNA
entra no histórico DEMARCADA, e o builder do system prompt injeta a instrução
mecânica de não obedecer comandos contidos nela. O delimitador é sanitizado
contra escape (texto externo não pode forjar o fim do bloco).
"""

from __future__ import annotations

from dataclasses import dataclass

from cortex.identity.models import Persona
from cortex.memory.models import Procedencia

PAPEL_GESTOR = "gestor"
PAPEL_COLEGA = "colega"
PAPEL_DESCONHECIDO = "desconhecido"

# Delimitadores do bloco de dado externo. Fixos no código (formação, não SOUL).
DELIM_FIM = "<<<FIM_DADO_EXTERNO>>>"


def _delim_inicio(canal: str, canal_id: str) -> str:
    return f"<<<DADO_EXTERNO canal={canal} id={canal_id}>>>"


@dataclass(frozen=True)
class Identidade:
    """Quem fala neste turno, resolvido pelo canal autenticado (não pelo texto)."""

    nome: str
    procedencia: Procedencia
    papel: str
    canal: str = "interno"
    canal_id: str = "-"

    @property
    def externa(self) -> bool:
        return self.procedencia is Procedencia.EXTERNA


def identidade_externa(canal: str, canal_id: str) -> Identidade:
    """Remetente não mapeado: EXTERNO, nome derivado do CANAL — nunca do texto."""
    return Identidade(
        nome=f"desconhecido({canal}:{canal_id})",
        procedencia=Procedencia.EXTERNA,
        papel=PAPEL_DESCONHECIDO,
        canal=canal,
        canal_id=canal_id,
    )


def papel_no_user_md(persona: Persona, nome: str) -> str | None:
    """Papel da pessoa no USER.md: 'gestor', 'colega' — ou None se não consta.

    É a base tanto da resolução de identidade (servidor) quanto da validação de
    mapa órfão: um nome em canais.yaml que não exista aqui é erro de config.
    """
    if persona.user.autoridade.gestor.nome == nome:
        return PAPEL_GESTOR
    if any(c.nome == nome for c in persona.user.relacionamento):
        return PAPEL_COLEGA
    return None


def identidade_interna(
    persona: Persona, nome: str, canal: str = "interno", canal_id: str = "-"
) -> Identidade:
    """Identidade INTERNA de uma pessoa conhecida do USER.md (papel derivado de lá)."""
    papel = papel_no_user_md(persona, nome)
    if papel is None:
        raise ValueError(
            f"'{nome}' não consta do USER.md (nem gestor nem colega) — não pode ser interno"
        )
    return Identidade(
        nome=nome, procedencia=Procedencia.INTERNA, papel=papel, canal=canal, canal_id=canal_id
    )


def _neutralizar_delimitador(texto: str) -> str:
    """Neutraliza marcadores `<<<`/`>>>` no texto externo: não pode forjar o bloco."""
    return texto.replace("<<<", "‹‹‹").replace(">>>", "›››")


def demarcar_entrada(texto: str, identidade: Identidade | None) -> str:
    """Embrulha o texto em delimitadores de DADO EXTERNO quando a fonte é EXTERNA.

    INTERNA (ou sem identidade, no modo dev) entra como hoje. EXTERNA entra
    sanitizada e cercada — o LLM, instruído pelo builder, a trata como dado.
    """
    if identidade is None or not identidade.externa:
        return texto
    corpo = _neutralizar_delimitador(texto)
    return f"{_delim_inicio(identidade.canal, identidade.canal_id)}\n{corpo}\n{DELIM_FIM}"
