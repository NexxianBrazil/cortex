"""OpenAICompatProvider — provedor para o protocolo OpenAI (Fase 2).

Por que "compat": o protocolo de chat-completions da OpenAI virou lingua
franca — OpenAI, Ollama, vLLM e a futura LLM interna da Nexxian falam o
mesmo fio. Um único provedor com `base_url` configurável cobre todos: trocar
de OpenAI para a LLM on-premise é mudar OPENAI_BASE_URL, não código.
"""

import json
import logging
from collections.abc import Sequence

from openai import OpenAI

from cortex.identity.models import ToolDeclaration
from cortex.runtime.messages import LLMResponse, Message, Role, ToolCall
from cortex.runtime.providers.base import LLMProvider

logger = logging.getLogger("cortex.runtime")

MODELO_PADRAO = "gpt-4o-mini"

_TIPOS_JSON = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


def _tool_para_openai(tool: ToolDeclaration) -> dict:
    """ToolDeclaration (Fase 1) → function-tool do protocolo OpenAI."""
    propriedades = {
        p.nome: {"type": _TIPOS_JSON.get(p.tipo, "string"), "description": p.descricao}
        for p in tool.parametros
    }
    obrigatorios = [p.nome for p in tool.parametros if p.obrigatorio]
    return {
        "type": "function",
        "function": {
            "name": tool.nome,
            "description": tool.descricao,
            "parameters": {
                "type": "object",
                "properties": propriedades,
                "required": obrigatorios,
            },
        },
    }


def _mensagens_para_openai(system: str, mensagens: Sequence[Message]) -> list[dict]:
    """Histórico interno → messages do protocolo OpenAI (system como mensagem)."""
    convertidas: list[dict] = [{"role": "system", "content": system}]
    for m in mensagens:
        if m.role is Role.USER:
            convertidas.append({"role": "user", "content": m.content})
        elif m.role is Role.ASSISTANT and m.tool_calls:
            convertidas.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.nome,
                                "arguments": json.dumps(tc.argumentos, ensure_ascii=False),
                            },
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        elif m.role is Role.ASSISTANT:
            convertidas.append({"role": "assistant", "content": m.content})
        elif m.role is Role.TOOL:
            convertidas.append(
                {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
            )
    return convertidas


def _parsear_argumentos(bruto: str | None) -> dict:
    """Argumentos vêm como JSON serializado; falha de parse vira dict vazio.

    Não derrubamos o processo por JSON malformado do modelo — o registry vai
    acusar argumentos inválidos e o erro volta tratável para o LLM corrigir.
    """
    if not bruto:
        return {}
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        logger.warning("argumentos de tool com JSON inválido: %r", bruto)
        return {}
    return dados if isinstance(dados, dict) else {}


class OpenAICompatProvider(LLMProvider):
    """Fala o protocolo OpenAI (OpenAI, Ollama, vLLM, LLM interna)."""

    def __init__(
        self,
        modelo: str = MODELO_PADRAO,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._modelo = modelo
        # Servidores locais (Ollama/vLLM) não validam chave, mas o SDK exige
        # uma string — usamos um placeholder explícito quando não há chave.
        kwargs: dict = {}
        if api_key:
            kwargs["api_key"] = api_key
        elif base_url:
            kwargs["api_key"] = "sem-chave-servidor-local"
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)

    def gerar(
        self,
        system: str,
        mensagens: Sequence[Message],
        tools: Sequence[ToolDeclaration],
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self._modelo,
            "messages": _mensagens_para_openai(system, mensagens),
        }
        if tools:
            kwargs["tools"] = [_tool_para_openai(t) for t in tools]

        resposta = self._client.chat.completions.create(**kwargs)
        escolha = resposta.choices[0].message

        tool_calls = [
            ToolCall(
                id=tc.id,
                nome=tc.function.name,
                argumentos=_parsear_argumentos(tc.function.arguments),
            )
            for tc in (escolha.tool_calls or [])
        ]
        return LLMResponse(texto=escolha.content, tool_calls=tool_calls)
