"""Plano EPISÓDICO: o registro BRUTO do que aconteceu (Fase 3a — Ajuste 4).

Toda chamada a `observe()` gera UM episódio preservado — o evento cru: o que
foi afirmado, por quem, quando, e qual decisão saiu disso. A episódica é a
fonte de onde a semântica deriva e a base da LINHAGEM: nunca apaga nada.

Por que separar da semântica: a crença (semântica) é a conclusão atual sobre
a verdade; o episódio é o fato histórico de que 'em tal instante, fulano
afirmou X e o agente decidiu Y'. Mesmo quando a crença é superada ou a
afirmação é escalada/rejeitada, o episódio permanece intacto.
"""

import itertools
from datetime import datetime

from pydantic import Field

from cortex.memory.models import (
    Justification,
    ModeloMemoria,
    Relationship,
    Source,
    agora,
)
from cortex.risk import RiskLevel

_ids_episodio = itertools.count(1)


class Episode(ModeloMemoria):
    """Um evento cru de observação, com a decisão que dele resultou.

    Liga os planos: `resulting_belief_id` aponta para a crença semântica
    criada/afetada (lineage episódica → semântica). Os campos de decisão
    (`relationship`, `risk`, `action`, `reason`) são o traço auditável que a
    governança da Fase 4 vai consumir.
    """

    key: str
    asserted_value: str  # o que foi afirmado (valor cru, mesmo que recusado)
    source: Source  # por quem
    justification: Justification  # com qual lastro
    domain: str
    occurred_at: datetime = Field(default_factory=agora)  # quando

    # --- a decisão tomada ---
    relationship: Relationship
    risk: RiskLevel
    action: str  # descrição da ação tomada (ex.: "deliberou e superou")
    reason: str | None = None
    magnitude_ratio: float | None = None  # preenchido só em contradição numérica
    escalated: bool = False  # foi para a fila de aprovação humana?

    # --- lineage / consulta à fonte de verdade ---
    # Registramos QUE a fonte de verdade foi consultada e QUAL valor ela deu
    # (para auditoria), mas esse valor NÃO vira crença permanente — ver
    # SourceOfTruth. O valor "apodrece"; a consulta fica registrada.
    source_of_truth_consulted: bool = False
    source_of_truth_value: str | None = None
    resulting_belief_id: int | None = None

    id: int = Field(default_factory=lambda: next(_ids_episodio))
