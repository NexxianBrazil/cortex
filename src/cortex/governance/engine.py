"""Decision Engine — o motor de risco (Fase 4a).

Recebe uma CHAMADA CONCRETA de tool (nome + argumentos) e a RiskPolicy, e
produz um Decision: qual risco, por quê (quais escaladores dispararam) e o
veredito conforme o modo.

DOIS MODOS, OBSERVE É O DEFAULT desta fase:
  - observe (dry-run): avalia e LOGA o veredito, mas DEIXA a ação executar.
    É para CALIBRAR as regras com dados reais antes de ligar o bloqueio —
    ligar enforce sem calibrar arrisca barrar o legítimo e quebrar a operação.
  - enforce: o MESMO engine, mas barra ações de risco MEDIUM+ (roteia para
    aprovação — a Learning Queue da 4c; aqui apenas NEGA e registra que
    precisaria de aprovação, sem implementar a fila).

Filosofia (deny-by-default, permission-first, audit-first): o veredito de toda
decisão é registrado de forma estruturada (`decisoes`) — a semente do Audit
Engine da 4c. Nada de decisão silenciosa.
"""

import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from cortex.governance.policy import RiskPolicy
from cortex.risk import RiskLevel

logger = logging.getLogger("cortex.governance")

# Limiar de bloqueio em enforce: MEDIUM ou acima precisa de aprovação.
# É dado de política (poderia vir da config); mantido num ponto único.
LIMIAR_BLOQUEIO = RiskLevel.MEDIUM


class InvarianteSemLinhagemError(Exception):
    """Um invariante cita um soul_behavior_id que não existe no SOUL carregado.

    A formação é a FONTE; a policy é a materialização. Invariante órfão é erro
    de configuração — falha alto na montagem, não em runtime.
    """


class DecisionMode(StrEnum):
    """Modo do engine — escolhido pela config; observe é o default da fase."""

    OBSERVE = "observe"
    ENFORCE = "enforce"


class Verdict(StrEnum):
    """O que o engine decidiu sobre a chamada."""

    PERMITIDO = "permitido"
    PRECISA_APROVACAO = "precisa_aprovacao"
    PROIBIDO_FORMACAO = "proibido_formacao"  # viola invariante do SOUL — piso inviolável


class Decision(BaseModel):
    """O veredito estruturado de uma chamada — registro de auditoria (semente 4c)."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    argumentos: dict
    risco: RiskLevel
    motivos: list[str]  # base + escaladores que dispararam
    modo: DecisionMode
    verdict: Verdict
    executou: bool  # a ação vai/foi executada?
    bloqueavel: bool  # risco >= limiar (em enforce seria barrada)
    soul_behavior_id: str | None = None  # linhagem ao SOUL quando PROIBIDO_FORMACAO


class DecisionEngine:
    """Avalia o risco de cada chamada de tool e decide conforme o modo."""

    def __init__(
        self,
        policy: RiskPolicy,
        mode: DecisionMode = DecisionMode.OBSERVE,
        buscar_excecao: Callable[[str, str], Any | None] | None = None,
        audit: Any | None = None,
    ) -> None:
        self._policy = policy
        self._mode = mode
        # Callable injetado pelo runtime (memory.consumir_excecao) — a
        # governança NÃO importa a memória; recebe só a função (Bloco 2).
        self._buscar_excecao = buscar_excecao
        # AuditTrail injetado pelo runtime (Bloco 3); None = só log em memória.
        self._audit = audit
        # Trilha de auditoria em memória — toda decisão fica aqui.
        self.decisoes: list[Decision] = []

    @property
    def mode(self) -> DecisionMode:
        return self._mode

    def avaliar(self, tool: str, argumentos: dict) -> Decision:
        """Avalia uma chamada e devolve o Decision, já registrado e logado."""
        # PISO INVIOLÁVEL (doutrina §6.1): invariante de formação é checado
        # ANTES de qualquer lógica de modo. Viola → PROIBIDO em QUALQUER modo
        # (sem dry-run), executou=False, não-aprovável pela fila do cliente.
        inv = self._policy.violacao(tool, argumentos)
        if inv is not None:
            decisao = Decision(
                tool=tool,
                argumentos=argumentos,
                risco=RiskLevel.CRITICAL,
                motivos=[f"invariante de formação [{inv.soul_behavior_id}]: {inv.mensagem}"],
                modo=self._mode,
                verdict=Verdict.PROIBIDO_FORMACAO,
                executou=False,
                bloqueavel=True,
                soul_behavior_id=inv.soul_behavior_id,
            )
            self._finalizar(decisao)
            return decisao

        risco, motivos = self._policy.avaliar_risco(tool, argumentos)
        bloqueavel = risco.ordem >= LIMIAR_BLOQUEIO.ordem

        if self._mode is DecisionMode.OBSERVE:
            # Dry-run: nunca barra; só observa (e diz o que faria em enforce).
            verdict = Verdict.PERMITIDO
            executou = True
        elif not bloqueavel:
            verdict = Verdict.PERMITIDO
            executou = True
        else:
            # Enforce + bloqueável: antes de barrar, há exceção aprovada para
            # EXATAMENTE esta chamada? A aprovação humana concede uma exceção
            # one-shot que consumimos aqui (match exato tool+args canônicos).
            excecao = self._consumir_excecao(tool, argumentos)
            if excecao is not None:
                verdict = Verdict.PERMITIDO
                executou = True
                motivos = [*motivos, f"exceção aprovada por humano (proposta {excecao.id})"]
            else:
                verdict = Verdict.PRECISA_APROVACAO
                executou = False

        decisao = Decision(
            tool=tool,
            argumentos=argumentos,
            risco=risco,
            motivos=motivos,
            modo=self._mode,
            verdict=verdict,
            executou=executou,
            bloqueavel=bloqueavel,
        )
        self._finalizar(decisao)
        return decisao

    def _consumir_excecao(self, tool: str, argumentos: dict):
        """Procura/consome a exceção one-shot via o callable injetado (ou None).

        O DecisionEngine NÃO importa a memória — recebe a função
        (memory.consumir_excecao). Serializa os args canonicamente (mesmo
        json.dumps sort_keys da proposta) para o match exato.
        """
        if self._buscar_excecao is None:
            return None
        import json

        argumentos_json = json.dumps(argumentos, ensure_ascii=False, sort_keys=True)
        return self._buscar_excecao(tool, argumentos_json)

    def _finalizar(self, decisao: Decision) -> None:
        """Registra a decisão na trilha em memória, no audit (se houver) e loga."""
        self.decisoes.append(decisao)
        if self._audit is not None:
            self._audit.registrar(
                "decisao_tool",
                tool=decisao.tool,
                risco=decisao.risco.value,
                modo=decisao.modo.value,
                verdict=decisao.verdict.value,
                executou=decisao.executou,
                bloqueavel=decisao.bloqueavel,
                soul_behavior_id=decisao.soul_behavior_id,
                motivos=decisao.motivos,
                argumentos=decisao.argumentos,
            )
        self._registrar(decisao)

    @staticmethod
    def _registrar(d: Decision) -> None:
        """Log estruturado do veredito (semente do Audit Engine da 4c)."""
        if d.verdict is Verdict.PROIBIDO_FORMACAO:
            logger.warning(
                "decisão PROIBIDA POR FORMAÇÃO tool=%s comportamento=%s motivos=%s args=%s",
                d.tool,
                d.soul_behavior_id,
                "; ".join(d.motivos),
                d.argumentos,
            )
            return
        marca = "EXECUTA" if d.executou else "BLOQUEIA"
        if d.modo is DecisionMode.OBSERVE and d.bloqueavel:
            marca = "EXECUTA (seria BLOQUEADA em enforce)"
        logger.info(
            "decisão tool=%s risco=%s modo=%s verdict=%s %s motivos=%s args=%s",
            d.tool,
            d.risco.value,
            d.modo.value,
            d.verdict.value,
            marca,
            "; ".join(d.motivos),
            d.argumentos,
        )
