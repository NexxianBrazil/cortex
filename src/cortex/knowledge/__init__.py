"""Fase 5 — Knowledge: RAG sobre a KB e (futuro) systems of record.

Fase 5a (esta entrega): o Plano 2 da memória — a VERDADE DECLARADA da empresa.
A KB é um diretório de .md com curadoria leve (frontmatter: autoridade,
domínio, vigência), bi-temporal (revogado nunca some — fica marcado) e
consultada via RAG (`consultar_kb`). RAG é método de ACESSO: a KB é consultada,
nunca memorizada. Embeddings são SEAM (Stub no CI; OpenAI/Ollama local).
"""

from cortex.knowledge.embeddings import (
    EmbeddingProvider,
    OpenAICompatEmbedder,
    StubEmbedder,
    cosseno,
)
from cortex.knowledge.factory import ConfiguracaoEmbedderError, criar_embedder
from cortex.knowledge.index import (
    PISO_RELEVANCIA,
    KBIndexError,
    KnowledgeBase,
)
from cortex.knowledge.models import (
    KBChunk,
    KBDocument,
    ResultadoKB,
    vigente,
)
from cortex.knowledge.parser import KBParseError, carregar_documento
from cortex.knowledge.tool import ConsultarKBTool

__all__ = [
    "PISO_RELEVANCIA",
    "ConfiguracaoEmbedderError",
    "ConsultarKBTool",
    "EmbeddingProvider",
    "KBChunk",
    "KBDocument",
    "KBIndexError",
    "KBParseError",
    "KnowledgeBase",
    "OpenAICompatEmbedder",
    "ResultadoKB",
    "StubEmbedder",
    "carregar_documento",
    "cosseno",
    "criar_embedder",
    "vigente",
]
