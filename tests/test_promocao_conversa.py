"""Integração da promoção de conversa (Fase 7b) — tudo pelo MESMO observe().

Prova que um fato dito na conversa vira memória passando pelo motor governado
(não por escrita direta), que a procedência segue o canal (externo escala) e
que tools têm prioridade no dedup.
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
from cortex.runtime import Identidade, Message, PromotionCandidate, Role
from cortex.runtime.extracao_conversa import HeuristicExtratorConversa
from cortex.runtime.promotion import DOMINIO_PADRAO, promover_fim_de_turno


def _engine(gestor="Carlos Menezes"):
    return MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap({DOMINIO_PADRAO: {gestor}}),
        source_of_truth=DictSourceOfTruth({}),
    )


def _ident(nome="Carlos Menezes", proc=Procedencia.INTERNA, papel="gestor"):
    return Identidade(nome=nome, procedencia=proc, papel=papel)


def _turno_usuario(texto):
    return [Message(role=Role.USER, content=texto)]


def test_fato_de_gestor_interno_vira_memoria_via_observe(tmp_path):
    engine = _engine()
    audit = AuditTrail(tmp_path / "audit.jsonl")
    eps = promover_fim_de_turno(
        engine,
        _turno_usuario("anota aí: cliente ACME prazo = 45 dias"),
        extrator_conversa=HeuristicExtratorConversa(),
        identidade=_ident(),
        audit=audit,
    )
    # Passou pelo observe() (gerou episódio) e virou a crença vigente.
    assert len(eps) == 1
    ativo = engine.active("cliente:ACME:prazo")
    assert ativo is not None and ativo.value == "45 dias"
    assert ativo.source.name == "Carlos Menezes"
    # O aprendizado da conversa entrou no audit.
    linhas = [ln for ln in audit.ultimos(10) if ln["tipo"] == "aprendizado_conversa"]
    assert linhas and linhas[0]["key"] == "cliente:ACME:prazo"
    assert linhas[0]["procedencia"] == "interna"
    assert linhas[0]["desfecho"] == "aceito"


def test_fato_externo_contraditorio_escala_para_a_fila(tmp_path):
    engine = _engine()
    # Crença vigente de fonte interna autoritativa.
    engine.observe(
        "cliente:ACME:prazo",
        "30 dias",
        Source(name="Carlos Menezes", kind=SourceKind.HUMAN),
        Justification(why="combinado em reunião"),
        domain=DOMINIO_PADRAO,
    )
    # Fato de canal EXTERNO que contradiz → procedência externa (teto 0.4) → escala.
    ident_ext = _ident("desconhecido(whatsapp:+55)", Procedencia.EXTERNA, "desconhecido")
    promover_fim_de_turno(
        engine,
        _turno_usuario("registra que cliente ACME prazo é 90 dias"),
        extrator_conversa=HeuristicExtratorConversa(),
        identidade=ident_ext,
    )
    # Memória inalterada e proposta na fila, marcada como fonte externa.
    assert engine.active("cliente:ACME:prazo").value == "30 dias"
    pendentes = engine.pending_approvals
    assert len(pendentes) == 1
    assert pendentes[0].proposed_value == "90 dias"
    assert pendentes[0].source.procedencia is Procedencia.EXTERNA


def test_dedup_conversa_que_repete_tool_fica_so_a_tool(monkeypatch):
    from cortex.runtime import promotion

    def _extrator_fake(_resultado):
        return [
            PromotionCandidate(
                key="cliente:ACME:prazo",
                value="45 dias",
                source=Source(name="tool_x", kind=SourceKind.TOOL),
                justification=Justification(why="retornado pela tool"),
            )
        ]

    monkeypatch.setitem(promotion.EXTRATORES_POR_TOOL, "tool_x", _extrator_fake)
    engine = _engine()
    turno = [
        Message(role=Role.TOOL, nome_tool="tool_x", content='{"qualquer": 1}'),
        Message(role=Role.USER, content="anota aí: cliente ACME prazo = 45 dias"),
    ]
    eps = promover_fim_de_turno(
        engine,
        turno,
        extrator_conversa=HeuristicExtratorConversa(),
        identidade=_ident(),
    )
    # Mesmo key+value vindo de tool e de conversa → 1 só observe (o da tool).
    assert len(eps) == 1
    ativo = engine.active("cliente:ACME:prazo")
    assert ativo.value == "45 dias"
    assert ativo.source.name == "tool_x"  # a fonte que ficou é a melhor (tool)


def test_aprendizado_desligado_quando_sem_identidade():
    # Sem identidade autenticada (modo dev), a conversa NÃO é ouvida.
    engine = _engine()
    eps = promover_fim_de_turno(
        engine,
        _turno_usuario("anota aí: cliente ACME prazo = 45 dias"),
        extrator_conversa=HeuristicExtratorConversa(),
        identidade=None,
    )
    assert eps == []
    assert engine.active("cliente:ACME:prazo") is None


def test_proposta_externa_pode_ser_aprovada_pelo_gestor(tmp_path):
    """Fecha o ciclo 7b: fato externo escala e o gestor aprova (status muda)."""
    engine = _engine()
    engine.observe(
        "cliente:ACME:prazo",
        "30 dias",
        Source(name="Carlos Menezes", kind=SourceKind.HUMAN),
        Justification(why="combinado"),
        domain=DOMINIO_PADRAO,
    )
    promover_fim_de_turno(
        engine,
        _turno_usuario("registra que cliente ACME prazo é 90 dias"),
        extrator_conversa=HeuristicExtratorConversa(),
        identidade=_ident("desconhecido(whatsapp:+55)", Procedencia.EXTERNA, "desconhecido"),
    )
    pid = engine.pending_approvals[0].id
    engine.aprovar(pid, "Carlos Menezes", "confirmei com o cliente")
    assert engine.store.proposal_by_id(pid).status is ProposalStatus.APROVADA
    assert engine.active("cliente:ACME:prazo").value == "90 dias"
