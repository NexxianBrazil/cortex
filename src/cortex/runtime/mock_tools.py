"""Implementações MOCKADAS das tools do catálogo de exemplo (Fase 2).

Nada aqui integra sistema real — são dados de brinquedo, determinísticos e
plausíveis, suficientes para o loop fechar o circuito pedido→execução→
resultado. As integrações reais (SAP/SQL, e-mail) chegam na Fase 5 e vão
substituir estas funções no registry sem tocar no loop.
"""

from collections.abc import Mapping

from cortex.identity.models import ToolDeclaration
from cortex.runtime.tools import ToolRegistry

# Tabela de preços de brinquedo — determinística por design (sem random).
_PRECOS_FAKE: dict[str, float] = {
    "PRD-001": 1250.00,
    "PRD-002": 380.50,
    "PRD-003": 47.90,
}
_PRECO_PADRAO = 99.90


def _consultar_preco(codigo_produto: str, quantidade: int | None = None) -> dict:
    """Devolve preço de tabela fake; sempre disponível, sempre o mesmo valor."""
    preco = _PRECOS_FAKE.get(codigo_produto, _PRECO_PADRAO)
    return {
        "codigo_produto": codigo_produto,
        "preco_unitario": preco,
        "moeda": "BRL",
        "disponivel": True,
        "quantidade_consultada": quantidade,
    }


def _emitir_cotacao(
    cliente_id: str,
    itens: list,
    condicao_pagamento: str,
    valor_total: float | None = None,
    validade_dias: int = 15,
) -> dict:
    """Gera um número de cotação fake e determinístico (sem estado, sem banco)."""
    return {
        "numero_cotacao": f"COT-{cliente_id}-0001",
        "cliente_id": cliente_id,
        "total_itens": len(itens),
        "condicao_pagamento": condicao_pagamento,
        "valor_total": valor_total,
        "validade_dias": validade_dias,
        "status": "emitida",
    }


def _enviar_email(
    destinatario: str,
    assunto: str,
    corpo: str,
    anexos: list | None = None,
    promete_condicao: bool = False,
    cotacao_emitida: bool = False,
) -> dict:
    """Finge enviar o e-mail; nenhuma mensagem sai de verdade.

    `promete_condicao`/`cotacao_emitida` existem para a política de governança
    (invariante nao_prometer_sem_lastro) — não alteram o envio fake.
    """
    return {
        "status": "enviado",
        "destinatario": destinatario,
        "assunto": assunto,
        "total_anexos": len(anexos or []),
        "message_id": "MSG-FAKE-0001",
    }


_IMPLEMENTACOES = {
    "consultar_preco": _consultar_preco,
    "emitir_cotacao": _emitir_cotacao,
    "enviar_email": _enviar_email,
}


def criar_registry_mock(declaracoes: Mapping[str, ToolDeclaration]) -> ToolRegistry:
    """Monta o registry com as implementações fake das tools declaradas.

    Registra apenas as tools que existem TANTO no catálogo da persona QUANTO
    no conjunto de mocks — assim uma persona com catálogo diferente não
    quebra, só fica sem implementação para as tools não mockadas.
    """
    registry = ToolRegistry(declaracoes)
    for nome, func in _IMPLEMENTACOES.items():
        if nome in declaracoes:
            registry.registrar(nome, func)
    return registry
