"""Motor de reflexão batch (Fase 8) — destila padrões do episódico em propostas.

Lê os episódios de uma JANELA e roda detectores conservadores; cada padrão vira
uma `PropostaReflexao` (proposta + warrant + saliência), depois materializada
como `Proposal` PENDENTE na Learning Queue (nunca escrita direta). Idempotente
por janela: não repropõe o que já está pendente nem o que já é crença ativa.

A reflexão LÊ episódico e PROPÕE semântico (Plano 3). NÃO inventa fato de
system-of-record (preço etc. — Plano 4) nem mexe na KB.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from cortex.memory.learning import Proposal, ProposalKind, ProposalStatus
from cortex.memory.models import (
    Justification,
    Procedencia,
    Relationship,
    Source,
    SourceKind,
    Status,
)
from cortex.memory.store import MemoryStore
from cortex.risk import RiskLevel

logger = logging.getLogger("cortex.reflection")

# Limiares dos detectores (conservadores — na dúvida não propõe).
MIN_FONTES_REFORCO = 3  # afirmações internas DISTINTAS para consolidar
MIN_CONTRADICOES = 3  # escalonamentos/contradições na janela para pedir revisão

# Chaves que pertencem ao system of record (espelham sor/truth.py) — a reflexão
# NUNCA propõe sobre elas: dado vivo se consulta, não se memoriza (Plano 4).
_CHAVES_SOR = (
    re.compile(r"^produto:.+:preco$"),
    re.compile(r"^cliente:.+:limite_credito$"),
    re.compile(r"^cliente:.+:condicao_pagamento$"),
)


def _eh_chave_de_sor(key: str) -> bool:
    return any(p.match(key) for p in _CHAVES_SOR)


@dataclass
class PropostaReflexao:
    """Um padrão destilado, pronto para virar Proposal na fila."""

    key: str
    proposed_value: str
    current_value: str | None
    domain: str
    saliencia: float
    detector: str
    warrant: str
    origin_episode_id: int
    kind: ProposalKind = ProposalKind.MEMORIA


@dataclass
class RelatorioReflexao:
    """O que o batch leu e o que propôs — prestação de contas do 'pensar'."""

    janela_dias: int
    episodios_lidos: int
    propostas: list[PropostaReflexao] = field(default_factory=list)
    por_detector: dict[str, int] = field(default_factory=dict)


class ReflectionEngine:
    """Roda os detectores sobre os episódios da janela e devolve um relatório."""

    def __init__(self, store: MemoryStore, janela_dias: int = 1) -> None:
        self._store = store
        self._janela_dias = janela_dias

    def refletir(self, agora: datetime | None = None) -> RelatorioReflexao:
        agora = agora or datetime.now(UTC)
        corte = agora - timedelta(days=self._janela_dias)
        episodios = [e for e in self._store.episodes() if e.occurred_at >= corte]

        detectores = (
            self._reforco_recorrente,
            self._contradicao_cronica,
            self._eficacia_procedural,
        )
        candidatos: list[PropostaReflexao] = []
        por_detector: dict[str, int] = {}
        for detector in detectores:
            achados = detector(episodios)
            por_detector[detector.__name__.lstrip("_")] = len(achados)
            candidatos.extend(achados)

        # Idempotência: não repropor o que já está pendente nem o que já é crença
        # ativa igual. A reflexão pode rodar duas vezes sem duplicar nada.
        pendentes = {
            (p.key, p.proposed_value) for p in self._store.proposals(ProposalStatus.PENDENTE)
        }
        propostas = [
            c
            for c in candidatos
            if (c.key, c.proposed_value) not in pendentes and not self._ja_e_crenca(c)
        ]

        return RelatorioReflexao(
            janela_dias=self._janela_dias,
            episodios_lidos=len(episodios),
            propostas=propostas,
            por_detector=por_detector,
        )

    # ---- helpers ---------------------------------------------------------- #

    def _ativa(self, key: str):
        cands = [b for b in self._store.beliefs_for(key) if b.status is Status.ACTIVE]
        return max(cands, key=lambda b: b.confidence) if cands else None

    def _ja_e_crenca(self, c: PropostaReflexao) -> bool:
        if c.kind is not ProposalKind.MEMORIA:
            return False
        ativa = self._ativa(c.key)
        return ativa is not None and ativa.value == c.proposed_value

    # ---- detector 1: reforço recorrente não-promovido --------------------- #

    def _reforco_recorrente(self, episodios) -> list[PropostaReflexao]:
        """Mesma key+value afirmada por N+ fontes INTERNAS distintas → consolidar.

        Frequência vira SALIÊNCIA, não autoridade: a proposta nasce de uma fonte
        de sistema (reflexão), não do gestor — o humano ratifica.
        """
        grupos: dict[tuple[str, str], dict] = {}
        for e in episodios:
            if e.source.procedencia is not Procedencia.INTERNA:
                continue
            if e.relationship is Relationship.CONTRADICTS:
                continue  # afirmações reforçam/independem; contradição é outro detector
            if _eh_chave_de_sor(e.key):
                continue
            g = grupos.setdefault(
                (e.key, e.asserted_value),
                {"fontes": set(), "n": 0, "dominio": e.domain, "episodio": e.id},
            )
            g["fontes"].add(e.source.name)
            g["n"] += 1

        out = []
        for (key, value), g in grupos.items():
            if len(g["fontes"]) < MIN_FONTES_REFORCO:
                continue
            ativa = self._ativa(key)
            if ativa is not None and ativa.value == value:
                continue  # já consolidado
            out.append(
                PropostaReflexao(
                    key=key,
                    proposed_value=value,
                    current_value=ativa.value if ativa is not None else None,
                    domain=g["dominio"],
                    saliencia=float(g["n"]),
                    detector="reforco_recorrente",
                    warrant=(
                        f"afirmado {g['n']}x por {len(g['fontes'])} fontes internas "
                        "distintas na janela — peso por SALIÊNCIA (frequência+recência), "
                        "não por autoridade; ratifique para consolidar como crença"
                    ),
                    origin_episode_id=g["episodio"],
                )
            )
        return out

    # ---- detector 2: contradição crônica ---------------------------------- #

    def _contradicao_cronica(self, episodios) -> list[PropostaReflexao]:
        """Uma key que escalou/contradisse várias vezes → pedir REVISÃO da régua.

        Não propõe um valor — propõe atenção (kind=REVISAR): "essa informação
        vive em conflito, decida a fonte de verdade dela".
        """
        contagem: dict[str, dict] = {}
        for e in episodios:
            if _eh_chave_de_sor(e.key):
                continue
            if e.escalated or e.relationship is Relationship.CONTRADICTS:
                g = contagem.setdefault(e.key, {"n": 0, "dominio": e.domain, "episodio": e.id})
                g["n"] += 1

        out = []
        for key, g in contagem.items():
            if g["n"] < MIN_CONTRADICOES:
                continue
            ativa = self._ativa(key)
            out.append(
                PropostaReflexao(
                    key=key,
                    proposed_value=f"(revisar a fonte de verdade de '{key}')",
                    current_value=ativa.value if ativa is not None else None,
                    domain=g["dominio"],
                    saliencia=float(g["n"]),
                    detector="contradicao_cronica",
                    warrant=(
                        f"{g['n']} conflitos/escalonamentos na janela — esta chave vive "
                        "em conflito; reveja a régua/fonte de verdade dela"
                    ),
                    origin_episode_id=g["episodio"],
                    kind=ProposalKind.REVISAR,
                )
            )
        return out

    # ---- detector 3: eficácia procedural (Face 2) — INERTE ---------------- #

    def _eficacia_procedural(self, episodios) -> list[PropostaReflexao]:
        """Padrão de abordagem que precedeu SUCESSO repetido → propor fato de eficácia.

        INERTE nesta fase: o Episode NÃO carrega sinal de sucesso de OPERAÇÃO
        (action descreve a decisão de memória, não o desfecho do negócio). Não
        inventamos sinal que não existe — o detector existe e fica pronto, mas
        não propõe nada até uma fase futura instrumentar o sucesso operacional.
        Promover a Face 2 à Face 1 (AGENTS) é, de todo modo, decisão humana.
        """
        # TODO(fase futura): quando o Episode (ou um EventoOperacao) carregar um
        # sinal de sucesso de negócio, agrupar por (action, domain) e propor o
        # padrão recorrente bem-sucedido como fato semântico de eficácia.
        return []


def aplicar_reflexao(
    store: MemoryStore, relatorio: RelatorioReflexao, audit=None
) -> list[Proposal]:
    """Materializa o relatório como Proposals PENDENTES na fila + registra o audit.

    A fonte é de SISTEMA (reflexão, AGENT/INTERNA) — não o gestor: a proposta
    NÃO nasce autoritativa. Devolve as propostas criadas (a camada de serviço
    decide se notifica o gestor — a reflexão não conhece o canal).
    """
    criadas: list[Proposal] = []
    for pr in relatorio.propostas:
        proposta = Proposal(
            key=pr.key,
            current_value=pr.current_value,
            proposed_value=pr.proposed_value,
            source=Source(name="reflexão", kind=SourceKind.AGENT, procedencia=Procedencia.INTERNA),
            justification=Justification(why=pr.warrant),
            domain=pr.domain,
            risk=RiskLevel.LOW,
            reason=f"[reflexão/{pr.detector}] saliência={pr.saliencia:.0f}",
            origin_episode_id=pr.origin_episode_id,
            kind=pr.kind,
        )
        store.add_proposal(proposta)
        criadas.append(proposta)
        if audit is not None:
            audit.registrar(
                "reflexao_proposta",
                id=proposta.id,
                key=pr.key,
                detector=pr.detector,
                kind=pr.kind.value,
                saliencia=pr.saliencia,
            )

    if audit is not None:
        audit.registrar(
            "reflexao",
            janela_dias=relatorio.janela_dias,
            episodios_lidos=relatorio.episodios_lidos,
            por_detector=relatorio.por_detector,
            propostas_emitidas=len(criadas),
        )
    if criadas:
        logger.info("reflexão emitiu %d proposta(s) à fila", len(criadas))
    return criadas
