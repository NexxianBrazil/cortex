"""O cético confere a fonte VIVA (Fase 5b) — GatewaySourceOfTruth.

Traduz a CHAVE CANÔNICA de um fato verificável numa consulta ao gateway. Honra
a regra de seams.py::SourceOfTruth: o valor volta para a DECISÃO DO MOMENTO,
nunca para virar cache — a crença permanece verifiable=True e será reconferida.

Mapa de padrões de chave (cresce conforme novos domínios entram):
  produto:{codigo}:preco        → gateway.preco(codigo).preco_unitario
  cliente:{id}:limite_credito   → gateway.cliente(id).limite_credito
  cliente:{id}:condicao_pagamento → gateway.cliente(id).condicao_pagamento_padrao
  qualquer outra chave          → found=False (o cético escala — conservador)

Formatação MORA AQUI, não no motor: o cético compara valores por igualdade
textual normalizada (não numérica), então preço/limite voltam em reais
('R$ 1.250,00') para casar com a forma como o humano e a crença escrevem
dinheiro. Indisponibilidade do gateway vira found=False (não consegui conferir
→ o motor escala com cautela).
"""

import re

from cortex.memory.seams import SourceOfTruth, TruthLookup
from cortex.sor.gateway import SORGateway, SORIndisponivelError

_RE_PRECO = re.compile(r"^produto:(?P<codigo>.+):preco$")
_RE_LIMITE = re.compile(r"^cliente:(?P<id>.+):limite_credito$")
_RE_COND = re.compile(r"^cliente:(?P<id>.+):condicao_pagamento$")


def _brl(valor: float) -> str:
    """Formata um float como reais pt-BR: 1250.0 → 'R$ 1.250,00'."""
    s = f"{valor:,.2f}"  # 1,250.00 (estilo en-US)
    return "R$ " + s.replace(",", "_").replace(".", ",").replace("_", ".")


class GatewaySourceOfTruth(SourceOfTruth):
    """Fonte de verdade VIVA: traduz a chave canônica em consulta ao gateway."""

    def __init__(self, gateway: SORGateway) -> None:
        self._gateway = gateway

    def lookup(self, key: str) -> TruthLookup:
        try:
            return self._lookup(key)
        except SORIndisponivelError:
            # Não consegui conferir: o motor trata found=False como 'fonte
            # indisponível' e escala com cautela (memória inalterada).
            return TruthLookup(found=False)

    def _lookup(self, key: str) -> TruthLookup:
        if (m := _RE_PRECO.match(key)) is not None:
            preco = self._gateway.preco(m["codigo"])
            if preco is None:
                return TruthLookup(found=False)
            return TruthLookup(found=True, value=_brl(preco.preco_unitario))

        if (m := _RE_LIMITE.match(key)) is not None:
            cliente = self._gateway.cliente(m["id"])
            if cliente is None:
                return TruthLookup(found=False)
            return TruthLookup(found=True, value=_brl(cliente.limite_credito))

        if (m := _RE_COND.match(key)) is not None:
            cliente = self._gateway.cliente(m["id"])
            if cliente is None:
                return TruthLookup(found=False)
            return TruthLookup(found=True, value=cliente.condicao_pagamento_padrao)

        # Chave fora dos padrões: nada a conferir → não-verificável (escala).
        return TruthLookup(found=False)
