"""Testes do canal de saída (Fase 7c) — LogCanalSaida + EvolutionCanalSaida."""

import json

import httpx
import pytest

from cortex.server.canal_saida import (
    CanalSaidaError,
    EvolutionCanalSaida,
    LogCanalSaida,
)


def test_log_canal_saida_acumula_e_nao_toca_rede():
    canal = LogCanalSaida()
    canal.enviar("5511999990000", "oi")
    canal.enviar("5511999990000", "tudo bem?")
    assert canal.enviados == [
        ("5511999990000", "oi"),
        ("5511999990000", "tudo bem?"),
    ]
    assert canal.nome_canal == "whatsapp"


def test_evolution_monta_request_e_trata_erro():
    capturado = {}

    def handler(request: httpx.Request) -> httpx.Response:
        capturado["url"] = str(request.url)
        capturado["apikey"] = request.headers.get("apikey")
        capturado["body"] = json.loads(request.content)
        if capturado["body"]["number"] == "ERRO":
            return httpx.Response(500)
        return httpx.Response(200, json={"key": {"id": "MSG1"}})

    canal = EvolutionCanalSaida(
        "http://evo.local",
        "minha-instancia",
        api_key="segredo",
        transport=httpx.MockTransport(handler),
    )

    canal.enviar("5511999990000", "olá")
    assert capturado["url"] == "http://evo.local/message/sendText/minha-instancia"
    assert capturado["apikey"] == "segredo"
    assert capturado["body"] == {"number": "5511999990000", "text": "olá"}

    # 5xx → CanalSaidaError tratável (não derruba quem está a montante).
    with pytest.raises(CanalSaidaError, match="500"):
        canal.enviar("ERRO", "x")
