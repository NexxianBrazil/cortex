"""ClaudeProvider — provedor concreto para a API da Anthropic (Fase 2).

Tradução de fronteira: converte o formato interno do Cortex para o fio da
Messages API (system separado, tool_use/tool_result em content blocks) e
normaliza a resposta de volta para LLMResponse. Nada da API da Anthropic
vaza para fora deste módulo.

A chave NUNCA é embutida em código: vem da config (que lê ANTHROPIC_API_KEY
do ambiente/.env) ou da resolução padrão do próprio SDK.
"""

from collections.abc import Sequence

import anthropic

from cortex.identity.models import ToolDeclaration
from cortex.runtime.messages import LLMResponse, Message, Role, TokenUsage, ToolCall
from cortex.runtime.providers.base import LLMProvider

MODELO_PADRAO = "claude-opus-4-8"

# Mapa tipo declarado na Fase 1 → tipo JSON Schema exigido pela API.
_TIPOS_JSON = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


def _tool_para_claude(tool: ToolDeclaration) -> dict:
    """ToolDeclaration (Fase 1) → schema de tool da Messages API."""
    propriedades = {
        p.nome: {"type": _TIPOS_JSON.get(p.tipo, "string"), "description": p.descricao}
        for p in tool.parametros
    }
    obrigatorios = [p.nome for p in tool.parametros if p.obrigatorio]
    return {
        "name": tool.nome,
        "description": tool.descricao,
        "input_schema": {
            "type": "object",
            "properties": propriedades,
            "required": obrigatorios,
        },
    }


def _mensagens_para_claude(mensagens: Sequence[Message]) -> list[dict]:
    """Histórico interno → messages da API (tool result vira user/tool_result)."""
    convertidas: list[dict] = []
    for m in mensagens:
        if m.role is Role.USER:
            convertidas.append({"role": "user", "content": m.content})
        elif m.role is Role.ASSISTANT and m.tool_calls:
            blocos: list[dict] = []
            if m.content:
                blocos.append({"type": "text", "text": m.content})
            blocos.extend(
                {"type": "tool_use", "id": tc.id, "name": tc.nome, "input": tc.argumentos}
                for tc in m.tool_calls
            )
            convertidas.append({"role": "assistant", "content": blocos})
        elif m.role is Role.ASSISTANT:
            convertidas.append({"role": "assistant", "content": m.content})
        elif m.role is Role.TOOL:
            convertidas.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id,
                            "content": m.content,
                            "is_error": m.erro,
                        }
                    ],
                }
            )
    return convertidas


class ClaudeProvider(LLMProvider):
    """Fala com a API da Anthropic via SDK oficial."""

    def __init__(
        self,
        modelo: str = MODELO_PADRAO,
        api_key: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self._modelo = modelo
        self._max_tokens = max_tokens
        # Sem api_key explícita, o SDK resolve ANTHROPIC_API_KEY do ambiente.
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def gerar(
        self,
        system: str,
        mensagens: Sequence[Message],
        tools: Sequence[ToolDeclaration],
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self._modelo,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": _mensagens_para_claude(mensagens),
        }
        if tools:
            kwargs["tools"] = [_tool_para_claude(t) for t in tools]

        resposta = self._client.messages.create(**kwargs)

        # Normalização: content blocks → (texto, tool_calls) internos.
        textos = [b.text for b in resposta.content if b.type == "text"]
        tool_calls = [
            ToolCall(id=b.id, nome=b.name, argumentos=dict(b.input))
            for b in resposta.content
            if b.type == "tool_use"
        ]
        texto = "\n".join(textos) if textos else None
        uso = None
        if resposta.usage is not None:
            uso = TokenUsage(
                input_tokens=resposta.usage.input_tokens,
                output_tokens=resposta.usage.output_tokens,
            )
        return LLMResponse(texto=texto, tool_calls=tool_calls, uso=uso)
