"""Testes do Heartbeat (Fase 8) — saúde local, limiar de disco e /v1/saude."""

from pathlib import Path

from fastapi.testclient import TestClient

from cortex.identity import carregar_persona
from cortex.ops.heartbeat import Heartbeat, StatusSaude
from cortex.runtime import AgentLoop, LLMResponse, StubProvider, criar_registry_mock
from cortex.server import criar_app

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"


def test_heartbeat_coleta_metricas_e_status(tmp_path):
    hb = Heartbeat(caminho_disco=str(tmp_path), sessoes_ativas=lambda: 2, pendentes=lambda: 5)
    m = hb.coletar()
    assert m["status"] in {s.value for s in StatusSaude}
    assert 0 <= m["disco_livre_pct"] <= 100
    assert m["sessoes_ativas"] == 2
    assert m["propostas_pendentes"] == 5
    assert "ts" in m


def test_status_por_limiar_de_disco():
    assert Heartbeat._status(50.0) is StatusSaude.OK
    assert Heartbeat._status(8.0) is StatusSaude.WARNING  # disco livre < 10%
    assert Heartbeat._status(2.0) is StatusSaude.ERROR  # < 3%


def test_banco_bytes_quando_existe(tmp_path):
    db = tmp_path / "memoria.kuzu"
    db.write_bytes(b"x" * 128)
    assert Heartbeat(caminho_disco=str(tmp_path), db_path=db).coletar()["banco_bytes"] == 128
    assert Heartbeat(caminho_disco=str(tmp_path)).coletar()["banco_bytes"] is None


def test_saude_do_servidor_traz_o_heartbeat():
    persona = carregar_persona(PERSONAS_DIR)
    loop = AgentLoop(
        StubProvider(roteiro=[LLMResponse(texto="ok")], repetir_ultimo=True),
        criar_registry_mock(persona.tools),
    )
    app = criar_app(persona=persona, loop=loop, mapa_identidades={}, token="tok")
    saude = TestClient(app).get("/v1/saude").json()
    assert "heartbeat" in saude
    assert saude["heartbeat"]["status"] in {s.value for s in StatusSaude}
