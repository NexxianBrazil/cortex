"""Modelos do system of record (Fase 5b) — o domínio comercial mínimo.

O suficiente para a Mariana operar: preço de produto e cadastro de cliente.
Campos extras são PROIBIDOS (`extra="forbid"`): uma resposta da API com campo
inesperado vira erro explícito em vez de passar despercebida.
"""

from pydantic import BaseModel, ConfigDict


class _SORModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PrecoSOR(_SORModel):
    """Preço e disponibilidade de um produto, vindos AO VIVO do system of record."""

    codigo_produto: str
    preco_unitario: float
    moeda: str = "BRL"
    disponivel: bool = True


class ClienteSOR(_SORModel):
    """Cadastro comercial de um cliente — dado vivo (limite/condição mudam na fonte)."""

    cliente_id: str
    razao_social: str
    limite_credito: float
    condicao_pagamento_padrao: str
    bloqueado: bool = False
