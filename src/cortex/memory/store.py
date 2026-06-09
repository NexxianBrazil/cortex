"""Persistência da memória — interface MemoryStore (Fase 3a — Ajuste 5).

Hoje dicts em memória; a 3b pluga o Graphiti AQUI, sem o motor mudar. A
interface cobre os TRÊS planos de propósito (semântica/episódica/entidade):
o Graphiti é um backend único para os três, então um único MemoryStore é o
ponto de extensão natural.

Invariantes que qualquer backend deve honrar:
  - a episódica é append-only (nunca remove um episódio);
  - a semântica nunca apaga: supersessão é rebaixamento de status, e tanto a
    crença ativa quanto as superadas continuam recuperáveis por `beliefs_for`.
"""

from abc import ABC, abstractmethod

from cortex.memory.entity import Entity
from cortex.memory.episodic import Episode
from cortex.memory.semantic import Belief


class MemoryStore(ABC):
    """Contrato de persistência dos três planos da memória."""

    # --- semântica (crenças) ---
    @abstractmethod
    def add_belief(self, belief: Belief) -> None:
        """Acrescenta uma crença (nova ou superando outra). Nunca substitui."""

    @abstractmethod
    def beliefs_for(self, key: str) -> list[Belief]:
        """Todas as crenças daquela chave — ativas, superadas e rejeitadas."""

    # --- episódica (eventos crus) ---
    @abstractmethod
    def add_episode(self, episode: Episode) -> None:
        """Registra um episódio. Append-only: jamais removido."""

    @abstractmethod
    def episodes(self) -> list[Episode]:
        """Todos os episódios, em ordem de registro."""

    @abstractmethod
    def episodes_for(self, key: str) -> list[Episode]:
        """Episódios de uma chave — a lineage bruta daquele fato."""

    # --- entidade (estrutura; lógica é fase posterior) ---
    @abstractmethod
    def upsert_entity(self, entity: Entity) -> None:
        """Insere/atualiza uma entidade por id."""

    @abstractmethod
    def entities(self) -> list[Entity]:
        """Todas as entidades conhecidas."""


class InMemoryStore(MemoryStore):
    """Default desta fase: tudo em estruturas Python na memória do processo.

    Equivale ao armazenamento do protótipo, mas atrás da interface — assim os
    testes (e o motor) já exercitam exatamente o contrato que o Graphiti vai
    implementar na 3b.
    """

    def __init__(self) -> None:
        self._crencas: dict[str, list[Belief]] = {}
        self._episodios: list[Episode] = []
        self._entidades: dict[int, Entity] = {}

    def add_belief(self, belief: Belief) -> None:
        self._crencas.setdefault(belief.key, []).append(belief)

    def beliefs_for(self, key: str) -> list[Belief]:
        return list(self._crencas.get(key, []))

    def add_episode(self, episode: Episode) -> None:
        self._episodios.append(episode)

    def episodes(self) -> list[Episode]:
        return list(self._episodios)

    def episodes_for(self, key: str) -> list[Episode]:
        return [e for e in self._episodios if e.key == key]

    def upsert_entity(self, entity: Entity) -> None:
        self._entidades[entity.id] = entity

    def entities(self) -> list[Entity]:
        return list(self._entidades.values())

    # --- helpers de iteração (leitura) ---
    # Não fazem parte do contrato MemoryStore; existem para um backend de
    # persistência (GraphitiStore) usar este store como working set e
    # re-serializar o conjunto completo. São leituras puras, seguras.

    def all_beliefs(self) -> list[Belief]:
        """Todas as crenças de todas as chaves (ativas e superadas)."""
        return [b for crencas in self._crencas.values() for b in crencas]

    def all_episodes(self) -> list[Episode]:
        """Todos os episódios registrados."""
        return list(self._episodios)

    def all_entities(self) -> list[Entity]:
        """Todas as entidades conhecidas."""
        return list(self._entidades.values())
