"""Plano SEMÂNTICO: a crença justificada (Fase 3a).

A unidade de memória NÃO é o fato — é a CRENÇA JUSTIFICADA: fato + porquê +
fonte + dois scores + bi-temporal. É o que o protótipo já materializava;
aqui em pydantic.

Os DOIS scores, separados de propósito:
  - confidence (autoridade da fonte) → manda na VERDADE (quem está ativo);
  - salience   (recência + frequência) → manda no RANKING/desempate.
Frequência não é verdade: uma correção autoritativa supera um erro repetido.
"""

from datetime import datetime

from pydantic import Field

from cortex.memory.models import (
    Contador,
    Justification,
    ModeloMemoria,
    Source,
    Status,
    agora,
)

# Contador de ids de crença. Público (sem underscore) de propósito: a
# hidratação do GraphitiStore o avança com garantir_minimo() para não colidir
# com os ids já persistidos no Kuzu (ver Contador em models.py).
ids_crenca = Contador()


class Belief(ModeloMemoria):
    """Uma crença justificada, possivelmente já superada (mas nunca apagada).

    Bi-temporal: `valid_at` marca desde quando vale; `invalid_at` é setado no
    momento em que outra crença a supera — assim a linha do tempo fica
    auditável. `supersedes` e `reason_for_change` guardam a linhagem da
    mudança (qual crença substituiu e por quê).
    """

    key: str  # chave canônica do fato (ex.: "cliente:ABC:prazo")
    value: str
    source: Source
    justification: Justification
    domain: str  # domínio do assunto (ex.: "comercial")
    confidence: float  # autoridade -> verdade

    valid_at: datetime = Field(default_factory=agora)
    invalid_at: datetime | None = None  # bi-temporal: setado ao superar
    status: Status = Status.ACTIVE
    seen_count: int = 1
    last_seen: datetime = Field(default_factory=agora)
    reason_for_change: str | None = None
    supersedes: int | None = None  # id da crença que esta substitui
    id: int = Field(default_factory=ids_crenca)

    @property
    def salience(self) -> float:
        """Recência + frequência. Hoje só frequência; decaimento é um SEAM."""
        return float(self.seen_count)
