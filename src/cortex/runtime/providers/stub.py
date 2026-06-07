"""StubProvider — LLM falso e DETERMINÍSTICO (Fase 2).

Por que existe: o CI precisa exercitar o loop completo (pedido de tool →
execução → resultado → resposta final) sem rede, sem chave e sem custo.
O stub segue um roteiro pré-programado injetado pelo teste, então o
comportamento é 100% reprodutível.
"""

from collections.abc import Sequence

from cortex.identity.models import ToolDeclaration
from cortex.runtime.messages import LLMResponse, Message
from cortex.runtime.providers.base import LLMProvider


class StubProvider(LLMProvider):
    """Segue um roteiro fixo de respostas, uma por chamada.

    `repetir_ultimo=True` faz o stub repetir a última resposta para sempre —
    útil para simular um LLM que pede tool infinitamente (teste do teto de
    iterações) e para o modo demo do CLI.
    """

    def __init__(self, roteiro: Sequence[LLMResponse], repetir_ultimo: bool = False) -> None:
        if not roteiro:
            raise ValueError("StubProvider exige um roteiro com pelo menos uma resposta")
        self._roteiro = list(roteiro)
        self._repetir_ultimo = repetir_ultimo
        self._proxima = 0
        # Tudo que o stub recebeu, para os testes inspecionarem o circuito:
        # cada item é (system, mensagens, tools) de uma chamada a gerar().
        self.chamadas: list[tuple[str, list[Message], list[ToolDeclaration]]] = []

    def gerar(
        self,
        system: str,
        mensagens: Sequence[Message],
        tools: Sequence[ToolDeclaration],
    ) -> LLMResponse:
        self.chamadas.append((system, list(mensagens), list(tools)))
        if self._proxima >= len(self._roteiro):
            if self._repetir_ultimo:
                return self._roteiro[-1]
            raise RuntimeError(
                "roteiro do StubProvider esgotado — o teste fez mais chamadas "
                "ao LLM do que o roteiro previa"
            )
        resposta = self._roteiro[self._proxima]
        self._proxima += 1
        return resposta
