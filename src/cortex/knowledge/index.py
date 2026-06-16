"""Índice e busca da Knowledge Base (Fase 5a).

Chunking por seção de heading, índice persistido em JSON e busca por cosseno em
Python puro. DÍVIDA CONSCIENTE: a busca linear em listas serve para a escala de
MVP (dezenas/centenas de docs); volume real pede um índice vetorial de verdade
(FAISS/pgvector/...) — que entra ATRÁS desta mesma interface KnowledgeBase, sem
o resto do sistema mudar.
"""

import json
import re
from datetime import date
from pathlib import Path

from cortex.knowledge.embeddings import EmbeddingProvider, cosseno
from cortex.knowledge.models import KBChunk, ResultadoKB, vigente
from cortex.knowledge.parser import carregar_documento

# Nome do índice persistido, dentro do diretório da KB.
NOME_INDICE = ".kb_index.json"

# Limites de chunking (caracteres). Seção maior é fatiada com overlap.
_MAX_CHUNK = 1500
_OVERLAP = 200

# Piso de similaridade abaixo do qual consideramos "nada relevante".
PISO_RELEVANCIA = 0.10


class KBIndexError(Exception):
    """Índice ausente ou defasado — instrui o curador a reindexar."""


def _chunk_secoes(corpo: str) -> list[tuple[str | None, str]]:
    """Fatia o corpo por headings markdown (## / ###); seção longa é sub-fatiada.

    Devolve pares (titulo_da_secao, texto). Texto antes do primeiro heading vai
    numa seção None (preâmbulo).
    """
    linhas = corpo.splitlines()
    secoes: list[tuple[str | None, list[str]]] = [(None, [])]
    for linha in linhas:
        m = re.match(r"^#{2,3}\s+(.*)$", linha)
        if m:
            secoes.append((m.group(1).strip(), []))
        else:
            secoes[-1][1].append(linha)

    out: list[tuple[str | None, str]] = []
    for titulo, corpo_linhas in secoes:
        texto = "\n".join(corpo_linhas).strip()
        if not texto:
            continue
        if len(texto) <= _MAX_CHUNK:
            out.append((titulo, texto))
        else:
            out.extend((titulo, pedaco) for pedaco in _fatiar_com_overlap(texto))
    return out


def _fatiar_com_overlap(texto: str) -> list[str]:
    """Fatia um texto longo em janelas de ~_MAX_CHUNK com ~_OVERLAP de overlap."""
    pedacos: list[str] = []
    inicio = 0
    n = len(texto)
    while inicio < n:
        fim = min(inicio + _MAX_CHUNK, n)
        pedacos.append(texto[inicio:fim].strip())
        if fim >= n:
            break
        inicio = fim - _OVERLAP
    return pedacos


class KnowledgeBase:
    """Acesso à KB: indexação (deliberada) e busca bi-temporal (só-vigentes por default)."""

    def __init__(self, kb_path: Path | str, embedder: EmbeddingProvider) -> None:
        self._path = Path(kb_path)
        self._embedder = embedder
        self._indice = self._path / NOME_INDICE

    # ---- indexação (ato deliberado do curador) --------------------------- #

    def _arquivos_md(self) -> list[Path]:
        if not self._path.is_dir():
            return []
        # README.md é doc do curador (gerado pelo scaffold), não conteúdo da KB —
        # não tem frontmatter e não deve ser indexado nem acusar erro de curadoria.
        return sorted(
            p
            for p in self._path.glob("*.md")
            if p.name != NOME_INDICE and p.name.lower() != "readme.md"
        )

    def indexar(self) -> dict:
        """Varre o diretório, parseia, chunka, embeda e grava o índice (do zero).

        Idempotente: recria o índice inteiro. Devolve um resumo (docs, chunks).
        """
        chunks: list[KBChunk] = []
        arquivos = self._arquivos_md()
        for arquivo in arquivos:
            doc = carregar_documento(arquivo)
            for secao, texto in _chunk_secoes(doc.corpo):
                chunks.append(KBChunk(doc=doc, secao=secao, texto=texto))

        textos = [c.texto for c in chunks]
        vetores = self._embedder.embed(textos) if textos else []
        for chunk, vetor in zip(chunks, vetores, strict=True):
            chunk.embedding = vetor

        self._path.mkdir(parents=True, exist_ok=True)
        self._indice.write_text(
            json.dumps([c.model_dump(mode="json") for c in chunks], ensure_ascii=False),
            encoding="utf-8",
        )
        return {"documentos": len(arquivos), "chunks": len(chunks)}

    # ---- carga e checagem de frescor ------------------------------------- #

    def _carregar_indice(self) -> list[KBChunk]:
        if not self._indice.is_file():
            raise KBIndexError(
                f"índice da KB ausente em {self._indice}. Rode `cortex kb indexar` "
                "(indexar é ato deliberado do curador — embedding pode ser remoto)."
            )
        mtime_indice = self._indice.stat().st_mtime
        for arquivo in self._arquivos_md():
            if arquivo.stat().st_mtime > mtime_indice:
                raise KBIndexError(
                    f"índice da KB defasado: {arquivo.name} mudou depois da última "
                    "indexação. Rode `cortex kb indexar`."
                )
        dados = json.loads(self._indice.read_text(encoding="utf-8"))
        return [KBChunk.model_validate(d) for d in dados]

    # ---- busca ----------------------------------------------------------- #

    def buscar(
        self,
        pergunta: str,
        dominio: str | None = None,
        k: int = 4,
        incluir_revogados: bool = False,
        em: date | None = None,
    ) -> list[ResultadoKB]:
        """Top-k trechos por similaridade, BI-TEMPORAL.

        Default só-vigentes (na data `em`, default hoje). `incluir_revogados`
        traz também os revogados, MARCADOS (revogado=True) — o histórico existe,
        mas nunca se passa por vigente. `dominio` filtra por domínio do doc.
        """
        chunks = self._carregar_indice()
        if not chunks:
            return []
        (consulta_vec,) = self._embedder.embed([pergunta])

        candidatos: list[ResultadoKB] = []
        for c in chunks:
            doc = c.doc
            if dominio is not None and doc.dominio != dominio:
                continue
            esta_vigente = vigente(doc, em)
            if not esta_vigente and not incluir_revogados:
                continue
            score = cosseno(consulta_vec, c.embedding or [])
            if score < PISO_RELEVANCIA:
                continue
            candidatos.append(
                ResultadoKB(
                    texto=c.texto,
                    arquivo=doc.arquivo,
                    titulo=doc.titulo,
                    autoridade=doc.autoridade,
                    dominio=doc.dominio,
                    secao=c.secao,
                    vigente_desde=doc.vigente_desde,
                    vigente_ate=doc.vigente_ate,
                    substituido_por=doc.substituido_por,
                    revogado=not esta_vigente,
                    score=round(score, 4),
                )
            )

        # Vigentes primeiro, depois por score — um revogado nunca encobre o vigente.
        candidatos.sort(key=lambda r: (not r.revogado, r.score), reverse=True)
        return candidatos[:k]
