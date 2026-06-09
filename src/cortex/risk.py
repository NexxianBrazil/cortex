"""Níveis de risco compartilhados entre as camadas do Cortex.

Este módulo vive no topo do pacote (e não dentro de `identity/`) de propósito:
o risco base de uma tool nasce na camada de identidade (Fase 1), mas é a
Decision Engine (Fase 4) quem vai consumi-lo para decidir entre executar,
pedir aprovação ou escalar. Um único enum compartilhado evita que cada fase
invente sua própria escala e elas divirjam.
"""

from enum import StrEnum


class RiskLevel(StrEnum):
    """Escala canônica de risco de uma ação.

    Os valores são minúsculos porque é assim que aparecem nos arquivos de
    formação (YAML editado por humanos); os nomes maiúsculos são a forma
    usada no código.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def ordem(self) -> int:
        """Posição na escala (0=LOW … 3=CRITICAL).

        Necessária porque StrEnum compara pelos VALORES (alfabético:
        critical<high<low<medium — sem sentido). A Decision Engine (Fase 4)
        precisa ordenar e tomar o máximo de riscos, então a ordem é explícita.
        """
        return _ORDEM[self]


_ORDEM = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def risco_maximo(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    """O mais alto entre dois riscos — base do empilhamento de escaladores."""
    return a if a.ordem >= b.ordem else b
