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
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cortex.governance.policy import RiskPolicy
from cortex.risk import RiskLevel

logger = logging.getLogger("cortex.governance")

# Limiar de bloqueio em enforce: MEDIUM ou acima precisa de aprovação.
# É dado de política (poderia vir da config); mantido num ponto único.
LIMIAR_BLOQUEIO = RiskLevel.MEDIUM


class DecisionMode(StrEnum):
    """Modo do engine — escolhido pela config; observe é o default da fase."""

    OBSERVE = "observe"
    ENFORCE = "enforce"


class Verdict(StrEnum):
    """O que o engine decidiu sobre a chamada."""

    PERMITIDO = "permitido"
    PRECISA_APROVACAO = "precisa_aprovacao"


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


class DecisionEngine:
    """Avalia o risco de cada chamada de tool e decide conforme o modo."""

    def __init__(self, policy: RiskPolicy, mode: DecisionMode = DecisionMode.OBSERVE) -> None:
        self._policy = policy
        self._mode = mode
        # Trilha de auditoria em memória — toda decisão fica aqui (semente 4c).
        self.decisoes: list[Decision] = []

    @property
    def mode(self) -> DecisionMode:
        return self._mode

    def avaliar(self, tool: str, argumentos: dict) -> Decision:
        """Avalia uma chamada e devolve o Decision, já registrado e logado."""
        risco, motivos = self._policy.avaliar_risco(tool, argumentos)
        bloqueavel = risco.ordem >= LIMIAR_BLOQUEIO.ordem

        if self._mode is DecisionMode.OBSERVE:
            # Dry-run: nunca barra; só observa (e diz o que faria em enforce).
            verdict = Verdict.PERMITIDO
            executou = True
        else:
            # Enforce: risco MEDIUM+ não executa, vira pedido de aprovação.
            if bloqueavel:
                verdict = Verdict.PRECISA_APROVACAO
                executou = False
            else:
                verdict = Verdict.PERMITIDO
                executou = True

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
        self.decisoes.append(decisao)
        self._registrar(decisao)
        return decisao

    @staticmethod
    def _registrar(d: Decision) -> None:
        """Log estruturado do veredito (semente do Audit Engine da 4c)."""
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
