"""Fase 5b — Systems of Record: o Plano 4 da memória (a verdade VIVA).

Preço, saldo, cadastro e estoque vivem no system of record (SAP/SQL) e são
consultados AO VIVO via tool, NUNCA memorizados — uma cópia apodrece quando o
valor muda na fonte. O acesso passa SEMPRE por uma camada de API intermediária
controlada pela Nexxian (o SORGateway): nunca RAG indexado sobre o banco, nunca
SQL direto da persona. Hierarquia de verdade: System of Record > KB > Semântica.
"""

from cortex.sor.factory import ConfiguracaoGatewayError, criar_gateway
from cortex.sor.gateway import (
    HTTPSORGateway,
    MockSORGateway,
    SORError,
    SORGateway,
    SORIndisponivelError,
)
from cortex.sor.models import ClienteSOR, PrecoSOR
from cortex.sor.tools import (
    ConsultarClienteTool,
    ConsultarPrecoTool,
    registrar_tools_sor,
)
from cortex.sor.truth import GatewaySourceOfTruth

__all__ = [
    "ClienteSOR",
    "ConfiguracaoGatewayError",
    "ConsultarClienteTool",
    "ConsultarPrecoTool",
    "GatewaySourceOfTruth",
    "HTTPSORGateway",
    "MockSORGateway",
    "PrecoSOR",
    "SORError",
    "SORGateway",
    "SORIndisponivelError",
    "criar_gateway",
    "registrar_tools_sor",
]
