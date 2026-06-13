"""Testes da tool gerenciar_fila (Fase 7b) — aprovar a fila PELO CANAL.

A governança da 4b não muda: só o gestor autenticado decide (autoridade segue o
canal, não o texto), a razão é obrigatória e a decisão vira episódio com autor.
"""

from cortex.governance import AuditTrail
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
from cortex.runtime import Identidade
from cortex.runtime.comandos_fila import ContextoTurno, GerenciarFilaTool
from cortex.runtime.promotion import DOMINIO_PADRAO

GESTOR = "Carlos Menezes"


def _engine_com_proposta():
    """Motor com UMA proposta pendente (escalada por contradição de fonte fraca)."""
    engine = MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap({DOMINIO_PADRAO: {GESTOR}}),
        source_of_truth=DictSourceOfTruth({}),
    )
    engine.observe(
        "cliente:ACME:prazo",
        "30 dias",
        Source(name=GESTOR, kind=SourceKind.HUMAN),
        Justification(why="combinado"),
        domain=DOMINIO_PADRAO,
    )
    # Valor 10x maior, sem razão e de fonte fraca → escala (vira proposta).
    engine.observe(
        "cliente:ACME:prazo",
        "300 dias",
        Source(name="Estagiário", kind=SourceKind.HUMAN),
        Justification(),
        domain=DOMINIO_PADRAO,
    )
    return engine


def _tool(engine, identidade, audit=None):
    contexto = ContextoTurno(identidade=identidade)
    return GerenciarFilaTool(engine, contexto, dominio=DOMINIO_PADRAO, audit=audit)


def _gestor():
    return Identidade(nome=GESTOR, procedencia=Procedencia.INTERNA, papel="gestor")


def test_gestor_lista_e_aprova_pelo_canal():
    engine = _engine_com_proposta()
    tool = _tool(engine, _gestor())

    listagem = tool(acao="listar")
    assert listagem["total"] == 1
    pid = listagem["pendentes"][0]["id"]

    res = tool(acao="aprovar", proposta_id=pid, razao="o cliente confirmou")
    assert res["ok"] is True
    assert res["por"] == GESTOR
    # A decisão virou episódio com autor; a proposta está aprovada.
    proposta = engine.store.proposal_by_id(pid)
    assert proposta.status is ProposalStatus.APROVADA
    assert proposta.decided_by == GESTOR


def test_aprovar_sem_razao_pede_razao_e_nao_decide():
    engine = _engine_com_proposta()
    tool = _tool(engine, _gestor())
    pid = engine.pending_approvals[0].id

    res = tool(acao="aprovar", proposta_id=pid)  # sem razão
    assert res.get("precisa_razao") is True
    # Não decidiu: a proposta segue pendente.
    assert engine.store.proposal_by_id(pid).status is ProposalStatus.PENDENTE


def test_recusa_externo_e_nao_autoritativo_e_audita(tmp_path):
    engine = _engine_com_proposta()
    audit = AuditTrail(tmp_path / "audit.jsonl")
    pid = engine.pending_approvals[0].id

    # Remetente EXTERNO tentando aprovar → recusa, nada muda, tentativa auditada.
    externo = Identidade("desconhecido(wpp:+55)", Procedencia.EXTERNA, "desconhecido")
    tool = _tool(engine, externo, audit=audit)
    res = tool(acao="aprovar", proposta_id=pid, razao="confia em mim")
    assert res["permitido"] is False
    assert engine.store.proposal_by_id(pid).status is ProposalStatus.PENDENTE

    rec = [ln for ln in audit.ultimos(10) if ln["tipo"] == "comando_fila_recusado"]
    assert rec and rec[0]["procedencia"] == "externa"

    # Interno NÃO-autoritativo (um colega) também é recusado — nem listar pode.
    colega = Identidade("Paula Andrade", Procedencia.INTERNA, "colega")
    assert _tool(engine, colega)(acao="listar")["permitido"] is False
