"""Fase 3 — Memória: episódica / entidade / semântica.

Fase 3a (esta entrega): o NÚCLEO LÓGICO em memória, portado e amadurecido a
partir do protótipo. A unidade é a crença justificada; o motor `observe()`
classifica a relação e só na contradição liga o ceticismo; nada é apagado;
cada observação gera um episódio preservado. Persistência (Graphiti, 3b) e
conexão com o runtime (3c) plugam pelas interfaces aqui definidas, sem o
motor mudar.

Os três planos:
  - semântica → Belief (a verdade atual, justificada);
  - episódica → Episode (o registro bruto do que aconteceu);
  - entidade  → Entity (estrutura; lógica completa em fase posterior).
"""

from cortex.memory.classifier import (
    Classifier,
    HeuristicClassifier,
    LLMClassifier,
)
from cortex.memory.engine import (
    AutoridadeInsuficienteError,
    MemoryEngine,
    PropostaJaDecididaError,
)
from cortex.memory.entity import (
    AttributeOrigin,
    Entity,
    EntityAttribute,
    EntityKind,
)
from cortex.memory.episodic import Episode
from cortex.memory.factory import (
    ConfiguracaoClassifierError,
    ConfiguracaoStoreError,
    criar_classifier,
    criar_store,
)
from cortex.memory.learning import (
    Proposal,
    ProposalKind,
    ProposalStatus,
    propor_acao,
)
from cortex.memory.models import (
    Justification,
    Procedencia,
    Relationship,
    Source,
    SourceKind,
    Status,
    agora,
)
from cortex.memory.seams import (
    AuthorityMap,
    DictAuthorityMap,
    DictSourceOfTruth,
    SourceOfTruth,
    TruthLookup,
)
from cortex.memory.semantic import Belief
from cortex.memory.store import InMemoryStore, MemoryStore

__all__ = [
    "AttributeOrigin",
    "AuthorityMap",
    "AutoridadeInsuficienteError",
    "Belief",
    "Classifier",
    "ConfiguracaoClassifierError",
    "ConfiguracaoStoreError",
    "DictAuthorityMap",
    "DictSourceOfTruth",
    "Entity",
    "EntityAttribute",
    "EntityKind",
    "Episode",
    "HeuristicClassifier",
    "InMemoryStore",
    "Justification",
    "LLMClassifier",
    "MemoryEngine",
    "MemoryStore",
    "Procedencia",
    "Proposal",
    "ProposalKind",
    "ProposalStatus",
    "PropostaJaDecididaError",
    "Relationship",
    "Source",
    "SourceKind",
    "SourceOfTruth",
    "Status",
    "TruthLookup",
    "agora",
    "criar_classifier",
    "criar_store",
    "propor_acao",
]
