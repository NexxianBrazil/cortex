"""Modelos da Knowledge Base — o Plano 2 da memória (Fase 5a).

A KB é a VERDADE DECLARADA da empresa: políticas, manuais, regras de negócio.
Curada por humanos, bi-temporal, consultada via RAG. Princípios no desenho:

  - RAG é método de ACESSO, não memória: a KB é consultada, nunca copiada para
    a memória semântica (isso seria a cópia que apodrece — ver promotion.py).
  - Bi-temporal: ao revogar uma política, a versão antiga ganha `vigente_ate` e
    fica acessível MARCADA; a nova entra `vigente_desde`. Revogado nunca some,
    mas nunca se passa por vigente.
  - Curadoria LEVE: o curador carimba autoridade e vigência no frontmatter; o
    documento NÃO é reescrito nem sumarizado na ingestão.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class _KBModel(BaseModel):
    """Base: proíbe campos extras (frontmatter com typo vira erro explícito)."""

    model_config = ConfigDict(extra="forbid")


class KBDocument(_KBModel):
    """Um documento da KB: metadados curados (frontmatter) + corpo.

    `autoridade` é texto livre carimbado pelo curador (ex.: 'politica_oficial',
    'manual_produto') — a procedência da regra. `vigente_ate` presente significa
    documento REVOGADO a partir daquela data; `substituido_por` aponta ao
    sucessor, fechando a linhagem bi-temporal.
    """

    titulo: str
    autoridade: str
    dominio: str
    vigente_desde: date
    vigente_ate: date | None = None  # presente = revogado a partir desta data
    substituido_por: str | None = None  # nome do arquivo sucessor, se houver
    arquivo: str  # nome do arquivo de origem (linhagem)
    corpo: str


class KBChunk(_KBModel):
    """Um trecho indexável de um documento, com seu embedding.

    Carrega os METADADOS do documento (não o corpo inteiro) para que cada
    resultado de busca já saiba sua fonte, autoridade e vigência sem releitura.
    """

    doc: KBDocument
    secao: str | None = None  # heading de origem do trecho
    texto: str
    embedding: list[float] | None = None


class ResultadoKB(_KBModel):
    """Um resultado de busca: o trecho + score + vigência (para o LLM citar)."""

    texto: str
    arquivo: str
    titulo: str
    autoridade: str
    dominio: str
    secao: str | None
    vigente_desde: date
    vigente_ate: date | None
    substituido_por: str | None
    revogado: bool  # True = veio só porque incluir_revogados; nunca é "vigente"
    score: float = Field(description="Similaridade de cosseno query×trecho")


def vigente(doc: KBDocument, em: date | None = None) -> bool:
    """O documento está vigente na data `em` (default hoje)?

    Bi-temporal: vigente_desde <= em e (sem vigente_ate, ou vigente_ate > em).
    Consultar uma data passada em que o doc ERA vigente devolve True mesmo que
    hoje ele esteja revogado.
    """
    if em is None:
        em = date.today()
    if doc.vigente_desde > em:
        return False
    return doc.vigente_ate is None or doc.vigente_ate > em
