"""Testes LIVE dos provedores reais — PULADOS sem chave no ambiente.

O CI nunca falha por falta de chave: cada teste é skip se a variável de
ambiente correspondente não existir. São smoke tests mínimos (1 chamada
curta) só para provar que a tradução de fio funciona contra a API real.
"""

import os

import pytest

from cortex.runtime import Message, Role

requer_anthropic = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY não definida — teste live pulado",
)
requer_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY não definida — teste live pulado",
)


@requer_anthropic
def test_claude_provider_responde_texto():
    from cortex.runtime import ClaudeProvider

    provider = ClaudeProvider(max_tokens=32)
    resposta = provider.gerar(
        system="Responda exatamente a palavra 'ok', nada mais.",
        mensagens=[Message(role=Role.USER, content="diga ok")],
        tools=[],
    )
    assert resposta.texto
    assert not resposta.pediu_tool


@requer_openai
def test_openai_provider_responde_texto():
    from cortex.runtime import OpenAICompatProvider

    provider = OpenAICompatProvider(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL"),
    )
    resposta = provider.gerar(
        system="Responda exatamente a palavra 'ok', nada mais.",
        mensagens=[Message(role=Role.USER, content="diga ok")],
        tools=[],
    )
    assert resposta.texto
    assert not resposta.pediu_tool
