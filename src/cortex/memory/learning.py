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

import json
from datetime import datetime
from enum import StrEnum

from pydantic import Field

from cortex.memory.models import (
    Contador,
    Justification,
    ModeloMemoria,
    Source,
    SourceKind,
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


class ProposalKind(StrEnum):
    """O que a proposta propõe.

    MEMORIA (default, Fase 4b): uma escrita de crença que o cético escalou.
    ACAO (Fase 4c): uma CHAMADA DE TOOL bloqueada pelo Decision Engine em
    enforce; aprová-la concede uma exceção ONE-SHOT (a mesma chamada, uma vez).
    """

    MEMORIA = "memoria"
    ACAO = "acao"


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

    kind: ProposalKind = ProposalKind.MEMORIA
    created_at: datetime = Field(default_factory=agora)
    status: ProposalStatus = ProposalStatus.PENDENTE

    # Preenchidos na decisão humana:
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    # Só para ACAO aprovada: quando a exceção one-shot foi de fato usada.
    consumed_at: datetime | None = None

    id: int = Field(default_factory=ids_proposta)


def argumentos_canonicos(argumentos: dict) -> str:
    """Serialização canônica dos args — a CHAVE do match da exceção one-shot.

    sort_keys garante que a mesma chamada produz sempre a mesma string, em
    qualquer provedor; é o que o DecisionEngine compara para consumir a exceção.
    """
    return json.dumps(argumentos, ensure_ascii=False, sort_keys=True)


def propor_acao(
    tool: str,
    argumentos: dict,
    risco: RiskLevel,
    motivos: list[str],
    autor_pedido: str,
    domain: str,
) -> Proposal:
    """Monta a proposta de AÇÃO de uma chamada de tool bloqueada em enforce.

    `domain` é o domínio da persona — quem decide a ação é o mesmo gestor
    autoritativo nesse domínio (autoridade vem do USER.md). `proposed_value` é
    a serialização canônica dos argumentos — a chave do match da exceção.
    `origin_episode_id=0`: ações não nascem de episódio de memória (a linhagem
    é o audit da decisão, não um episódio); por isso 0 (sentinela).
    """
    return Proposal(
        key=f"acao:{tool}",
        current_value=None,
        proposed_value=argumentos_canonicos(argumentos),
        source=Source(name=autor_pedido, kind=SourceKind.AGENT),
        justification=Justification(why="; ".join(motivos)),
        domain=domain,
        risk=risco,
        reason="bloqueada pelo Decision Engine (enforce)",
        origin_episode_id=0,
        kind=ProposalKind.ACAO,
    )
