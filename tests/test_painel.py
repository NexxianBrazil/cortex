"""Testes do painel do operador (Fase 7d) — TestClient + Stub, sem rede.

A FRONTEIRA é o ponto da fase: o painel PROPÕE e APROVA, nunca digita verdade.
Cobrem auth por cookie, leitura da fila, aprovação governada (autor=operador),
o teste-assinatura (nenhuma rota escreve crença/SOUL) e a KB curada.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cortex.config import CortexConfig
from cortex.identity import carregar_persona
from cortex.knowledge import KnowledgeBase
from cortex.knowledge.embeddings import StubEmbedder
from cortex.memory import (
    DictAuthorityMap,
    DictSourceOfTruth,
    HeuristicClassifier,
    InMemoryStore,
    Justification,
    MemoryEngine,
    ProposalStatus,
    Source,
    SourceKind,
)
from cortex.memory.models import Procedencia
from cortex.runtime import AgentLoop, LLMResponse, StubProvider, criar_registry_mock
from cortex.runtime.promotion import DOMINIO_PADRAO
from cortex.server import criar_app

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"
SENHA = "s3nha-do-painel"
ESCRITA = {"POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture(scope="module")
def persona():
    return carregar_persona(PERSONAS_DIR)


def _engine_com_proposta_externa():
    """Motor com UMA proposta pendente de fonte EXTERNA (escalada por contradição)."""
    engine = MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap({DOMINIO_PADRAO: {"Carlos Menezes"}}),
        source_of_truth=DictSourceOfTruth({}),
    )
    engine.observe(
        "cliente:ACME:prazo",
        "30 dias",
        Source(name="Carlos Menezes", kind=SourceKind.HUMAN),
        Justification(why="combinado em reunião"),
        domain=DOMINIO_PADRAO,
    )
    externo = Source(
        name="desconhecido(wpp:+55)", kind=SourceKind.HUMAN, procedencia=Procedencia.EXTERNA
    )
    engine.observe(
        "cliente:ACME:prazo",
        "90 dias",
        externo,
        Justification(why="cliente alegou no WhatsApp"),
        domain=DOMINIO_PADRAO,
    )
    return engine


def _app(persona, engine, tmp_path, *, operador="Carlos Menezes"):
    kb_path = tmp_path / "kb"
    kb_path.mkdir(exist_ok=True)
    kb = KnowledgeBase(kb_path, StubEmbedder())
    config = CortexConfig(painel_senha=SENHA, kb_path=kb_path)
    loop = AgentLoop(
        StubProvider(roteiro=[LLMResponse(texto="ok")], repetir_ultimo=True),
        criar_registry_mock(persona.tools),
    )
    return criar_app(
        persona=persona,
        loop=loop,
        mapa_identidades={},
        token="tok",
        engine=engine,
        config=config,
        kb=kb,
        audit=None,
        painel_operador=operador,
    )


def _logar(client):
    assert client.post("/painel/login", json={"senha": SENHA}).status_code == 200


def test_login_e_protecao_de_rotas(persona, tmp_path):
    client = TestClient(_app(persona, _engine_com_proposta_externa(), tmp_path))
    assert client.get("/painel/api/resumo").status_code == 401  # sem cookie
    assert client.post("/painel/login", json={"senha": "errada"}).status_code == 401
    _logar(client)  # senha certa → cookie
    assert client.get("/painel/api/resumo").status_code == 200


def test_fila_lista_warrant_com_flag_externa(persona, tmp_path):
    engine = _engine_com_proposta_externa()
    client = TestClient(_app(persona, engine, tmp_path))
    _logar(client)
    fila = client.get("/painel/api/fila").json()
    assert fila["total"] == 1
    p = fila["propostas"][0]
    assert p["chave"] == "cliente:ACME:prazo"
    assert p["proposto"] == "90 dias"
    assert p["externa"] is True  # ⚠ fonte externa
    assert p["porque"]  # warrant presente


def test_aprovar_com_razao_vira_decisao_do_operador(persona, tmp_path):
    engine = _engine_com_proposta_externa()
    client = TestClient(_app(persona, engine, tmp_path))
    _logar(client)
    pid = engine.pending_approvals[0].id

    # Sem razão → 400 (a decisão precisa de autor E porquê).
    assert client.post(f"/painel/api/fila/{pid}/aprovar", json={"razao": ""}).status_code == 400

    r = client.post(f"/painel/api/fila/{pid}/aprovar", json={"razao": "confirmei com o cliente"})
    assert r.status_code == 200 and r.json()["por"] == "Carlos Menezes"
    proposta = engine.store.proposal_by_id(pid)
    assert proposta.status is ProposalStatus.APROVADA
    assert proposta.decided_by == "Carlos Menezes"  # episódio com o nome dele

    # Proposta inexistente → 409.
    assert client.post("/painel/api/fila/9999/aprovar", json={"razao": "x"}).status_code == 409


def test_fronteira_nenhuma_rota_escreve_crenca_nem_toca_soul(persona, tmp_path):
    """Teste-assinatura: o painel não tem porta dos fundos para escrever verdade."""
    app = _app(persona, _engine_com_proposta_externa(), tmp_path)
    for route in app.routes:
        path = getattr(route, "path", "").lower()
        metodos = getattr(route, "methods", set()) or set()
        # NENHUMA rota toca o SOUL (formação é da Nexxian, via Git).
        assert "soul" not in path
        # NENHUMA rota de ESCRITA mexe em crença/memória diretamente.
        if metodos & ESCRITA:
            assert "belief" not in path and "crenca" not in path, path
            assert "/memoria" not in path, path  # memória é só leitura (GET)

    # A ÚNICA escrita de memória existente é via aprovar/rejeitar (motor governado).
    escrita_fila = [
        r.path
        for r in app.routes
        if (getattr(r, "methods", set()) or set()) & ESCRITA and "/fila/" in r.path
    ]
    assert any("aprovar" in p for p in escrita_fila)
    assert any("rejeitar" in p for p in escrita_fila)


def test_kb_upload_valido_indexa_e_invalido_explica(persona, tmp_path):
    client = TestClient(_app(persona, _engine_com_proposta_externa(), tmp_path))
    _logar(client)
    valido = (
        "---\ntitulo: Política X\nautoridade: politica_oficial\n"
        "dominio: comercial\nvigente_desde: 2026-01-01\n---\n\nCorpo da política."
    )
    r = client.post("/painel/api/kb/upload", json={"nome": "politica_x.md", "conteudo": valido})
    assert r.status_code == 200 and r.json()["documentos"] == 1

    # Sem frontmatter de curadoria → 400 explicando o que falta.
    r = client.post(
        "/painel/api/kb/upload", json={"nome": "ruim.md", "conteudo": "# só texto, sem frontmatter"}
    )
    assert r.status_code == 400 and "frontmatter" in r.json()["detail"].lower()


def test_operador_nao_autoritativo_recebe_409(persona, tmp_path):
    # Paula Andrade existe no USER.md (colega), mas NÃO é autoritativa no domínio.
    engine = _engine_com_proposta_externa()
    client = TestClient(_app(persona, engine, tmp_path, operador="Paula Andrade"))
    _logar(client)
    pid = engine.pending_approvals[0].id
    r = client.post(f"/painel/api/fila/{pid}/aprovar", json={"razao": "vou aprovar"})
    assert r.status_code == 409  # governança da 4b intacta
    assert engine.store.proposal_by_id(pid).status is ProposalStatus.PENDENTE  # nada mudou


def test_operador_orfao_falha_no_startup(persona, tmp_path):
    with pytest.raises(ValueError, match="não existe no USER.md"):
        _app(persona, _engine_com_proposta_externa(), tmp_path, operador="Fulano Inexistente")


def test_memoria_read_only_e_historico(persona, tmp_path):
    engine = _engine_com_proposta_externa()
    client = TestClient(_app(persona, engine, tmp_path))
    _logar(client)
    mem = client.get("/painel/api/memoria").json()
    assert any(c["key"] == "cliente:ACME:prazo" for c in mem["crencas"])
    hist = client.get("/painel/api/memoria/cliente:ACME:prazo/historico").json()
    assert len(hist["historico"]) >= 1  # a linha bi-temporal da chave
