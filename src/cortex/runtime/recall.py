"""Recuperação de memória ao montar o contexto (Fase 3c).

A ponte de LEITURA entre a memória e o runtime. Antes de responder, o Cortex
consulta o que já sabe e injeta os beliefs ATIVOS relevantes no system prompt
— para "saber o que já sabe".

RELEVÂNCIA NESTA FASE é simples e SEM EMBEDDINGS: sobreposição de palavras
entre o que está sendo conversado e a chave/valor dos beliefs, desempatando
pela SALIÊNCIA (recência+frequência manda no ranking). Capamos em N para a
injeção ser ENXUTA — despejar a memória inteira no prompt estoura contexto, o
anti-pattern que evitamos.

SEAM documentado: a recuperação semântica por embeddings (a serviço da
governança da Fase 4) entra depois e substitui só o ranqueamento abaixo — a
forma de injetar (enxuta, salientes primeiro) permanece.
"""

import re
from collections.abc import Sequence

from cortex.memory.engine import MemoryEngine
from cortex.memory.semantic import Belief


def _tokens(texto: str) -> set[str]:
    """Palavras/códigos significativos (>=2 chars) para casar conversa com beliefs.

    Limiar 2 (não 3) de propósito: códigos de produto/pedido costumam ser
    curtos (P5, AB, 001) e são justamente o que liga a conversa ao belief.
    """
    return {t for t in re.split(r"[^0-9a-zà-ÿ]+", texto.lower()) if len(t) >= 2}


def recuperar_beliefs(
    engine: MemoryEngine, entrada_usuario: str, limite: int = 5
) -> list[Belief]:
    """Devolve até `limite` beliefs ativos mais relevantes ao que se conversa.

    Ranqueia por (sobreposição de palavras com a entrada, saliência), ambos
    desc. Sem nenhuma sobreposição, devolve os mais salientes — um retrato
    enxuto do que o Cortex sabe. `limite=0` desliga a recuperação.
    """
    if limite <= 0:
        return []
    ativos = engine.beliefs_ativos()
    if not ativos:
        return []
    alvo = _tokens(entrada_usuario)

    def ranque(b: Belief) -> tuple[int, float]:
        texto = _tokens(f"{b.key} {b.value}")
        return (len(alvo & texto), b.salience)

    return sorted(ativos, key=ranque, reverse=True)[:limite]


def formatar_beliefs(beliefs: Sequence[Belief]) -> str:
    """Renderiza os beliefs recuperados de forma enxuta para o system prompt."""
    linhas = "\n".join(
        f"- {b.key} = {b.value} (fonte: {b.source.name})" for b in beliefs
    )
    return (
        "## Memória — o que você já sabe\n\n"
        "(apenas os itens mais relevantes/salientes; não é a memória inteira)\n"
        f"{linhas}"
    )
