"""Fase 1 — Camada de identidade (SOUL/AGENTS/USER).

Esta camada entrega o "profissional formado, ainda não alocado": os arquivos
de formação em `personas/` são parseados para objetos tipados que dizem QUEM
a persona é e COMO ela trabalha. Memória, governança e execução vêm nas
fases seguintes e consomem estes modelos.
"""

from cortex.identity.models import (
    AuthorityBlock,
    Behavior,
    Colleague,
    EscalationPoint,
    Manager,
    Persona,
    Playbook,
    PlaybookStep,
    Soul,
    ToolDeclaration,
    ToolParameter,
    User,
)
from cortex.identity.parser import (
    PersonaParseError,
    ReferenciaInvalidaError,
    carregar_persona,
    carregar_playbook,
    carregar_soul,
    carregar_tools,
    carregar_user,
)

__all__ = [
    "AuthorityBlock",
    "Behavior",
    "Colleague",
    "EscalationPoint",
    "Manager",
    "Persona",
    "PersonaParseError",
    "Playbook",
    "PlaybookStep",
    "ReferenciaInvalidaError",
    "Soul",
    "ToolDeclaration",
    "ToolParameter",
    "User",
    "carregar_persona",
    "carregar_playbook",
    "carregar_soul",
    "carregar_tools",
    "carregar_user",
]
