"""Canal de SAÍDA do Cortex (Fase 7c) — a porta por onde o Cortex FALA.

A 7a resolveu o turno SÍNCRONO (pergunta→resposta). Mas o WhatsApp é
assíncrono: a resposta sai por um caminho separado, e a Learning Queue precisa
NOTIFICAR o gestor sem ele ter perguntado. Este é esse caminho de saída.

DOUTRINA: o Cortex fala pela INTERFACE (CanalSaida), agnóstico de provedor —
quem é Evolution (Baileys on-prem, teste) ou Cloud API oficial (cliente) é
detalhe de config. Trocar um pelo outro NÃO toca o Cortex. Soberania: a
Evolution roda no Data Plane do cliente; credenciais via SecretStr/env.
"""

import logging
from abc import ABC, abstractmethod

import httpx

logger = logging.getLogger("cortex.server")


class CanalSaidaError(Exception):
    """Falha ao entregar uma mensagem de saída — TRATÁVEL (entrega é best-effort).

    Entregar não pode derrubar o que está a montante (o turno já foi processado;
    a memória já foi escrita). Quem chama loga e segue. Retry = dívida anotada.
    """


class CanalSaida(ABC):
    """Contrato de saída: o Cortex envia uma mensagem a um destinatário do canal."""

    nome_canal: str = "canal"

    @abstractmethod
    def enviar(self, canal_id: str, texto: str) -> None:
        """Entrega `texto` ao destinatário `canal_id` (telefone, e-mail, ...)."""


class LogCanalSaida(CanalSaida):
    """Canal de saída de CI/dev — não toca a rede; só loga e acumula os envios."""

    nome_canal = "whatsapp"

    def __init__(self) -> None:
        self.enviados: list[tuple[str, str]] = []

    def enviar(self, canal_id: str, texto: str) -> None:
        self.enviados.append((canal_id, texto))
        logger.info("[saída→%s] %s", canal_id, texto)


class EvolutionCanalSaida(CanalSaida):
    """Canal de saída via Evolution API (Baileys on-prem) — WhatsApp de verdade.

    POST {base_url}/message/sendText/{instancia}, header `apikey`, body
    {"number": canal_id, "text": texto}. Timeout 10s, SEM retry (dívida). Falha
    vira CanalSaidaError tratável. `transport` permite httpx.MockTransport nos
    testes (HTTP real sem rede).
    """

    nome_canal = "whatsapp"

    def __init__(
        self,
        base_url: str,
        instancia: str,
        api_key: str | None = None,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._instancia = instancia
        headers = {"apikey": api_key} if api_key else {}
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, headers=headers, transport=transport
        )

    def enviar(self, canal_id: str, texto: str) -> None:
        rota = f"/message/sendText/{self._instancia}"
        try:
            resposta = self._client.post(rota, json={"number": canal_id, "text": texto})
        except httpx.HTTPError as exc:
            logger.error("falha ao enviar pela Evolution (%s): %s", rota, exc)
            raise CanalSaidaError(f"erro de rede ao enviar para {canal_id}: {exc}") from exc
        if resposta.status_code >= 400:
            logger.error("Evolution retornou %s ao enviar para %s", resposta.status_code, canal_id)
            raise CanalSaidaError(
                f"Evolution retornou {resposta.status_code} ao enviar para {canal_id}"
            )


def criar_canal_saida(config) -> CanalSaida:
    """Instancia o canal de saída conforme a config (default 'log', sem rede)."""
    if config.canal_saida == "evolution":
        if not config.evolution_base_url or not config.evolution_instancia:
            raise CanalSaidaError(
                "canal_saida='evolution' exige evolution_base_url e evolution_instancia"
            )
        return EvolutionCanalSaida(
            base_url=config.evolution_base_url,
            instancia=config.evolution_instancia,
            api_key=(
                config.evolution_api_key.get_secret_value()
                if config.evolution_api_key
                else None
            ),
        )
    return LogCanalSaida()
