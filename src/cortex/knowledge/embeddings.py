"""Embeddings como SEAM (Fase 5a) — mesmo padrão dos providers de LLM.

Interface + Stub determinístico para o CI + implementação real OpenAI-compat.
SOBERANIA: o embedding NÃO pode obrigar dado do cliente a sair. Por isso o
provider é trocável e a opção real fala o protocolo OpenAI — que cobre o
endpoint da OpenAI E o Ollama LOCAL (o caminho on-prem), sem mudar código.
"""

import hashlib
import math
import re
from abc import ABC, abstractmethod

from cortex.memory.text import normalizar

# Dimensão do vetor do StubEmbedder — fixa e arbitrária; só precisa ser estável.
_STUB_DIM = 256


class EmbeddingProvider(ABC):
    """Contrato: transforma textos em vetores. Implementações são trocáveis."""

    @abstractmethod
    def embed(self, textos: list[str]) -> list[list[float]]:
        """Devolve um vetor por texto, na mesma ordem."""


def _tokenizar(texto: str) -> list[str]:
    """Tokens normalizados (>=2 chars) — reusa a normalização da memória."""
    return [t for t in re.split(r"[^0-9a-zà-ÿ]+", normalizar(texto)) if len(t) >= 2]


def _normalizar_l2(vetor: list[float]) -> list[float]:
    norma = math.sqrt(sum(x * x for x in vetor))
    if norma == 0:
        return vetor
    return [x / norma for x in vetor]


class StubEmbedder(EmbeddingProvider):
    """Embedder determinístico por FEATURE HASHING — roda no CI, sem rede.

    Cada token vira um índice (hash estável) num vetor de dimensão fixa e
    acumula; normaliza L2. O resultado tem similaridade LEXICAL real: uma query
    que compartilha palavras com o trecho ranqueia mais alto. Não é semântico,
    mas é suficiente para testes significativos e 100% reprodutível.
    """

    def __init__(self, dim: int = _STUB_DIM) -> None:
        self._dim = dim

    def embed(self, textos: list[str]) -> list[list[float]]:
        return [self._embed_um(t) for t in textos]

    def _embed_um(self, texto: str) -> list[float]:
        vetor = [0.0] * self._dim
        for token in _tokenizar(texto):
            h = hashlib.sha1(token.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self._dim
            # Sinal estável a partir do hash → evita que tudo some construtivo.
            sinal = 1.0 if h[4] & 1 else -1.0
            vetor[idx] += sinal
        return _normalizar_l2(vetor)


# Modelo de embedding padrão quando a config não especifica (Ollama-friendly).
MODELO_EMBEDDING_PADRAO = "nomic-embed-text"


class OpenAICompatEmbedder(EmbeddingProvider):
    """Embedder via protocolo OpenAI (/embeddings) — OpenAI ou Ollama local.

    Reusa base_url/key do provider OpenAI-compat. Apontar OPENAI_BASE_URL para
    o Ollama local mantém o dado on-prem — a soberania por config, sem código.
    """

    def __init__(
        self,
        modelo: str = MODELO_EMBEDDING_PADRAO,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        from openai import OpenAI

        self._modelo = modelo
        kwargs: dict = {}
        if api_key:
            kwargs["api_key"] = api_key
        elif base_url:
            kwargs["api_key"] = "sem-chave-servidor-local"
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)

    def embed(self, textos: list[str]) -> list[list[float]]:
        if not textos:
            return []
        resposta = self._client.embeddings.create(model=self._modelo, input=textos)
        return [d.embedding for d in resposta.data]


def cosseno(a: list[float], b: list[float]) -> float:
    """Similaridade de cosseno entre dois vetores (Python puro, MVP)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    produto = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return produto / (na * nb)
