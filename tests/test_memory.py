"""Testes do núcleo da memória (Fase 3a).

Os 5 cenários do demo do protótipo viram testes pytest de verdade (A–E),
mais os testes do que é novo nesta fase: a episódica preservada e o
classificador trocável. Tudo determinístico, sem rede — usa a heurística.
"""

import pytest

from cortex.config import CortexConfig
from cortex.memory import (
    Belief,
    ConfiguracaoClassifierError,
    DictAuthorityMap,
    DictSourceOfTruth,
    Episode,
    HeuristicClassifier,
    InMemoryStore,
    Justification,
    MemoryEngine,
    Relationship,
    Source,
    SourceKind,
    Status,
    criar_classifier,
)
from cortex.memory.classifier import Classifier
from cortex.risk import RiskLevel

H, S = SourceKind.HUMAN, SourceKind.SYSTEM


@pytest.fixture()
def engine() -> MemoryEngine:
    """Motor com os mesmos dados de autoridade/fonte de verdade do demo."""
    authority = DictAuthorityMap(
        {
            "comercial": {"CFO Denilson", "Fonte de verdade"},
            "estoque": {"Líder de Estoque"},
        }
    )
    sot = DictSourceOfTruth({"pedido:4471:total": "R$ 12.000"})
    return MemoryEngine(
        store=InMemoryStore(),
        classifier=HeuristicClassifier(),
        authority_map=authority,
        source_of_truth=sot,
    )


# ---------------------------------------------------------------------------
# Os 5 cenários do protótipo, como testes
# ---------------------------------------------------------------------------


def test_A_repeticao_reforca_e_ganha_saliencia(engine):
    """(A) Mesmo fato repetido reforça e ganha saliência, sem conflito."""
    for _ in range(3):
        ep = engine.observe(
            "cliente:ABC:prazo",
            "30 dias",
            Source(name="Vendedor João", kind=H),
            Justification(why="histórico de pedidos"),
            domain="comercial",
        )
    assert ep.relationship is Relationship.REINFORCES
    assert ep.risk is RiskLevel.LOW
    ativo = engine.active("cliente:ABC:prazo")
    assert ativo.value == "30 dias"
    assert ativo.seen_count == 3
    assert ativo.salience == 3.0
    # Uma única crença na chave — reforço não cria duplicata.
    assert len(engine.history("cliente:ABC:prazo")) == 1


def test_B_correcao_autoritativa_nao_verificavel_supera_com_razao(engine):
    """(B) CFO (autoridade) corrige prazo: supera com razão, antiga preservada."""
    engine.observe(
        "cliente:ABC:prazo",
        "30 dias",
        Source(name="Vendedor João", kind=H),
        Justification(why="histórico de pedidos"),
        domain="comercial",
    )
    ep = engine.observe(
        "cliente:ABC:prazo",
        "60 dias",
        Source(name="CFO Denilson", kind=H),
        Justification(why="renegociação contratual fechada ontem"),
        domain="comercial",
    )
    assert ep.relationship is Relationship.CONTRADICTS
    assert ep.risk is RiskLevel.MEDIUM
    assert "superou" in ep.action
    assert ep.reason == "renegociação contratual fechada ontem"

    ativo = engine.active("cliente:ABC:prazo")
    assert ativo.value == "60 dias"

    # A antiga continua consultável, agora superada e com invalid_at marcado.
    superada = [
        b for b in engine.history("cliente:ABC:prazo") if b.status is Status.SUPERSEDED
    ]
    assert len(superada) == 1
    assert superada[0].value == "30 dias"
    assert superada[0].invalid_at is not None


def test_C_magnitude_absurda_autoridade_menor_escala_sem_mudar(engine):
    """(C) Estagiário propõe valor 10x com autoridade menor: escala, não muda."""
    engine.observe(
        "cliente:X:limite",
        "R$ 50.000",
        Source(name="CFO Denilson", kind=H),
        Justification(why="análise de crédito"),
        domain="comercial",
    )
    ep = engine.observe(
        "cliente:X:limite",
        "R$ 500.000",
        Source(name="Estagiário", kind=H),
        Justification(),
        domain="comercial",
    )
    assert ep.relationship is Relationship.CONTRADICTS
    assert ep.risk is RiskLevel.HIGH
    assert ep.escalated is True
    assert ep.magnitude_ratio == 10.0

    # Memória inalterada: o vigente continua sendo o valor do CFO.
    assert engine.active("cliente:X:limite").value == "R$ 50.000"
    assert len(engine.pending_approvals) == 1
    pendente = engine.pending_approvals[-1]
    assert pendente.proposed_value == "R$ 500.000"
    assert "magnitude suspeita" in pendente.reason


def test_correcao_gestor_sobre_documento_nao_escala(engine):
    """Correção 3: gestor autoritativo corrige um DOCUMENTO bem justificado.

    Antes da correção, a confiança da vigente (documento: 0.9 + why + evidence
    = 1.1) batia a autoridade CRUA do gestor (1.0) e a correção escalava à toa.
    Comparando confiança×confiança, o gestor (1.0 + why = 1.1) >= vigente →
    delibera e supera (não escala), preservando a linhagem.
    """
    engine.observe(
        "cliente:ABC:condicao",
        "à vista",
        Source(name="manual_comercial.pdf", kind=SourceKind.DOCUMENT),
        Justification(why="política vigente", evidence="manual_comercial.pdf#12"),
        domain="comercial",
    )
    ep = engine.observe(
        "cliente:ABC:condicao",
        "parcelado em 3x",
        Source(name="CFO Denilson", kind=H),  # autoritativo no domínio comercial
        Justification(why="exceção aprovada para este cliente"),
        domain="comercial",
    )
    assert not ep.escalated
    assert ep.risk is RiskLevel.MEDIUM
    ativo = engine.active("cliente:ABC:condicao")
    assert ativo.value == "parcelado em 3x"
    # Linhagem preservada: o documento antigo ficou SUPERSEDED, não apagado.
    superada = [
        b for b in engine.history("cliente:ABC:condicao") if b.status is Status.SUPERSEDED
    ]
    assert len(superada) == 1
    assert superada[0].value == "à vista"


def test_D_conflito_verificavel_confere_na_fonte_de_verdade(engine):
    """(D) Conflito verificável: não discute, confere no SAP e adota o real."""
    engine.observe(
        "pedido:4471:total",
        "R$ 10.000",
        Source(name="Vendedor João", kind=H),
        Justification(verifiable=True, proof_pointer="SAP:VBAK/4471"),
        domain="comercial",
    )
    ep = engine.observe(
        "pedido:4471:total",
        "R$ 12.000",
        Source(name="Mariana", kind=S),
        Justification(verifiable=True, proof_pointer="SAP:VBAK/4471"),
        domain="comercial",
    )
    assert ep.relationship is Relationship.CONTRADICTS
    assert ep.source_of_truth_consulted is True
    assert ep.source_of_truth_value == "R$ 12.000"
    assert engine.active("pedido:4471:total").value == "R$ 12.000"


def test_E_correcao_preserva_quem_corrigiu_e_por_que(engine):
    """(E) 'pratinho' → 'pires': linha do tempo sem nada apagado, com a razão."""
    engine.observe(
        "objeto:7:nome",
        "pratinho",
        Source(name="Colega Ana", kind=H),
        Justification(why="foi o que me falaram"),
        domain="comercial",
    )
    engine.observe(
        "objeto:7:nome",
        "pires",
        Source(name="CFO Denilson", kind=H),
        Justification(
            why="nome técnico correto da peça", evidence="catálogo do fabricante"
        ),
        domain="comercial",
    )
    ativo = engine.active("objeto:7:nome")
    assert ativo.value == "pires"
    assert ativo.reason_for_change == "nome técnico correto da peça"
    assert ativo.supersedes is not None

    # Nada apagado: ambos os valores seguem na história, um ativo, um superado.
    historia = engine.history("objeto:7:nome")
    assert [b.value for b in historia] == ["pratinho", "pires"]
    assert historia[0].status is Status.SUPERSEDED
    assert historia[1].status is Status.ACTIVE
    # Quem corrigiu fica preservado na crença ativa.
    assert ativo.source.name == "CFO Denilson"


# ---------------------------------------------------------------------------
# Novo na Fase 3a: a memória EPISÓDICA
# ---------------------------------------------------------------------------


def test_cada_observe_gera_um_episodio(engine):
    """Toda observação produz exatamente um episódio preservado."""
    ep = engine.observe(
        "cliente:Z:prazo",
        "15 dias",
        Source(name="Vendedor João", kind=H),
        Justification(why="combinado"),
        domain="comercial",
    )
    assert isinstance(ep, Episode)
    assert engine.store.episodes() == [ep]
    # O episódio guarda o evento cru e a decisão.
    assert ep.asserted_value == "15 dias"
    assert ep.source.name == "Vendedor João"
    assert ep.resulting_belief_id is not None


def test_episodica_nunca_perde_registro(engine):
    """Mesmo quando a crença é superada ou a proposta é escalada, o episódio
    bruto permanece — a episódica é append-only e é a base da lineage."""
    engine.observe(
        "cliente:ABC:prazo",
        "30 dias",
        Source(name="Vendedor João", kind=H),
        Justification(why="histórico"),
        domain="comercial",
    )
    engine.observe(  # contradição que supera
        "cliente:ABC:prazo",
        "60 dias",
        Source(name="CFO Denilson", kind=H),
        Justification(why="renegociação"),
        domain="comercial",
    )
    engine.observe(  # contradição que escala (não muda a semântica)
        "cliente:ABC:prazo",
        "9000 dias",
        Source(name="Estagiário", kind=H),
        Justification(),
        domain="comercial",
    )
    # 3 observações → 3 episódios, nenhum perdido, na ordem de chegada.
    episodios = engine.store.episodes_for("cliente:ABC:prazo")
    assert len(episodios) == 3
    assert [e.asserted_value for e in episodios] == ["30 dias", "60 dias", "9000 dias"]
    # O episódio escalado registra a escalada, ainda que a crença não mude.
    assert episodios[-1].escalated is True
    assert episodios[-1].resulting_belief_id is None


def test_episodio_da_consulta_registra_lineage_sem_memorizar_valor(engine):
    """A consulta à fonte de verdade fica registrada no episódio (lineage),
    mas a crença resultante segue verificável — o valor não vira cache."""
    engine.observe(
        "pedido:4471:total",
        "R$ 10.000",
        Source(name="Vendedor João", kind=H),
        Justification(verifiable=True),
        domain="comercial",
    )
    ep = engine.observe(
        "pedido:4471:total",
        "R$ 12.000",
        Source(name="Mariana", kind=S),
        Justification(verifiable=True),
        domain="comercial",
    )
    assert ep.source_of_truth_consulted is True
    assert ep.source_of_truth_value == "R$ 12.000"
    # A crença adotada continua verificável (apodrece → reconfere no futuro).
    assert engine.active("pedido:4471:total").justification.verifiable is True


# ---------------------------------------------------------------------------
# Novo na Fase 3a: classificador TROCÁVEL
# ---------------------------------------------------------------------------


def test_classificador_default_vem_da_config():
    """A config escolhe o classificador, como faz com o provider de LLM."""
    cls = criar_classifier(CortexConfig(classifier="heuristic"))
    assert isinstance(cls, HeuristicClassifier)


def test_classificador_llm_exige_provider():
    """classifier=llm (implementado na 3c) exige um provider; falha claro sem ele."""
    with pytest.raises(ConfiguracaoClassifierError, match="LLMProvider"):
        criar_classifier(CortexConfig(classifier="llm"))


def test_classificador_e_trocavel_por_um_custom(engine):
    """O motor fala só com a interface Classifier — dá para injetar outro.

    Aqui um classificador que força tudo a INDEPENDENT prova que o motor usa
    a relação que o classificador devolve (sem reforço/contradição nenhum).
    """

    class SempreIndependente(Classifier):
        def classify(self, existing: Belief | None, value: str) -> Relationship:
            return Relationship.INDEPENDENT

    engine.classifier = SempreIndependente()
    engine.observe(
        "k", "v1", Source(name="A", kind=H), Justification(), domain="comercial"
    )
    ep = engine.observe(
        "k", "v2", Source(name="B", kind=H), Justification(), domain="comercial"
    )
    # Como tudo é "independente", a segunda não contradiz: vira outra crença.
    assert ep.relationship is Relationship.INDEPENDENT
    assert len(engine.history("k")) == 2
    assert all(b.status is Status.ACTIVE for b in engine.history("k"))


def test_heuristica_classifica_os_tres_casos():
    """A heurística portada cobre independente/reforça/contradiz."""
    h = HeuristicClassifier()
    base = Belief(
        key="k",
        value="30 dias",
        source=Source(name="A", kind=H),
        justification=Justification(),
        domain="comercial",
        confidence=0.5,
    )
    assert h.classify(None, "qualquer") is Relationship.INDEPENDENT
    assert h.classify(base, "30 DIAS") is Relationship.REINFORCES  # normaliza
    assert h.classify(base, "60 dias") is Relationship.CONTRADICTS


def test_llm_classifier_fallback_incerto_e_contradiz():
    """Correção 2: resposta incerta do LLM NÃO vira INDEPENDENT (escreveria por
    fora do cético); o fallback binário conservador é CONTRADICTS."""
    from cortex.memory.classifier import LLMClassifier

    assert LLMClassifier._interpretar("reforça") is Relationship.REINFORCES
    assert LLMClassifier._interpretar("Contradiz.") is Relationship.CONTRADICTS
    # Incerto/vazio/None → CONTRADICTS (liga o ceticismo, não escreve calado).
    assert LLMClassifier._interpretar("hmm, não sei dizer") is Relationship.CONTRADICTS
    assert LLMClassifier._interpretar("") is Relationship.CONTRADICTS
    assert LLMClassifier._interpretar(None) is Relationship.CONTRADICTS
