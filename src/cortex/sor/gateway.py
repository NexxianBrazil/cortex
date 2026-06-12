"""O SOR Gateway (Fase 5b) — a camada de API intermediária da Nexxian.

DOUTRINA (não viole): a persona NUNCA fala SQL nem toca o banco do cliente. Todo
acesso a dado vivo (preço, cadastro, saldo) passa por ESTA camada intermediária,
operada pela Nexxian — não há RAG indexado sobre o banco do SAP nem query direta
da persona. Só chamadas de LEITURA a uma API controlada, auditáveis e revogáveis.

Dado vivo nunca é memorizado (Plano 4): consulta-se ao vivo a cada decisão. Ver
memory/seams.py::SourceOfTruth para a regra do 'valor que apodrece'.
"""

from abc import ABC, abstractmethod

import httpx

from cortex.sor.models import ClienteSOR, PrecoSOR


class SORError(Exception):
    """Base dos erros do gateway."""


class SORIndisponivelError(SORError):
    """A API intermediária falhou (timeout, 5xx, 4xx que não 404).

    Erro TRATÁVEL por design: a tool o converte em erro legível e o LLM informa
    o usuário — indisponibilidade do SAP NÃO derruba o turno.
    """


class SORGateway(ABC):
    """Contrato da camada intermediária: leitura de dado vivo por chave de domínio.

    `None` significa NÃO HÁ REGISTRO (produto/cliente inexistente) — distinto de
    erro (indisponibilidade vira SORIndisponivelError). As implementações são
    trocáveis por config: mock no CI/dev, HTTP contra a API real em produção.
    """

    @abstractmethod
    def preco(self, codigo_produto: str) -> PrecoSOR | None:
        """Preço/disponibilidade do produto; None se não há registro."""

    @abstractmethod
    def cliente(self, cliente_id: str) -> ClienteSOR | None:
        """Cadastro do cliente; None se não há registro."""


# Dados de brinquedo — vivem SÓ aqui agora (migrados do antigo mock_tools.py).
# Uma fonte única de dados fake para todo o desenvolvimento/CI.
_PRECOS_FAKE: dict[str, float] = {
    "PRD-001": 1250.00,
    "PRD-002": 380.50,
    "PRD-003": 47.90,
}
_CLIENTES_FAKE: dict[str, ClienteSOR] = {
    "CLI-001": ClienteSOR(
        cliente_id="CLI-001",
        razao_social="ABC Comércio Ltda",
        limite_credito=50000.00,
        condicao_pagamento_padrao="28 DDL",
        bloqueado=False,
    ),
    "CLI-002": ClienteSOR(
        cliente_id="CLI-002",
        razao_social="Inadimplentes S/A",
        limite_credito=0.00,
        condicao_pagamento_padrao="à vista",
        bloqueado=True,
    ),
    "CLI-003": ClienteSOR(
        cliente_id="CLI-003",
        razao_social="Construtora Delta ME",
        limite_credito=15000.00,
        condicao_pagamento_padrao="42 DDL",
        bloqueado=False,
    ),
}


class MockSORGateway(SORGateway):
    """Gateway determinístico para CI/dev — a fonte única de dados de brinquedo.

    Default de desenvolvimento. `precos`/`clientes` permitem cenários de teste
    (ex.: um SAP que devolve outro preço para exercitar o cético).
    """

    def __init__(
        self,
        precos: dict[str, float] | None = None,
        clientes: dict[str, ClienteSOR] | None = None,
    ) -> None:
        self._precos = dict(_PRECOS_FAKE if precos is None else precos)
        self._clientes = dict(_CLIENTES_FAKE if clientes is None else clientes)

    def preco(self, codigo_produto: str) -> PrecoSOR | None:
        if codigo_produto not in self._precos:
            return None
        return PrecoSOR(
            codigo_produto=codigo_produto,
            preco_unitario=self._precos[codigo_produto],
            moeda="BRL",
            disponivel=True,
        )

    def cliente(self, cliente_id: str) -> ClienteSOR | None:
        return self._clientes.get(cliente_id)


class HTTPSORGateway(SORGateway):
    """Gateway HTTP contra a API intermediária — fala o protocolo, não o banco.

    `GET {base_url}/v1/precos/{codigo}` e `GET {base_url}/v1/clientes/{id}`, com
    `Authorization: Bearer {token}`. 404 → None (não há registro); qualquer outra
    falha (timeout, 4xx, 5xx) → SORIndisponivelError (erro tratável).

    DÍVIDA ANOTADA: timeout curto (5s) e SEM retry/circuit breaker nesta fase —
    resiliência a flapping do SAP é trabalho de uma fase futura. `transport`
    permite injetar httpx.MockTransport nos testes (HTTP real sem rede).
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, headers=headers, transport=transport
        )

    def _get(self, path: str) -> dict | None:
        try:
            resposta = self._client.get(path)
        except httpx.HTTPError as exc:  # timeout, conexão, etc.
            raise SORIndisponivelError(f"falha ao consultar o SOR em {path}: {exc}") from exc
        if resposta.status_code == 404:
            return None
        if resposta.status_code >= 400:
            raise SORIndisponivelError(
                f"system of record retornou {resposta.status_code} em {path}"
            )
        return resposta.json()

    def preco(self, codigo_produto: str) -> PrecoSOR | None:
        dados = self._get(f"/v1/precos/{codigo_produto}")
        return PrecoSOR(**dados) if dados is not None else None

    def cliente(self, cliente_id: str) -> ClienteSOR | None:
        dados = self._get(f"/v1/clientes/{cliente_id}")
        return ClienteSOR(**dados) if dados is not None else None
