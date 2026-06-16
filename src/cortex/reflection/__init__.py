"""Reflexão batch (Fase 8) — a outra metade do aprendizado (destilação §3).

O Cortex aprende SÍNCRONO no fim do turno (7b). Aqui entra o BATCH: um job que
"revisa o dia", percebe PADRÕES no episódico acumulado e PROPÕE writes — pela
mesma Learning Queue (4b), nunca direto. Thesis aplicado ao batch: supervised
learning, never autonomous mutation. Frequência vira SALIÊNCIA (recência+
frequência), não autoridade; um padrão frequente é hipótese a ratificar.
"""

from cortex.reflection.engine import (
    PropostaReflexao,
    ReflectionEngine,
    RelatorioReflexao,
    aplicar_reflexao,
)

__all__ = [
    "PropostaReflexao",
    "ReflectionEngine",
    "RelatorioReflexao",
    "aplicar_reflexao",
]
