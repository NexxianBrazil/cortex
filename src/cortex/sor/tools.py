"""Tools vivas sobre o system of record (Fase 5b).

`consultar_preco` e `consultar_cliente` consultam o gateway AO VIVO a cada
chamada. Dado vivo nunca vira crença (Plano 4): estas tools ficam de fora dos
extratores de promoção (ver runtime/promotion.py) — o que persiste é o rastro
da consulta no audit, não o valor congelado.

Distinguem explicitamente "não há registro" (`encontrado: false`) de erro
(SORIndisponivelError → ToolError tratável): o LLM precisa saber a diferença
entre 'produto não cadastrado' e 'o SAP está fora do ar'.
"""

import logging

from cortex.runtime.tools import ToolError, ToolRegistry
from cortex.sor.gateway import SORGateway, SORIndisponivelError

logger = logging.getLogger("cortex.sor")


class ConsultarPrecoTool:
    """Tool real `consultar_preco` — mesma assinatura/retorno da antiga mock.

    O LLM não percebe a troca: o shape é o mesmo, agora com dado vivo do SOR.
    """

    def __init__(self, gateway: SORGateway) -> None:
        self._gateway = gateway

    def __call__(self, codigo_produto: str, quantidade: int | None = None) -> dict:
        try:
            preco = self._gateway.preco(codigo_produto)
        except SORIndisponivelError as exc:
            logger.warning("consultar_preco: SOR indisponível: %s", exc)
            raise ToolError(f"system of record indisponível ao consultar preço: {exc}") from exc

        if preco is None:
            return {"encontrado": False, "codigo_produto": codigo_produto}
        return {
            "encontrado": True,
            "codigo_produto": preco.codigo_produto,
            "preco_unitario": preco.preco_unitario,
            "moeda": preco.moeda,
            "disponivel": preco.disponivel,
            "quantidade_consultada": quantidade,
        }


class ConsultarClienteTool:
    """Tool real `consultar_cliente` — cadastro comercial vivo do SOR."""

    def __init__(self, gateway: SORGateway) -> None:
        self._gateway = gateway

    def __call__(self, cliente_id: str) -> dict:
        try:
            cliente = self._gateway.cliente(cliente_id)
        except SORIndisponivelError as exc:
            logger.warning("consultar_cliente: SOR indisponível: %s", exc)
            raise ToolError(
                f"system of record indisponível ao consultar cliente: {exc}"
            ) from exc

        if cliente is None:
            return {"encontrado": False, "cliente_id": cliente_id}
        return {
            "encontrado": True,
            "cliente_id": cliente.cliente_id,
            "razao_social": cliente.razao_social,
            "limite_credito": cliente.limite_credito,
            "condicao_pagamento_padrao": cliente.condicao_pagamento_padrao,
            "bloqueado": cliente.bloqueado,
        }


# Tools vivas → classe da implementação. Registradas SOBRE as mocks (Fase 5b).
_TOOLS_SOR = {
    "consultar_preco": ConsultarPrecoTool,
    "consultar_cliente": ConsultarClienteTool,
}


def registrar_tools_sor(registry: ToolRegistry, gateway: SORGateway) -> None:
    """Registra as tools vivas no registry, por cima das mocks.

    Só registra as DECLARADAS no catálogo da persona — uma persona sem essas
    tools não quebra (apenas não ganha acesso ao SOR). O gateway injetado é o
    mesmo para todas — a fonte de dado vivo do Data Plane.
    """
    for nome, cls in _TOOLS_SOR.items():
        if registry.declarada(nome):
            registry.registrar(nome, cls(gateway))
