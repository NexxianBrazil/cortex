"""Policy-as-data da Decision Engine (Fase 4a).

Por que policy-as-data: a política de risco é DADO declarativo que o engine
interpreta — não código de risco espalhado. Adicionar uma regra nova é editar
a declaração (um escalador a mais), nunca reescrever o engine. Isso mantém a
política auditável, versionável e editável por quem define risco, não só por
quem programa.

A unidade é o RISK-BY-SCOPE: o risco não é do TIPO da tool, é da CHAMADA
concreta — função dos parâmetros e do raio de impacto. Por isso a policy de
uma tool é um risco BASE + ESCALADORES (predicados sobre os argumentos que
elevam o nível). A mesma tool pode sair LOW ou CRITICAL conforme os args.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from cortex.risk import RiskLevel, risco_maximo


class _PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Operador(StrEnum):
    """Operadores declarativos de uma condição sobre um parâmetro.

    Pequeno e suficiente para o risk-by-scope: comparação numérica, presença,
    sufixo (domínio de e-mail), verdade (anexo presente), pertencimento.
    Predicados ausentes nunca disparam — conservador por construção.
    """

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    ENDSWITH = "endswith"
    NOT_ENDSWITH = "not_endswith"
    TRUTHY = "truthy"
    FALSY = "falsy"
    IN = "in"
    NOT_IN = "not_in"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class Condition(_PolicyModel):
    """Um predicado declarativo sobre UM parâmetro da chamada de tool."""

    param: str
    op: Operador
    value: Any = None

    def aplica(self, argumentos: dict) -> bool:
        """Avalia o predicado contra os argumentos. Ausência → False (cauteloso)."""
        presente = self.param in argumentos
        v = argumentos.get(self.param)
        op = self.op

        if op is Operador.EXISTS:
            return presente
        if op is Operador.NOT_EXISTS:
            return not presente
        if op is Operador.TRUTHY:
            return presente and bool(v)
        if op is Operador.FALSY:
            return presente and not bool(v)

        if op in (Operador.GT, Operador.GTE, Operador.LT, Operador.LTE):
            a, b = _num(v), _num(self.value)
            if a is None or b is None:
                return False
            if op is Operador.GT:
                return a > b
            if op is Operador.GTE:
                return a >= b
            if op is Operador.LT:
                return a < b
            return a <= b

        if op is Operador.EQ:
            return v == self.value
        if op is Operador.NE:
            return v != self.value
        if op is Operador.CONTAINS:
            return presente and self.value in v
        if op is Operador.ENDSWITH:
            return presente and str(v).endswith(str(self.value))
        if op is Operador.NOT_ENDSWITH:
            return presente and not str(v).endswith(str(self.value))
        if op is Operador.IN:
            return presente and v in (self.value or [])
        if op is Operador.NOT_IN:
            return presente and v not in (self.value or [])
        return False


class RiskEscalator(_PolicyModel):
    """Um escalador: se TODAS as condições casarem, eleva o risco para um nível.

    Várias condições (AND) permitem capturar escopo composto — ex.:
    'destinatário externo' E 'tem anexo' → CRITICAL.
    """

    condicoes: list[Condition]
    eleva_para: RiskLevel
    motivo: str

    def aplica(self, argumentos: dict) -> bool:
        return all(c.aplica(argumentos) for c in self.condicoes)


class ToolRiskPolicy(_PolicyModel):
    """A política de risco de UMA tool: risco base + escaladores."""

    risco_base: RiskLevel
    escaladores: list[RiskEscalator] = []


class Invariante(_PolicyModel):
    """Um invariante de FORMAÇÃO — o piso inviolável (doutrina §6.1).

    Comportamento de formação não é prompt; é invariante mecânico. Se TODAS as
    condições casarem com os argumentos, a chamada é PROIBIDA — independente de
    quem pediu (inclusive o gestor do cliente), em QUALQUER modo (observe e
    enforce; o piso não tem dry-run) e NÃO é aprovável pela fila do cliente
    (formação só muda via Nexxian/Git).

    `soul_behavior_id` é a linhagem ao comportamento do SOUL que o motiva — a
    formação é a fonte, o invariante é a materialização. O wiring valida que
    esse id existe no SOUL carregado (invariante órfão = erro de config).
    """

    tool: str
    condicoes: list[Condition]
    soul_behavior_id: str
    mensagem: str

    def aplica(self, argumentos: dict) -> bool:
        return all(c.aplica(argumentos) for c in self.condicoes)


class RiskPolicy(_PolicyModel):
    """Política completa: o mapa tool → ToolRiskPolicy que o engine interpreta."""

    tools: dict[str, ToolRiskPolicy]
    invariantes: list[Invariante] = []

    def violacao(self, tool: str, argumentos: dict) -> Invariante | None:
        """O primeiro invariante de formação violado por esta chamada, ou None.

        Predicados ausentes nunca disparam — conservador como nos escaladores.
        """
        for inv in self.invariantes:
            if inv.tool == tool and inv.aplica(argumentos):
                return inv
        return None

    def avaliar_risco(self, tool: str, argumentos: dict) -> tuple[RiskLevel, list[str]]:
        """Risco da chamada concreta + os porquês (base + escaladores que dispararam).

        Tool sem policy declarada → HIGH com motivo explícito: deny-by-default
        inclina para o cuidado quando não sabemos a política (mas em modo
        observação isso só gera log, não bloqueia).
        """
        tp = self.tools.get(tool)
        if tp is None:
            return RiskLevel.HIGH, [f"tool '{tool}' sem policy declarada (cautela por padrão)"]

        risco = tp.risco_base
        motivos = [f"risco base: {tp.risco_base.value}"]
        for esc in tp.escaladores:
            if esc.aplica(argumentos):
                risco = risco_maximo(risco, esc.eleva_para)
                motivos.append(f"{esc.motivo} → {esc.eleva_para.value}")
        return risco, motivos
