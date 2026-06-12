"""Testes da Fase 5a — Knowledge Base / RAG.

Cobrem o que define a KB como Plano 2 da memória: curadoria obrigatória,
bi-temporalidade (revogado nunca some, mas nunca se passa por vigente), busca
por relevância com o StubEmbedder (lexical, determinístico) e — o invariante
central — RAG é ACESSO, não memória: consultar a KB não promove nada.
"""

import json
import os
from datetime import date

import pytest

from cortex.knowledge import (
    ConsultarKBTool,
    KBIndexError,
    KBParseError,
    KnowledgeBase,
    StubEmbedder,
    carregar_documento,
    vigente,
)
from cortex.knowledge.index import NOME_INDICE
from cortex.knowledge.models import KBDocument
from cortex.runtime.messages import Message, Role
from cortex.runtime.promotion import extrair_candidatos


def _escrever(dir_kb, nome, frontmatter, corpo):
    (dir_kb / nome).write_text(f"---\n{frontmatter}\n---\n\n{corpo}\n", encoding="utf-8")


def _kb_com_dois_docs(tmp_path):
    """KB com um doc comercial (descontos) e um logística (frete), em domínios distintos."""
    _escrever(
        tmp_path,
        "descontos.md",
        "titulo: Política de Desconto\nautoridade: politica_oficial\n"
        "dominio: comercial\nvigente_desde: 2026-01-01",
        "## Desconto máximo\n\nO desconto máximo sem aprovação é de 10 por cento sobre a tabela.",
    )
    _escrever(
        tmp_path,
        "frete.md",
        "titulo: Política de Frete\nautoridade: manual_logistica\n"
        "dominio: logistica\nvigente_desde: 2026-01-01",
        "## Frete grátis\n\nFrete grátis para pedidos acima de mil reais na região sudeste.",
    )
    return KnowledgeBase(tmp_path, StubEmbedder())


# --- parser / curadoria -------------------------------------------------- #


def test_parser_carrega_frontmatter_e_corpo(tmp_path):
    _escrever(
        tmp_path,
        "doc.md",
        "titulo: T\nautoridade: politica_oficial\ndominio: comercial\nvigente_desde: 2026-01-01",
        "## Seção\n\nCorpo do documento.",
    )
    doc = carregar_documento(tmp_path / "doc.md")
    assert doc.titulo == "T"
    assert doc.autoridade == "politica_oficial"
    assert doc.dominio == "comercial"
    assert doc.vigente_desde == date(2026, 1, 1)
    assert doc.arquivo == "doc.md"
    assert "Corpo do documento." in doc.corpo


def test_parser_sem_frontmatter_e_erro_explicito(tmp_path):
    (tmp_path / "cru.md").write_text("# Sem frontmatter\n\ntexto solto", encoding="utf-8")
    with pytest.raises(KBParseError, match="frontmatter ausente"):
        carregar_documento(tmp_path / "cru.md")


def test_parser_campo_obrigatorio_ausente_e_erro(tmp_path):
    # Falta `autoridade` — curadoria é requisito, não opção.
    _escrever(
        tmp_path,
        "incompleto.md",
        "titulo: T\ndominio: comercial\nvigente_desde: 2026-01-01",
        "x",
    )
    with pytest.raises(KBParseError, match="frontmatter inválido"):
        carregar_documento(tmp_path / "incompleto.md")


# --- vigência bi-temporal ------------------------------------------------ #


def _doc(vigente_desde, vigente_ate=None):
    return KBDocument(
        titulo="T",
        autoridade="politica_oficial",
        dominio="comercial",
        vigente_desde=vigente_desde,
        vigente_ate=vigente_ate,
        arquivo="d.md",
        corpo="x",
    )


def test_vigente_dentro_da_janela():
    doc = _doc(date(2026, 1, 1))
    assert vigente(doc, em=date(2026, 6, 12)) is True


def test_vigente_antes_de_comecar_e_falso():
    doc = _doc(date(2026, 7, 1))
    assert vigente(doc, em=date(2026, 6, 12)) is False


def test_vigente_apos_revogacao_e_falso_mas_passado_continua_valido():
    doc = _doc(date(2026, 1, 1), vigente_ate=date(2026, 3, 1))
    assert vigente(doc, em=date(2026, 6, 12)) is False
    # Numa data em que ERA vigente, continua True (memória bi-temporal).
    assert vigente(doc, em=date(2026, 2, 1)) is True


# --- indexação e busca --------------------------------------------------- #


def test_indexar_resume_docs_e_chunks(tmp_path):
    kb = _kb_com_dois_docs(tmp_path)
    resumo = kb.indexar()
    assert resumo["documentos"] == 2
    assert resumo["chunks"] >= 2
    assert (tmp_path / NOME_INDICE).is_file()


def test_buscar_traz_o_trecho_relevante_no_topo(tmp_path):
    kb = _kb_com_dois_docs(tmp_path)
    kb.indexar()
    resultados = kb.buscar("qual o desconto máximo?")
    assert resultados, "esperava pelo menos um resultado relevante"
    assert resultados[0].arquivo == "descontos.md"
    assert resultados[0].revogado is False


def test_buscar_filtra_por_dominio(tmp_path):
    kb = _kb_com_dois_docs(tmp_path)
    kb.indexar()
    resultados = kb.buscar("frete grátis região sudeste", dominio="comercial")
    # O doc de frete é logística — filtrado fora; nada de comercial casa.
    assert all(r.dominio == "comercial" for r in resultados)


def test_buscar_sem_indice_instrui_reindexar(tmp_path):
    kb = _kb_com_dois_docs(tmp_path)  # criado, mas nunca indexado
    with pytest.raises(KBIndexError, match="ausente"):
        kb.buscar("qualquer coisa")


def test_buscar_indice_defasado_e_erro(tmp_path):
    kb = _kb_com_dois_docs(tmp_path)
    kb.indexar()
    # Doc modificado DEPOIS da indexação → índice defasado.
    indice = tmp_path / NOME_INDICE
    futuro = indice.stat().st_mtime + 100
    os.utime(tmp_path / "descontos.md", (futuro, futuro))
    with pytest.raises(KBIndexError, match="defasado"):
        kb.buscar("desconto")


# --- revogação: nunca some, nunca se passa por vigente ------------------- #


def test_revogado_fica_de_fora_por_default_e_aparece_marcado_quando_pedido(tmp_path):
    _escrever(
        tmp_path,
        "antiga.md",
        "titulo: Desconto Antigo\nautoridade: politica_oficial\ndominio: comercial\n"
        "vigente_desde: 2025-01-01\nvigente_ate: 2026-01-01",
        "## Desconto máximo\n\nO desconto máximo sem aprovação era de 20 por cento sobre a tabela.",
    )
    kb = KnowledgeBase(tmp_path, StubEmbedder())
    kb.indexar()

    vigentes = kb.buscar("desconto máximo", em=date(2026, 6, 12))
    assert vigentes == [], "documento revogado não pode aparecer como vigente"

    historico = kb.buscar("desconto máximo", em=date(2026, 6, 12), incluir_revogados=True)
    assert historico, "com incluir_revogados o histórico deve aparecer"
    assert all(r.revogado for r in historico)


def test_vigente_vem_antes_do_revogado_na_ordenacao(tmp_path):
    _escrever(
        tmp_path,
        "nova.md",
        "titulo: Desconto Novo\nautoridade: politica_oficial\n"
        "dominio: comercial\nvigente_desde: 2026-01-01",
        "## Desconto máximo\n\nO desconto máximo sem aprovação é de 10 por cento sobre a tabela.",
    )
    _escrever(
        tmp_path,
        "velha.md",
        "titulo: Desconto Velho\nautoridade: politica_oficial\ndominio: comercial\n"
        "vigente_desde: 2025-01-01\nvigente_ate: 2026-01-01",
        "## Desconto máximo\n\nO desconto máximo sem aprovação era de 20 por cento sobre a tabela.",
    )
    kb = KnowledgeBase(tmp_path, StubEmbedder())
    kb.indexar()
    resultados = kb.buscar("desconto máximo", em=date(2026, 6, 12), incluir_revogados=True)
    assert resultados[0].revogado is False  # vigente nunca é encoberto pelo revogado


# --- a tool consultar_kb ------------------------------------------------- #


def test_tool_distingue_nada_relevante_de_nao_indexada(tmp_path):
    kb = _kb_com_dois_docs(tmp_path)
    tool = ConsultarKBTool(kb)

    # Sem índice: degrada para aviso, não derruba a persona.
    sem_indice = tool("desconto")
    assert sem_indice["resultados"] == []
    assert "não indexada" in sem_indice["aviso"]

    kb.indexar()
    # Indexada, mas pergunta sem casamento lexical algum.
    nada = tool("xyzzy plugh quux inexistente")
    assert nada["resultados"] == []
    assert nada["aviso"] == "nada relevante na KB"

    # Indexada e relevante: traz fonte, autoridade e vigência.
    achou = tool("qual o desconto máximo?")
    assert achou["resultados"]
    top = achou["resultados"][0]
    assert top["autoridade"] == "politica_oficial"
    assert "descontos.md" in top["fonte"]
    assert top["vigencia"]["desde"] == "2026-01-01"


def test_consultar_kb_nao_vira_memoria(tmp_path):
    """RAG é ACESSO: um resultado de consultar_kb não pode ser promovido a belief."""
    resultado_tool = {
        "resultados": [
            {"texto": "desconto máximo de 10%", "fonte": "descontos.md — Política", "score": 0.9}
        ]
    }
    msg = Message(
        role=Role.TOOL,
        nome_tool="consultar_kb",
        content=json.dumps(resultado_tool),
    )
    assert extrair_candidatos([msg]) == []
