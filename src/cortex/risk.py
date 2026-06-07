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
