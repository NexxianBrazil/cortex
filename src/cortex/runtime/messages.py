"""Formato interno único de mensagens e respostas de LLM (Fase 2).

Por que um formato próprio: cada provedor (Anthropic, OpenAI, stub) tem seu
formato de fio. O loop não pode depender de nenhum deles — os provedores
traduzem de/para ESTE formato na fronteira, e o resto do Cortex só conhece
estes tipos. Trocar de provedor não toca o loop.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Role(StrEnum):
    """Papéis canônicos do histórico interno."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """Pedido de execução de tool feito pelo LLM, já normalizado.

    O `id` correlaciona o pedido com o resultado — Anthropic e OpenAI exigem
    essa correlação no fio, então a preservamos no formato interno.
    """

    id: str
    nome: str
    argumentos: dict = Field(default_factory=dict)


class Message(BaseModel):
    """Uma mensagem do histórico interno da sessão.

    - role=USER: entrada do usuário.
    - role=ASSISTANT: resposta do LLM; se `tool_calls` não for vazio, é um
      pedido de execução de tools (o `content` pode trazer texto parcial).
    - role=TOOL: resultado de uma tool executada pelo runtime; `tool_call_id`
      aponta para o pedido correspondente e `erro` marca falha tratável.
    """

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    nome_tool: str | None = None
    erro: bool = False


class LLMResponse(BaseModel):
    """Resposta normalizada de QUALQUER provedor.

    Ou o LLM pediu tools (`tool_calls` não vazio), ou respondeu em texto
    (`texto`). O loop decide o próximo passo só olhando para isto — nunca
    para o formato bruto do provedor.
    """

    texto: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validar_conteudo(self) -> "LLMResponse":
        if self.texto is None and not self.tool_calls:
            raise ValueError("resposta de LLM precisa ter texto ou tool_calls")
        return self

    @property
    def pediu_tool(self) -> bool:
        """True quando o LLM quer executar tools antes de responder."""
        return bool(self.tool_calls)
