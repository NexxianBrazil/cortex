"""Testes da Learning Queue (Fase 4b) — InMemoryStore, determinísticos.

Materializa *supervised learning, never autonomous mutation*: o escalonamento
vira proposta; a decisão humana (aprovar/rejeitar) é governada por autoridade
e SEMPRE vira memória com autor; nada é apagado.
"""

import pytest

from cortex.memory import (
    AutoridadeInsuficienteError,
    DictAuthorityMap,
    DictSourceOfTruth,
    HeuristicClassifier,
    InMemoryStore,
    Justification,
    MemoryEngine,
    ProposalStatus,
    PropostaJaDecididaError,
    Source,
    SourceKind,
    Status,
)

H = SourceKind.HUMAN
DOMINIO = "comercial"
GESTOR = "CFO Denilson"


@pytest.fixture()
def engine() -> MemoryEngine:
    """Motor onde o gestor é autoritativo no domínio (como o USER.md derivaria)."""
    return MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=DictAuthorityMap({DOMINIO: {GESTOR}}),
        source_of_truth=DictSourceOfTruth({}),
    )


def _escalar(engine) -> int:
    """Cria uma contradição de alto risco que escala; devolve o id da proposta.

    Estagiário (não autoritativo) propõe valor 10x → escala (autoridade menor
    + magnitude suspeita). Memória fica inalterada.
    """
    engine.observe(
        "cliente:X:limite", "R$ 50.000", Source(name=GESTOR, kind=H),
        Justification(why="análise de crédito"), domain=DOMINIO,
    )
    engine.observe(
        "cliente:X:limite", "R$ 500.000", Source(name="Estagiário", kind=H),
        Justification(), domain=DOMINIO,
    )
    return engine.pending_approvals[-1].id


def test_escalonamento_cria_proposta_com_warrant(engine):
    pid = _escalar(engine)
    p = engine.store.proposal_by_id(pid)
    assert p.status is ProposalStatus.PENDENTE
    assert p.key == "cliente:X:limite"
    assert p.current_value == "R$ 50.000"  # vigente quando escalou
    assert p.proposed_value == "R$ 500.000"
    assert p.source.name == "Estagiário"
    assert p.risk.value == "high"
    assert "magnitude" in p.reason
    # Linhagem: aponta ao episódio que a originou.
    eps = engine.store.episodes_for("cliente:X:limite")
    assert any(e.id == p.origin_episode_id and e.escalated for e in eps)


def test_aprovar_por_autoritativo_supera_e_vira_memoria(engine):
    pid = _escalar(engine)
    ep = engine.aprovar(pid, autor=GESTOR, razao="crédito aprovado em comitê")

    ativo = engine.active("cliente:X:limite")
    assert ativo.value == "R$ 500.000"
    assert ativo.reason_for_change == f"aprovado por {GESTOR}: crédito aprovado em comitê"
    # A antiga não foi apagada, só superada.
    superada = [
        b for b in engine.history("cliente:X:limite") if b.status is Status.SUPERSEDED
    ]
    assert superada and superada[0].value == "R$ 50.000"
    # A proposta foi decidida com autor/razão.
    p = engine.store.proposal_by_id(pid)
    assert p.status is ProposalStatus.APROVADA
    assert p.decided_by == GESTOR and p.decision_reason == "crédito aprovado em comitê"
    # A APROVAÇÃO é memória com autor.
    assert ep.source.name == GESTOR and "APROVOU" in ep.action
    assert ep.resulting_belief_id == ativo.id


def test_rejeitar_mantem_memoria_e_vira_episodio(engine):
    pid = _escalar(engine)
    ep = engine.rejeitar(pid, autor=GESTOR, razao="valor irreal, manter limite")

    # Memória inalterada: o vigente continua o valor do gestor.
    assert engine.active("cliente:X:limite").value == "R$ 50.000"
    p = engine.store.proposal_by_id(pid)
    assert p.status is ProposalStatus.REJEITADA
    assert p.decided_by == GESTOR
    # A REJEIÇÃO é episódio de 1ª classe (com autor e razão).
    assert ep.source.name == GESTOR and "REJEITOU" in ep.action
    assert ep.reason == "valor irreal, manter limite"
    assert ep.resulting_belief_id is None


def test_decidir_sem_autoridade_falha_e_proposta_intacta(engine):
    pid = _escalar(engine)
    with pytest.raises(AutoridadeInsuficienteError):
        engine.aprovar(pid, autor="Estagiário", razao="quero aprovar")
    assert engine.store.proposal_by_id(pid).status is ProposalStatus.PENDENTE
    assert engine.active("cliente:X:limite").value == "R$ 50.000"


def test_caducidade_a_vigente_mudou_desde_o_escalonamento(engine):
    pid = _escalar(engine)
    # Por OUTRO caminho, a vigente muda (correção autoritativa intermediária).
    engine.observe(
        "cliente:X:limite", "R$ 80.000", Source(name=GESTOR, kind=H),
        Justification(why="revisão de crédito"), domain=DOMINIO,
    )
    assert engine.active("cliente:X:limite").value == "R$ 80.000"

    ep = engine.aprovar(pid, autor=GESTOR, razao="aprovo o que foi proposto")

    # Proposta caducou: não aplicou escrita cega sobre um mundo já mudado.
    assert engine.store.proposal_by_id(pid).status is ProposalStatus.CADUCADA
    assert engine.active("cliente:X:limite").value == "R$ 80.000"  # só a correção intermediária
    assert "caducou" in ep.action
    assert ep.resulting_belief_id is None


def test_decidir_duas_vezes_falha(engine):
    pid = _escalar(engine)
    engine.aprovar(pid, autor=GESTOR, razao="ok")
    with pytest.raises(PropostaJaDecididaError):
        engine.rejeitar(pid, autor=GESTOR, razao="mudei de ideia")


# ---------------------------------------------------------------------------
# Fase 4c — propostas de AÇÃO (enforce → fila) e exceção one-shot
# ---------------------------------------------------------------------------


def test_acao_bloqueada_vira_proposta_e_aprovar_libera_excecao(engine):
    """Bloqueio em enforce cria Proposal kind=ACAO; aprovar não escreve crença;
    consumir_excecao marca consumed_at e gera episódio."""
    import json

    from cortex.memory import ProposalKind, propor_acao
    from cortex.risk import RiskLevel

    args = {"destinatario": "cliente@gmail.com", "assunto": "x", "corpo": "y"}
    proposta = propor_acao(
        tool="enviar_email",
        argumentos=args,
        risco=RiskLevel.HIGH,
        motivos=["destinatário externo"],
        autor_pedido="Mariana",
        domain="comercial",
    )
    engine.store.add_proposal(proposta)
    pid = proposta.id

    p = engine.store.proposal_by_id(pid)
    assert p.kind is ProposalKind.ACAO
    assert p.key == "acao:enviar_email"
    assert json.loads(p.proposed_value) == args  # args canônicos

    # Aprovar a AÇÃO: nenhuma crença escrita; episódio com o autor.
    n_beliefs = len(engine.store.all_beliefs())
    ep = engine.aprovar(pid, autor=GESTOR, razao="ok, pode enviar")
    assert len(engine.store.all_beliefs()) == n_beliefs  # sem escrita de crença
    assert "APROVOU a ação enviar_email" in ep.action

    # Exceção disponível: consumir uma vez marca consumed_at e gera episódio.
    args_json = json.dumps(args, ensure_ascii=False, sort_keys=True)
    consumida = engine.consumir_excecao("enviar_email", args_json)
    assert consumida is not None and consumida.id == pid
    assert engine.store.proposal_by_id(pid).consumed_at is not None
    # Segunda vez: já consumida → None.
    assert engine.consumir_excecao("enviar_email", args_json) is None


def test_rejeitar_acao_nao_gera_excecao(engine):
    from cortex.memory import ProposalStatus, propor_acao
    from cortex.risk import RiskLevel

    args = {"destinatario": "cliente@gmail.com", "assunto": "x", "corpo": "y"}
    proposta = propor_acao(
        "enviar_email", args, RiskLevel.HIGH, ["externo"], "Mariana", "comercial"
    )
    engine.store.add_proposal(proposta)

    ep = engine.rejeitar(proposta.id, autor=GESTOR, razao="não autorizo")
    assert "REJEITOU a ação enviar_email" in ep.action
    assert engine.store.proposal_by_id(proposta.id).status is ProposalStatus.REJEITADA
    # Nenhuma exceção disponível para consumir.
    import json

    aj = json.dumps(args, ensure_ascii=False, sort_keys=True)
    assert engine.consumir_excecao("enviar_email", aj) is None
