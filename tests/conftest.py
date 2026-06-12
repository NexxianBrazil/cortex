"""Fixtures compartilhadas dos testes.

`promove_cotacao` registra um EXTRATOR DE TESTE para a máquina de promoção. A
partir da Fase 5b nenhuma tool de PRODUÇÃO promove memória (preço/cadastro são
dado vivo do system of record — Plano 4), então os testes que exercitam o
mecanismo de promoção usam um extrator local sobre `emitir_cotacao`: prova que a
máquina é genérica sem depender de qual tool de produção tem extrator.
"""

import pytest

from cortex.memory import Justification, Source, SourceKind
from cortex.runtime import PromotionCandidate, promotion


@pytest.fixture()
def promove_cotacao(monkeypatch):
    """Adiciona (e remove ao fim) um extrator de teste para `emitir_cotacao`.

    Promove a última cotação emitida como um fato lembrável — um candidato CLARO
    (chave/valor/fonte), suficiente para exercitar promoção, persistência e
    recall sem depender do preço (que, por doutrina, nunca vira crença).
    """

    def _extrator(resultado: dict) -> list[PromotionCandidate]:
        cliente_id = resultado.get("cliente_id")
        numero = resultado.get("numero_cotacao")
        if not cliente_id or not numero:
            return []
        return [
            PromotionCandidate(
                key=f"cliente:{cliente_id}:ultima_cotacao",
                value=numero,
                source=Source(name="emitir_cotacao", kind=SourceKind.TOOL),
                justification=Justification(why="cotação emitida"),
            )
        ]

    monkeypatch.setitem(promotion.EXTRATORES_POR_TOOL, "emitir_cotacao", _extrator)
    return _extrator
