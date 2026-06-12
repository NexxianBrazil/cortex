"""A tool real `consultar_kb` (Fase 5a) — RAG como método de acesso.

Devolve trechos da KB com fonte/autoridade/vigência. Distingue explicitamente
"nada relevante na KB" de "não procurei": o LLM precisa saber a diferença entre
'não há política' e 'não consultei'. RAG é ACESSO — o resultado NÃO vira memória
(consultar_kb está fora dos extratores de promoção; ver promotion.py).
"""

import logging

from cortex.knowledge.index import KBIndexError, KnowledgeBase

logger = logging.getLogger("cortex.knowledge")


class ConsultarKBTool:
    """Callable plugável no ToolRegistry: fecha sobre uma KnowledgeBase.

    KB sem diretório/índice não derruba a operação: devolve o aviso de KB vazia
    (um deploy sem KB curada não pode travar a persona).
    """

    def __init__(self, kb: KnowledgeBase) -> None:
        self._kb = kb

    def __call__(self, pergunta: str, dominio: str | None = None) -> dict:
        try:
            resultados = self._kb.buscar(pergunta, dominio=dominio)
        except KBIndexError as exc:
            logger.warning("consultar_kb sem índice utilizável: %s", exc)
            return {"resultados": [], "aviso": "KB não indexada ou indisponível"}

        if not resultados:
            # Explícito: 'não há nada relevante' ≠ 'não procurei'.
            return {"resultados": [], "aviso": "nada relevante na KB"}

        return {
            "resultados": [
                {
                    "texto": r.texto,
                    "fonte": f"{r.arquivo} — {r.titulo}",
                    "autoridade": r.autoridade,
                    "vigencia": {
                        "desde": r.vigente_desde.isoformat(),
                        "ate": r.vigente_ate.isoformat() if r.vigente_ate else None,
                    },
                    "score": r.score,
                }
                for r in resultados
            ]
        }
