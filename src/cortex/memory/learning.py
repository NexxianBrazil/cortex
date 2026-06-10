"""Learning Queue — o Plano 6 da memória (Fase 4b).

Materializa o thesis do projeto: *supervised learning, never autonomous
mutation*. Quando o cético escala uma contradição de alto risco, o agente não
muta a memória sozinho — emite uma PROPOSTA que um humano autoritativo decide.
Toda decisão (aprovação E rejeição) vira memória com autor.

Por que aqui (e não em governance/): a fila é um PLANO DA MEMÓRIA — propostas
persistem ao lado de crenças e episódios, nunca são apagadas (só mudam de
status), e cada transição gera episódio. A memória nunca importa o runtime
nem a governança; a direção é sempre runtime/governança → memória.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from cortex.memory.models import (
    Contador,
    Justification,
    ModeloMemoria,
    Source,
    agora,
)
from cortex.risk import RiskLevel

# Contador de ids de proposta — mesmo padrão dos outros planos (público; a
# hidratação do GraphitiStore o avança com garantir_minimo).
ids_proposta = Contador()


class ProposalStatus(StrEnum):
    """Estado de uma proposta. Nunca se apaga — o status é a única mutação.

    Toda transição (aprovar/rejeitar/caducar) gera um episódio com autor.
    """

    PENDENTE = "pendente"
    APROVADA = "aprovada"
    REJEITADA = "rejeitada"
    CADUCADA = "caducada"  # a crença vigente mudou desde o escalonamento


class Proposal(ModeloMemoria):
    """O WARRANT completo que o humano vê para decidir informado.

    Carrega tudo que sustenta a decisão: o que muda (key/atual→proposto), quem
    afirma e com que lastro (fonte+procedência+justificação), o impacto
    estimado (risco) e o porquê do escalonamento, mais a linhagem ao episódio
    que a originou. A procedência da fonte É parte do warrant: o humano precisa
    saber se a afirmação veio de canal autenticado ou de fora.
    """

    key: str
    current_value: str | None  # valor vigente quando escalou (None se assunto novo)
    proposed_value: str

    source: Source  # quem afirma (com procedência — parte do warrant)
    justification: Justification  # com que lastro

    domain: str
    risk: RiskLevel  # nível que disparou o escalonamento
    reason: str  # por que o cético escalou
    origin_episode_id: int  # linhagem ao episódio que originou a proposta

    created_at: datetime = Field(default_factory=agora)
    status: ProposalStatus = ProposalStatus.PENDENTE

    # Preenchidos na decisão humana:
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None

    id: int = Field(default_factory=ids_proposta)
