"""Testes da notificação da Learning Queue (Fase 7c) — o Cortex avisa o gestor.

Proposta PENDENTE nova num turno → 1 aviso ao canal do gestor, com id e
como-decidir. Sem renotificação, sem poluir a memória, e gestor não mapeado
não quebra.
"""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from cortex.identity import carregar_persona
from cortex.memory import (
    DictAuthorityMap,
    DictSourceOfTruth,
    HeuristicClassifier,
    InMemoryStore,
    Justification,
    MemoryEngine,
    Source,
    SourceKind,
)
from cortex.runtime import AgentLoop, LLMResponse, StubProvider, criar_registry_mock
from cortex.runtime.extracao_conversa import HeuristicExtratorConversa
from cortex.runtime.notificacao import NotificadorFila
from cortex.runtime.promotion import DOMINIO_PADRAO
from cortex.server import LogCanalSaida, canal_id_do_gestor, carregar_mapa_identidades, criar_app
from cortex.sor import MockSORGateway, registrar_tools_sor

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"
TOKEN = "tok"
GESTOR_WPP = "5511999990000"
EXTERNO_WPP = "5511000000000"
H = {"X-Cortex-Token": TOKEN}


@pytest.fixture(scope="module")
def persona():
    return carregar_persona(PERSONAS_DIR)


def _engine():
    engine = MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap({DOMINIO_PADRAO: {"Carlos Menezes"}}),
        source_of_truth=DictSourceOfTruth({}),
    )
    # Crença vigente forte — um fato externo contraditório vai escalar (vira proposta).
    engine.observe(
        "cliente:ACME:prazo",
        "30 dias",
        Source(name="Carlos Menezes", kind=SourceKind.HUMAN),
        Justification(why="combinado em reunião"),
        domain=DOMINIO_PADRAO,
    )
    return engine


def _mapa(persona, tmp_path, mapear_gestor=True):
    ids = (
        [{"canal": "whatsapp", "canal_id": GESTOR_WPP, "pessoa": "Carlos Menezes"}]
        if mapear_gestor
        else []
    )
    caminho = tmp_path / "canais.yaml"
    caminho.write_text(yaml.safe_dump({"identidades": ids}), encoding="utf-8")
    return carregar_mapa_identidades(caminho, persona)


def _app(persona, engine, mapa, canal, *, com_notificador=True):
    stub = StubProvider(roteiro=[LLMResponse(texto="ok")], repetir_ultimo=True)
    registry = criar_registry_mock(persona.tools)
    registrar_tools_sor(registry, MockSORGateway())
    loop = AgentLoop(
        stub, registry, memory=engine, extrator_conversa=HeuristicExtratorConversa()
    )
    notificador = (
        NotificadorFila(canal, canal_id_do_gestor(mapa, persona, "whatsapp"))
        if com_notificador
        else None
    )
    return criar_app(
        persona=persona,
        loop=loop,
        mapa_identidades=mapa,
        token=TOKEN,
        engine=engine,
        canal_saida=canal,
        notificador=notificador,
    )


FATO_CONTRADITORIO = "registra que cliente ACME prazo é 90 dias"


def _msg(canal_id, texto):
    return {"canal": "whatsapp", "canal_id": canal_id, "texto": texto}


def _enviar(client, canal_id, texto):
    return client.post("/v1/mensagens", json=_msg(canal_id, texto), headers=H)


def test_proposta_nova_notifica_o_gestor_com_como_decidir(persona, tmp_path):
    engine = _engine()
    canal = LogCanalSaida()
    client = TestClient(_app(persona, engine, _mapa(persona, tmp_path), canal))

    # Fato EXTERNO contraditório → escala → proposta nova.
    _enviar(client, EXTERNO_WPP, FATO_CONTRADITORIO)

    # O gestor foi avisado uma vez, no canal dele, com id e como-decidir.
    assert len(canal.enviados) == 1
    destino, texto = canal.enviados[0]
    assert destino == GESTOR_WPP
    pid = engine.pending_approvals[0].id
    assert f"#{pid}" in texto
    assert "aprovar" in texto and "rejeitar" in texto
    assert "⚠" in texto  # a fonte da proposta é externa


def test_sem_proposta_nova_nao_notifica(persona, tmp_path):
    engine = _engine()
    canal = LogCanalSaida()
    client = TestClient(_app(persona, engine, _mapa(persona, tmp_path), canal))
    # Mensagem benigna do gestor (mapeado) — nenhuma proposta nova.
    _enviar(client, GESTOR_WPP, "bom dia, tudo certo?")
    assert canal.enviados == []


def test_proposta_existente_nao_e_renotificada(persona, tmp_path):
    engine = _engine()
    canal = LogCanalSaida()
    client = TestClient(_app(persona, engine, _mapa(persona, tmp_path), canal))

    _enviar(client, EXTERNO_WPP, FATO_CONTRADITORIO)
    assert len(canal.enviados) == 1  # notificou a nova
    # Outro turno SEM proposta nova → a pendente de antes NÃO é renotificada.
    _enviar(client, EXTERNO_WPP, "ok, obrigado")
    assert len(canal.enviados) == 1


def test_notificacao_nao_vira_turno_nem_polui_memoria(persona, tmp_path):
    engine = _engine()
    canal = LogCanalSaida()
    client = TestClient(_app(persona, engine, _mapa(persona, tmp_path), canal))

    _enviar(client, EXTERNO_WPP, FATO_CONTRADITORIO)
    saude = client.get("/v1/saude").json()
    # A notificação é OUTBOUND: não vira turno (1 só) nem proposta extra.
    assert saude["turnos_atendidos"] == 1
    assert len(engine.pending_approvals) == 1
    # A crença vigente seguiu intacta (o externo escalou, não sobrescreveu).
    assert engine.active("cliente:ACME:prazo").value == "30 dias"


def test_gestor_nao_mapeado_nao_quebra(persona, tmp_path):
    engine = _engine()
    canal = LogCanalSaida()
    mapa = _mapa(persona, tmp_path, mapear_gestor=False)  # gestor sem canal → None
    client = TestClient(_app(persona, engine, mapa, canal))

    r = _enviar(client, EXTERNO_WPP, FATO_CONTRADITORIO)
    assert r.status_code == 200  # turno respondeu normalmente
    assert canal.enviados == []  # sem destino, nada enviado (só warning)
    assert len(engine.pending_approvals) == 1  # a proposta foi criada igual
