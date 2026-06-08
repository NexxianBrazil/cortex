"""Plano ENTIDADE: pessoas/empresas/objetos (Fase 3a — Ajuste 4, ESTRUTURA).

ESCOPO DESTA FASE: apenas a ESTRUTURA. Modelamos a entidade e seus atributos
com origem dupla (curada vs aprendida) para que os três planos da memória já
coexistam na arquitetura — senão a 3b (Graphiti) não encaixa. A LÓGICA
completa de entidade (resolução/merge de entidades, promoção de atributo
aprendido a curado, ligação automática episódio↔entidade) é trabalho de
fase posterior.

SEAM documentado abaixo: onde a lógica de entidade vai crescer.
"""

import itertools
from datetime import datetime
from enum import StrEnum

from pydantic import Field

from cortex.memory.models import ModeloMemoria, Source, agora

_ids_entidade = itertools.count(1)


class EntityKind(StrEnum):
    """Tipo da entidade — vocabulário inicial, extensível."""

    PERSON = "pessoa"
    COMPANY = "empresa"
    OBJECT = "objeto"


class AttributeOrigin(StrEnum):
    """Origem de um atributo — a distinção central do plano entidade.

    CURATED: veio da formação curada (USER.md / config do Data Plane) — é
        verdade de partida, confiável por definição.
    LEARNED: o agente aprendeu observando — precisa de mais cautela e carrega
        a procedência (de qual fonte/episódio veio).
    """

    CURATED = "curada"
    LEARNED = "aprendida"


class EntityAttribute(ModeloMemoria):
    """Um atributo de entidade com sua origem e procedência."""

    name: str
    value: str
    origin: AttributeOrigin
    source: Source | None = None  # de onde veio (obrigatório na prática p/ LEARNED)


class Entity(ModeloMemoria):
    """Uma pessoa/empresa/objeto com atributos de origem dupla.

    Estrutura mínima viável para a 3b persistir e para a semântica vir a
    referenciar (uma crença é sobre uma entidade). Os métodos de negócio
    chegam depois.
    """

    kind: EntityKind
    name: str
    attributes: list[EntityAttribute] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=agora)
    id: int = Field(default_factory=lambda: next(_ids_entidade))

    # ------------------------------------------------------------------ #
    # SEAM — lógica de entidade (fase posterior) conecta AQUI:
    #   - resolução/merge: decidir que "CFO Denilson" e "Denilson Medeiros"
    #     são a mesma pessoa;
    #   - promoção: um atributo LEARNED repetidamente confirmado vira CURATED;
    #   - ligação: associar cada Episode/Belief às entidades que menciona,
    #     formando o grafo que o Graphiti (3b) vai materializar.
    # Não implementado de propósito nesta fase.
    # ------------------------------------------------------------------ #
