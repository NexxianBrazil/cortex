"""Testes do Criador Visual de Cortexes — TestClient + gerenciador mockado.

Sem rede e sem subir processo real: o gerenciador de processos é um dublê. A
FRONTEIRA é o ponto: o criador cria/configura/sobe/indexa (alocação) e não tem
NENHUMA rota que edite SOUL/formação ou escreva memória — o teste-assinatura
varre a superfície canônica de rotas (app.openapi), como no painel 7d.
"""

import pytest
from fastapi.testclient import TestClient

from cortex.creator import ProcessoError, criar_app_criador
from cortex.scaffold import gerar_deploy


class GerenciadorFake:
    """Dublê do GerenciadorProcessos: registra chamadas, não sobe nada."""

    def __init__(self):
        self.no_ar: dict[str, dict] = {}
        self.chamadas: list[tuple[str, str]] = []

    def status(self, nome):
        info = self.no_ar.get(nome)
        return {**info, "vivo": True} if info else None

    def subir(self, nome):
        self.chamadas.append(("subir", nome))
        if nome in self.no_ar:
            raise ProcessoError(f"'{nome}' já está no ar")
        self.no_ar[nome] = {"pid": 4242, "porta": 8420}
        return self.no_ar[nome]

    def parar(self, nome):
        self.chamadas.append(("parar", nome))
        if nome not in self.no_ar:
            raise ProcessoError(f"'{nome}' não está no ar")
        del self.no_ar[nome]


@pytest.fixture()
def ambiente_criador(tmp_path):
    """(client, base_dir, gerenciador) — app do criador sobre um base_dir vazio."""
    base = tmp_path / "cortexes"
    base.mkdir()
    processos = GerenciadorFake()
    client = TestClient(criar_app_criador(base_dir=base, processos=processos))
    return client, base, processos


def _criar_rafael(base):
    """Deploy fixture pelo MESMO scaffold do produto (determinístico)."""
    return gerar_deploy(
        base / "rafael",
        nome="Rafael",
        funcao="suporte técnico",
        gestor="Denilson Medeiros",
        token="tok-teste",
        painel_senha="senha-teste",
    )


PEDIDO_VALIDO = {
    "nome": "Rafael",
    "funcao": "suporte técnico",
    "gestor": "Denilson Medeiros",
    "provider": "stub",
}


def test_ambiente_devolve_diagnostico(ambiente_criador):
    client, base, _ = ambiente_criador
    r = client.get("/api/ambiente")
    assert r.status_code == 200
    corpo = r.json()
    ids = {item["id"] for item in corpo["itens"]}
    assert {"python", "anthropic", "openai", "graphiti_core", "diretorio_base", "disco"} <= ids
    for item in corpo["itens"]:  # todo item reprovável ORIENTA como resolver
        assert item["como_resolver"]
    assert corpo["diretorio_base"] == str(base.resolve())


def test_criar_valido_chama_scaffold_e_devolve_proximos_passos(ambiente_criador, monkeypatch):
    client, base, _ = ambiente_criador
    chamadas = []
    import cortex.creator.deploys as deploys_mod

    original = deploys_mod.gerar_deploy

    def espiao(*args, **kwargs):
        chamadas.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(deploys_mod, "gerar_deploy", espiao)

    r = client.post("/api/criar", json=PEDIDO_VALIDO)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["deploy"] == str(base / "rafael")
    assert corpo["proximos_passos"]
    # Casca, não motor: a criação passou pelo scaffold EXISTENTE, validado.
    assert chamadas and chamadas[0]["nome"] == "Rafael"
    assert (base / "rafael" / "cortex.toml").is_file()
    assert (base / "rafael" / "personas" / "SOUL.md").is_file()


def test_criar_destino_nao_vazio_e_campos_ausentes_dao_400(ambiente_criador):
    client, base, _ = ambiente_criador
    (base / "ocupado").mkdir()
    (base / "ocupado" / "arquivo.txt").write_text("x", encoding="utf-8")

    r = client.post("/api/criar", json={**PEDIDO_VALIDO, "destino": "ocupado"})
    assert r.status_code == 400 and "não está vazio" in r.json()["detail"]

    r = client.post("/api/criar", json={"nome": "", "funcao": "suporte", "gestor": ""})
    assert r.status_code == 400
    assert "nome" in r.json()["detail"] and "gestor" in r.json()["detail"]

    # Traversal no destino é barrado antes de tocar o disco.
    r = client.post("/api/criar", json={**PEDIDO_VALIDO, "destino": "../fora"})
    assert r.status_code == 400


def test_provider_com_chave_grava_no_env_nunca_no_toml(ambiente_criador):
    client, base, _ = ambiente_criador

    r = client.post("/api/criar", json={**PEDIDO_VALIDO, "provider": "claude"})
    assert r.status_code == 400 and "chave" in r.json()["detail"].lower()

    r = client.post(
        "/api/criar", json={**PEDIDO_VALIDO, "provider": "claude", "api_key": "sk-ant-segredo"}
    )
    assert r.status_code == 200
    deploy = base / "rafael"
    toml = (deploy / "cortex.toml").read_text(encoding="utf-8")
    assert 'provider = "claude"' in toml
    assert "sk-ant-segredo" not in toml  # segredo NUNCA no arquivo versionável
    assert "ANTHROPIC_API_KEY=sk-ant-segredo" in (deploy / ".env").read_text(encoding="utf-8")


def test_deploys_lista_o_diretorio_base(ambiente_criador):
    client, base, _ = ambiente_criador
    _criar_rafael(base)
    r = client.get("/api/deploys")
    assert r.status_code == 200
    (d,) = r.json()["deploys"]
    assert d["pasta"] == "rafael"
    assert d["nome"] == "Rafael"
    assert d["funcao"] == "suporte técnico"
    assert d["gestor"] == "Denilson Medeiros"
    assert d["provider"] == "stub"
    assert d["kb_indexada"] is False
    assert d["no_ar"] is False


def test_subir_e_parar_usam_o_gerenciador(ambiente_criador):
    client, base, processos = ambiente_criador
    _criar_rafael(base)

    r = client.post("/api/deploys/rafael/subir")
    assert r.status_code == 200
    assert r.json()["painel_url"] == "http://127.0.0.1:8420/painel"
    assert ("subir", "rafael") in processos.chamadas
    assert client.get("/api/deploys").json()["deploys"][0]["no_ar"] is True

    assert client.post("/api/deploys/rafael/subir").status_code == 409  # já no ar

    assert client.post("/api/deploys/rafael/parar").status_code == 200
    assert ("parar", "rafael") in processos.chamadas
    assert processos.no_ar == {}
    assert client.post("/api/deploys/rafael/parar").status_code == 409  # já parado

    assert client.post("/api/deploys/nao-existe/subir").status_code == 404


def test_kb_indexar_e_mini_chat_funcionam_offline(ambiente_criador):
    client, base, _ = ambiente_criador
    _criar_rafael(base)

    r = client.post("/api/deploys/rafael/kb/indexar")
    assert r.status_code == 200 and "documentos" in r.json()

    r = client.get("/api/deploys/rafael/testar", params={"texto": "olá, tudo bem?"})
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["persona"] == "Rafael"
    assert corpo["resposta"]  # StubProvider responde offline

    assert client.get("/api/deploys/rafael/testar", params={"texto": "  "}).status_code == 400


def test_fronteira_nenhuma_rota_edita_formacao_nem_escreve_memoria(ambiente_criador):
    """Teste-assinatura: o criador é ALOCAÇÃO — nascer/configurar, nunca formar.

    Mesmo espírito do teste-fronteira do painel 7d: varre a superfície canônica
    (app.openapi) e garante que as únicas escritas são criar deploy, subir/parar
    e indexar KB. Nada de SOUL, playbook, memória, crença ou fila por aqui.
    """
    client, _, _ = ambiente_criador
    caminhos = client.app.openapi()["paths"]
    escreve = {"post", "put", "patch", "delete"}

    proibido = ("soul", "playbook", "formacao", "memoria", "belief", "crenca", "fila")
    for caminho in caminhos:
        assert not any(p in caminho.lower() for p in proibido), caminho

    permitidas = {
        "/api/criar",
        "/api/deploys/{nome}/subir",
        "/api/deploys/{nome}/parar",
        "/api/deploys/{nome}/kb/indexar",
    }
    escrita = {c for c, ops in caminhos.items() if escreve & {m.lower() for m in ops}}
    assert escrita == permitidas
