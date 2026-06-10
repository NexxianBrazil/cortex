"""Fase 4 — Governança: Decision Engine, ceticismo, justificação, learning queue.

Fase 4a (esta entrega): o Decision Engine — risk-by-scope, policy-as-data, em
modo OBSERVAÇÃO por default. Avalia o risco de cada chamada concreta de tool a
partir de uma RiskPolicy declarativa e registra o veredito; em observe, deixa
executar (para calibrar); em enforce, barra MEDIUM+ (roteia para aprovação,
cuja fila real é a 4c). Unificar o ceticismo da memória (4b) e a Learning
Queue + Audit (4c) vêm depois.
"""

from cortex.governance.audit import AuditTrail
from cortex.governance.engine import (
    Decision,
    DecisionEngine,
    DecisionMode,
    InvarianteSemLinhagemError,
    Verdict,
)
from cortex.governance.example_policy import (
    construir_policy_exemplo,
)
from cortex.governance.policy import (
    Condition,
    Invariante,
    Operador,
    RiskEscalator,
    RiskPolicy,
    ToolRiskPolicy,
)

__all__ = [
    "AuditTrail",
    "Condition",
    "Decision",
    "DecisionEngine",
    "DecisionMode",
    "Invariante",
    "InvarianteSemLinhagemError",
    "Operador",
    "RiskEscalator",
    "RiskPolicy",
    "ToolRiskPolicy",
    "Verdict",
    "construir_policy_exemplo",
]
