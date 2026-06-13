"""Servidor HTTP do Cortex (Fase 7a) — o gateway de mensagens para bridges.

`cortex servir` sobe este app: um bridge confiável (n8n/Evolution/Cloud API)
chama POST /v1/mensagens; o Cortex resolve a IDENTIDADE do remetente pelo canal
(canais.yaml), processa um turno (serializado por lock — engine não é
thread-safe) e responde. A 7c pluga o WhatsApp real por trás deste endpoint.
"""

from cortex.server.app import MensagemEntrada, MensagemResposta, criar_app
from cortex.server.identidade import (
    CanaisError,
    carregar_mapa_identidades,
    resolver_identidade,
)
from cortex.server.sessoes import GerenciadorSessoes

__all__ = [
    "CanaisError",
    "GerenciadorSessoes",
    "MensagemEntrada",
    "MensagemResposta",
    "carregar_mapa_identidades",
    "criar_app",
    "resolver_identidade",
]
