"""Interface provider-agnostic de LLM (Fase 2).

Decisão de arquitetura: o Cortex NUNCA fala diretamente com um SDK de LLM.
Todo acesso passa por esta interface — assim a escolha de provedor (Claude,
OpenAI-compat, stub determinístico) é uma decisão de CONFIGURAÇÃO da Nexxian,
não de código. O loop do agente só conhece `LLMProvider.gerar`.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from cortex.identity.models import ToolDeclaration
from cortex.runtime.messages import LLMResponse, Message


class LLMProvider(ABC):
    """Contrato único que todo provedor concreto implementa."""

    @abstractmethod
    def gerar(
        self,
        system: str,
        mensagens: Sequence[Message],
        tools: Sequence[ToolDeclaration],
    ) -> LLMResponse:
        """Gera a próxima resposta do LLM.

        Recebe o system prompt, o histórico no formato interno e as tools
        disponíveis (as ToolDeclaration da Fase 1 — declarações, não
        implementações). Devolve SEMPRE uma LLMResponse normalizada: ou um
        pedido de tool-call, ou texto. O chamador nunca vê o fio do provedor.
        """
