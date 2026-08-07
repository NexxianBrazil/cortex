"""Testes do painel do operador (Fase 7d) — TestClient + Stub, sem rede.

A FRONTEIRA é o ponto da fase: o painel PROPÕE e APROVA, nunca digita verdade.
Cobrem auth por cookie, leitura da fila, aprovação governada (autor=operador),
o teste-assinatura (nenhuma rota escreve crença/SOUL) e a KB curada.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cortex.config import CortexConfig
from cortex.governance.audit import AuditTrail
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


def _app(
    persona,
    engine,
    tmp_path,
    *,
    operador="Carlos Menezes",
    deploy_dir=None,
    senha_mestre=None,
    personas_dir=None,
    audit=None,
):
    kb_path = tmp_path / "kb"
    kb_path.mkdir(exist_ok=True)
    kb = KnowledgeBase(kb_path, StubEmbedder())
    config = CortexConfig(
        painel_senha=SENHA,
        kb_path=kb_path,
        painel_senha_mestre=senha_mestre,
        personas_dir=personas_dir or PERSONAS_DIR,
    )
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
        audit=audit,
        painel_operador=operador,
        deploy_dir=deploy_dir,
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


def test_fronteira_nenhuma_rota_escreve_crenca_e_formacao_e_so_do_mestre(persona, tmp_path):
    """Teste-assinatura da fronteira, agora com os DOIS papéis.

    Para o OPERADOR (cliente) nada mudou: ele propõe/aprova e cura KB, nunca
    escreve crença nem edita formação. A formação passou a ser editável APENAS
    pelo MESTRE (criador/dev), e só sob o prefixo do painel — com senha mestre
    vazia a porta não abre para ninguém (verificado em runtime abaixo).
    """
    app = _app(persona, _engine_com_proposta_externa(), tmp_path)
    caminhos = app.openapi()["paths"]
    escreve = {"post", "put", "patch", "delete"}

    for caminho, operacoes in caminhos.items():
        path = caminho.lower()
        metodos_escrita = escreve & {m.lower() for m in operacoes}
        if metodos_escrita:
            # (a) toda escrita de FORMAÇÃO mora sob o prefixo do painel
            if "formacao" in path:
                assert path.startswith("/painel/"), caminho
            # NENHUMA rota de ESCRITA mexe em crença/memória diretamente.
            assert "belief" not in path and "crenca" not in path, caminho
            assert "/memoria" not in path, caminho  # memória é só leitura (GET)

    # A ÚNICA escrita de memória existente é via aprovar/rejeitar (motor governado).
    escrita = [p for p, ops in caminhos.items() if escreve & {m.lower() for m in ops}]
    assert any("aprovar" in p for p in escrita)
    assert any("rejeitar" in p for p in escrita)

    # (c) sem painel_senha_mestre configurada, formação é 403 SEMPRE — mesmo
    # para uma sessão de operador válida (o modo mestre simplesmente não existe).
    client = TestClient(app)
    _logar(client)
    assert client.get("/painel/api/formacao").status_code == 403
    assert client.get("/painel/api/formacao/SOUL.md").status_code == 403
    r = client.post("/painel/api/formacao/SOUL.md", json={"conteudo": "x"})
    assert r.status_code == 403


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


# --------------------------- troca de senha (7d+) --------------------------- #

TOML_EXEMPLO = '''# Cortex — deploy de exemplo
painel_habilitado = true
painel_senha = "s3nha-do-painel"   # senha do operador
painel_operador = "Carlos Menezes"
'''


def _deploy_com_toml(tmp_path) -> Path:
    """Deploy mínimo: só o cortex.toml (é o que a troca de senha reescreve)."""
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "cortex.toml").write_text(TOML_EXEMPLO, encoding="utf-8")
    return deploy


def test_troca_de_senha_persiste_no_toml_e_vale_no_ato(persona, tmp_path):
    deploy = _deploy_com_toml(tmp_path)
    client = TestClient(_app(persona, _engine_com_proposta_externa(), tmp_path, deploy_dir=deploy))
    _logar(client)

    r = client.post(
        "/painel/api/senha", json={"senha_atual": SENHA, "nova_senha": "nova-senha-forte"}
    )
    assert r.status_code == 200

    # 1) persistiu no toml (só a linha da senha; comentários preservados)
    texto = (deploy / "cortex.toml").read_text(encoding="utf-8")
    assert 'painel_senha = "nova-senha-forte"   # senha do operador' in texto
    assert "painel_operador" in texto and "# Cortex — deploy de exemplo" in texto
    assert SENHA not in texto

    # 2) vale no ato, sem reiniciar: a antiga não loga, a nova sim
    novo = TestClient(client.app)
    assert novo.post("/painel/login", json={"senha": SENHA}).status_code == 401
    assert novo.post("/painel/login", json={"senha": "nova-senha-forte"}).status_code == 200

    # 3) a sessão que trocou continua viva (cookie reemitido)
    assert client.get("/painel/api/resumo").status_code == 200


def test_troca_de_senha_exige_a_atual_e_valida_a_nova(persona, tmp_path):
    deploy = _deploy_com_toml(tmp_path)
    client = TestClient(_app(persona, _engine_com_proposta_externa(), tmp_path, deploy_dir=deploy))
    _logar(client)

    # senha atual errada → 401 (estar logado não basta)
    r = client.post(
        "/painel/api/senha", json={"senha_atual": "errada", "nova_senha": "outra-senha"}
    )
    assert r.status_code == 401
    # curta demais → 400
    r = client.post("/painel/api/senha", json={"senha_atual": SENHA, "nova_senha": "curta"})
    assert r.status_code == 400
    # igual à atual → 400
    r = client.post("/painel/api/senha", json={"senha_atual": SENHA, "nova_senha": SENHA})
    assert r.status_code == 400
    # nada disso tocou o arquivo
    assert f'painel_senha = "{SENHA}"' in (deploy / "cortex.toml").read_text(encoding="utf-8")


def test_troca_de_senha_sem_sessao_e_sem_toml(persona, tmp_path):
    # sem cookie → 401 (rota protegida)
    sem_sessao = TestClient(_app(persona, _engine_com_proposta_externa(), tmp_path))
    r = sem_sessao.post("/painel/api/senha", json={"senha_atual": SENHA, "nova_senha": "x" * 10})
    assert r.status_code == 401

    # deploy sem cortex.toml conhecido (dev rodando do CWD) → 409 explicativo
    client = TestClient(_app(persona, _engine_com_proposta_externa(), tmp_path))
    _logar(client)
    r = client.post("/painel/api/senha", json={"senha_atual": SENHA, "nova_senha": "x" * 10})
    assert r.status_code == 409
    assert "cortex.toml" in r.json()["detail"]


def test_atualizar_senha_no_toml_escapa_e_falha_claro(tmp_path):
    from cortex.server.painel import SenhaTomlError, atualizar_senha_no_toml

    alvo = tmp_path / "cortex.toml"
    alvo.write_text(TOML_EXEMPLO, encoding="utf-8")
    atualizar_senha_no_toml(alvo, 'a"b\\c')
    assert 'painel_senha = "a\\"b\\\\c"' in alvo.read_text(encoding="utf-8")

    # arquivo sem a linha → erro claro, não silencioso
    vazio = tmp_path / "outro.toml"
    vazio.write_text("painel_habilitado = true\n", encoding="utf-8")
    with pytest.raises(SenhaTomlError, match="painel_senha"):
        atualizar_senha_no_toml(vazio, "qualquer-senha")


# --------------------------- modo mestre (formação) ------------------------- #

MESTRE = "senha-mestre-do-criador"


def _personas_copia(tmp_path) -> Path:
    """Cópia da formação de exemplo — os testes editam sem sujar o repo."""
    import shutil

    destino = tmp_path / "personas"
    shutil.copytree(PERSONAS_DIR, destino)
    return destino


def _app_mestre(persona, tmp_path, personas_dir, audit=None):
    return _app(
        persona,
        _engine_com_proposta_externa(),
        tmp_path,
        senha_mestre=MESTRE,
        personas_dir=personas_dir,
        audit=audit,
    )


def _logar_mestre(client):
    r = client.post("/painel/login", json={"senha": MESTRE})
    assert r.status_code == 200 and r.json()["papel"] == "mestre"


def test_papel_mestre_vs_operador(persona, tmp_path):
    pd = _personas_copia(tmp_path)
    client = TestClient(_app_mestre(persona, tmp_path, pd))

    # operador (senha normal) → sessão válida, mas 403 na formação
    _logar(client)
    assert client.get("/painel/api/resumo").json()["modo"] == "operador"
    assert client.get("/painel/api/formacao").status_code == 403
    assert client.post("/painel/api/formacao/SOUL.md", json={"conteudo": "x"}).status_code == 403

    # mestre → passa
    _logar_mestre(client)
    assert client.get("/painel/api/resumo").json()["modo"] == "mestre"
    lista = client.get("/painel/api/formacao").json()
    nomes = {a["arquivo"] for a in lista["arquivos"]}
    assert {"SOUL.md", "USER.md"} <= nomes
    assert any(n.startswith("playbooks/") for n in nomes)
    assert client.get("/painel/api/formacao/SOUL.md").json()["conteudo"].startswith("---")


def test_soul_com_yaml_invalido_da_400_e_nao_toca_o_arquivo(persona, tmp_path):
    pd = _personas_copia(tmp_path)
    client = TestClient(_app_mestre(persona, tmp_path, pd))
    _logar_mestre(client)
    original = (pd / "SOUL.md").read_text(encoding="utf-8")

    r = client.post(
        "/painel/api/formacao/SOUL.md",
        json={"conteudo": "---\nnome: [isto: nao: fecha\n---\n\nprosa"},
    )
    assert r.status_code == 400
    assert (pd / "SOUL.md").read_text(encoding="utf-8") == original  # intocado
    assert not (pd / ".historico").exists()  # nem backup houve


def test_salvar_formacao_faz_backup_e_audita_com_diff(persona, tmp_path):
    pd = _personas_copia(tmp_path)
    audit = AuditTrail(tmp_path / "audit.jsonl")
    client = TestClient(_app_mestre(persona, tmp_path, pd, audit=audit))
    _logar_mestre(client)

    antes = (pd / "SOUL.md").read_text(encoding="utf-8")
    novo = antes + "\n\nParágrafo acrescentado pelo mestre.\n"
    r = client.post("/painel/api/formacao/SOUL.md", json={"conteudo": novo})
    assert r.status_code == 200 and r.json()["requer_restart"] is True

    # gravou
    assert (pd / "SOUL.md").read_text(encoding="utf-8") == novo
    # backup da versão anterior
    backups = list((pd / ".historico").glob("SOUL.*.md"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == antes
    assert r.json()["backup"] == backups[0].name
    # audit com diff
    linhas = [json.loads(li) for li in (tmp_path / "audit.jsonl").read_text().splitlines()]
    ev = next(li for li in linhas if li["tipo"] == "edicao_formacao")
    assert ev["arquivo"] == "SOUL.md" and ev["papel"] == "mestre"
    assert ev["tamanho_antes"] == len(antes) and ev["tamanho_depois"] == len(novo)
    assert "Parágrafo acrescentado pelo mestre." in ev["diff"] and ev["diff"].startswith("---")


def test_formacao_recusa_traversal_e_arquivo_fora_do_catalogo(persona, tmp_path):
    pd = _personas_copia(tmp_path)
    client = TestClient(_app_mestre(persona, tmp_path, pd))
    _logar_mestre(client)
    segredo = tmp_path / "segredo.md"
    segredo.write_text("nao deveria vazar", encoding="utf-8")

    for caminho in ("../segredo.md", "playbooks/../../segredo.md", "tools.yaml", "AGENTS.txt"):
        r = client.get(f"/painel/api/formacao/{caminho}")
        assert r.status_code in (400, 404), caminho
        assert "nao deveria vazar" not in r.text
    assert segredo.read_text(encoding="utf-8") == "nao deveria vazar"


def test_cookie_de_operador_nao_vira_mestre(persona, tmp_path):
    """Escalada de papel: quem tem só a senha de operador não forja 'mestre'."""
    from cortex.server.painel import _assinar, papel_do_cookie

    forjado = _assinar(SENHA, 2 ** 31, "mestre")  # assinado com a senha ERRADA
    assert papel_do_cookie(forjado, SENHA, MESTRE) is None

    pd = _personas_copia(tmp_path)
    client = TestClient(_app_mestre(persona, tmp_path, pd))
    client.cookies.set("painel_sessao", forjado)
    assert client.get("/painel/api/formacao").status_code in (401, 403)
