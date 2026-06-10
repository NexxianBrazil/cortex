"""O motor da memória: observe() — o coração (Fase 3a).

Preserva a lógica madura do protótipo, agora integrada ao projeto:
  - confiança = autoridade da fonte + qualidade da justificação;
  - verdade decide por confiança (desempate por saliência);
  - só a CONTRADIÇÃO liga o ceticismo, que roteia por verificabilidade/risco;
  - nada é apagado: supersessão é bi-temporal e guarda a razão e a linhagem.

Ajustes aplicados aqui: usa RiskLevel do projeto; fala só com as interfaces
Classifier/AuthorityMap/SourceOfTruth/MemoryStore; e GERA UM EPISÓDIO a cada
observe() (plano episódico) além de atualizar a crença (plano semântico).
"""

import logging

from cortex.memory.classifier import Classifier
from cortex.memory.episodic import Episode
from cortex.memory.models import (
    Justification,
    ModeloMemoria,
    Relationship,
    Source,
    SourceKind,
    Status,
    agora,
)
from cortex.memory.seams import AuthorityMap, SourceOfTruth
from cortex.memory.semantic import Belief
from cortex.memory.store import MemoryStore
from cortex.memory.text import como_numero, normalizar
from cortex.risk import RiskLevel

logger = logging.getLogger("cortex.memory")

# Limiar de magnitude suspeita: valor >= 5x o vigente cheira a erro de digitação
# ou fraude e, sem verificabilidade, puxa o risco para alto.
_MAGNITUDE_SUSPEITA = 5.0


class PendingApproval(ModeloMemoria):
    """Item da fila de aprovação humana — uma contradição de alto risco que o
    agente se RECUSOU a absorver sozinho (memória fica inalterada até decisão).
    """

    key: str
    current_value: str
    proposed_value: str
    source_name: str
    reason: str


class MemoryEngine:
    """Orquestra leitura, ceticismo e escrita sobre os três planos da memória."""

    def __init__(
        self,
        store: MemoryStore,
        classifier: Classifier,
        authority_map: AuthorityMap,
        source_of_truth: SourceOfTruth,
    ) -> None:
        self.store = store
        self.classifier = classifier
        self.authority_map = authority_map
        self.source_of_truth = source_of_truth
        self.pending_approvals: list[PendingApproval] = []

    # ---- leitura ---------------------------------------------------------- #

    def active(self, key: str) -> Belief | None:
        """A crença vigente: maior CONFIANÇA; empate desempata por SALIÊNCIA."""
        cands = [b for b in self.store.beliefs_for(key) if b.status is Status.ACTIVE]
        if not cands:
            return None
        return max(cands, key=lambda b: (b.confidence, b.salience))

    def history(self, key: str) -> list[Belief]:
        """Toda a linha do tempo da chave, do mais antigo ao mais novo."""
        return sorted(self.store.beliefs_for(key), key=lambda b: b.valid_at)

    def beliefs_ativos(self) -> list[Belief]:
        """A crença vigente de CADA chave conhecida — base da recuperação (3c).

        Usa active() por chave (resolve o vencedor por confiança/saliência),
        então o que volta é exatamente o que o Cortex 'sabe' agora.
        """
        chaves = {b.key for b in self.store.all_beliefs()}
        return [a for k in chaves if (a := self.active(k)) is not None]

    # ---- autoridade e magnitude ------------------------------------------ #

    def _authority(self, source: Source, domain: str) -> float:
        """Autoridade da fonte no domínio → componente de verdade da confiança."""
        if self.authority_map.is_authoritative(source.name, domain):
            return 1.0
        if source.kind in (SourceKind.SYSTEM, SourceKind.TOOL, SourceKind.DOCUMENT):
            return 0.9
        if source.kind is SourceKind.HUMAN:
            return 0.5
        return 0.2  # inferência do agente / desconhecido

    def _magnitude_ratio(self, a: str, b: str) -> float:
        """Razão entre dois valores numéricos (1.0 quando não dá para comparar)."""
        na, nb = como_numero(a), como_numero(b)
        if na is None or nb is None or min(na, nb) == 0:
            return 1.0
        return max(na, nb) / min(na, nb)

    # ---- escrita: o coração, com a camada de ceticismo -------------------- #

    def observe(
        self,
        key: str,
        value: str,
        source: Source,
        justification: Justification,
        *,
        domain: str,
    ) -> Episode:
        """Recebe uma afirmação, decide o que fazer e devolve o episódio gerado.

        SEMPRE gera um episódio (registro bruto + decisão), independentemente
        do que acontece com a crença — é a fonte da lineage.
        """
        existing = self.active(key)
        rel = self.classifier.classify(existing, value)
        conf = round(self._authority(source, domain) + justification.quality(), 2)

        # Campos da decisão, preenchidos conforme o ramo tomado.
        dec: dict = {
            "risk": RiskLevel.LOW,
            "action": "",
            "reason": None,
            "magnitude_ratio": None,
            "escalated": False,
            "source_of_truth_consulted": False,
            "source_of_truth_value": None,
            "resulting_belief_id": None,
        }

        if rel is Relationship.INDEPENDENT:
            nova = self._insert(key, value, source, justification, domain, conf)
            dec["action"] = "memorizou (assunto novo)"
            dec["resulting_belief_id"] = nova.id

        elif rel is Relationship.REINFORCES:
            existing.seen_count += 1
            existing.last_seen = agora()
            existing.confidence = max(existing.confidence, conf)
            dec["action"] = f"reforçou (saliência={existing.salience:.0f})"
            dec["resulting_belief_id"] = existing.id

        else:  # CONTRADICTS — agente deixa de ser gravador e vira cético.
            self._resolver_contradicao(
                key, existing, value, source, justification, domain, conf, dec
            )

        episodio = Episode(
            key=key,
            asserted_value=value,
            source=source,
            justification=justification,
            domain=domain,
            relationship=rel,
            **dec,
        )
        self.store.add_episode(episodio)
        logger.info(
            "observe key=%s rel=%s risco=%s ação=%s",
            key,
            rel.value,
            dec["risk"].value,
            dec["action"],
        )
        return episodio

    def _resolver_contradicao(
        self, key, existing, value, source, justification, domain, conf, dec
    ) -> None:
        """Roteia uma contradição: verificável confere; o resto vai por risco."""
        # Regra 1: VERIFICÁVEL não se discute — confere na fonte de verdade.
        if justification.verifiable or existing.justification.verifiable:
            self._verificar_e_resolver(
                key, existing, value, source, justification, domain, conf, dec
            )
            return

        # Regra 2: não-verificável → avalia risco por confiança/magnitude/razão.
        # IMPORTANTE: comparamos GRANDEZAS IGUAIS — `conf` (autoridade da nova +
        # qualidade da justificação, já calculado no observe()) contra
        # `existing.confidence` (mesma fórmula). Antes comparávamos a autoridade
        # CRUA da nova (0–1.0) com a confiança da vigente (0–1.3): um gestor
        # autoritativo no domínio (1.0) perdia para um documento bem justificado
        # (0.9 + 0.2 = 1.1) e a correção do chefe escalava à toa — contra a
        # doutrina "correção autoritativa sobrepõe".
        magnitude = self._magnitude_ratio(existing.value, value)
        grande = magnitude >= _MAGNITUDE_SUSPEITA
        sem_razao = justification.why is None
        dec["magnitude_ratio"] = round(magnitude, 1)

        if conf >= existing.confidence and not grande and not sem_razao:
            # Risco médio: delibera consigo, loga a razão, aceita e supera.
            dec["risk"] = RiskLevel.MEDIUM
            reason = justification.why or "correção de fonte de autoridade ≥"
            nova = self._supersede(
                existing, value, source, justification, domain, conf, reason
            )
            dec["action"] = "deliberou e superou (rebaixou a antiga, não apagou)"
            dec["reason"] = reason
            dec["resulting_belief_id"] = nova.id
            return

        # Risco alto e não dá para conferir → ESCALA. Memória inalterada.
        dec["risk"] = RiskLevel.HIGH
        motivos = []
        if conf < existing.confidence:
            motivos.append("confiança (autoridade+justificação) menor que a da vigente")
        if grande:
            motivos.append(f"magnitude suspeita ({dec['magnitude_ratio']}x)")
        if sem_razao:
            motivos.append("veio sem razão")
        motivo = "; ".join(motivos)
        self.pending_approvals.append(
            PendingApproval(
                key=key,
                current_value=existing.value,
                proposed_value=value,
                source_name=source.name,
                reason=motivo,
            )
        )
        dec["action"] = "ESCALOU para humano (memória inalterada)"
        dec["reason"] = motivo
        dec["escalated"] = True
        # resulting_belief_id fica None: nada mudou na semântica.

    # ---- verificável: confere contra a fonte de verdade ------------------- #

    def _verificar_e_resolver(
        self, key, existing, value, source, justification, domain, conf, dec
    ) -> None:
        """Consulta a fonte de verdade e resolve sem discussão.

        IMPORTANTE (regra do 'valor que apodrece'): registramos no episódio QUE
        a consulta ocorreu e qual valor saiu dela (lineage), mas o valor não
        vira fato permanente — a crença resultante permanece `verifiable=True`
        e será reconferida no próximo conflito. Ver SourceOfTruth.
        """
        consulta = self.source_of_truth.lookup(key)
        dec["source_of_truth_consulted"] = True

        if not consulta.found:
            # Verificável, mas a fonte está indisponível: escala com cautela.
            dec["risk"] = RiskLevel.HIGH
            motivo = "verificável, mas fonte de verdade indisponível"
            self.pending_approvals.append(
                PendingApproval(
                    key=key,
                    current_value=existing.value,
                    proposed_value=value,
                    source_name=source.name,
                    reason=motivo,
                )
            )
            dec["action"] = "ESCALOU (não consegui conferir)"
            dec["reason"] = motivo
            dec["escalated"] = True
            return

        truth = consulta.value
        dec["source_of_truth_value"] = truth

        if normalizar(value) == normalizar(truth):
            nova = self._supersede(
                existing, value, source, justification, domain, conf,
                "confirmado pela fonte de verdade",
            )
            dec["action"] = "conferiu e superou (novo bate com a fonte de verdade)"
            dec["reason"] = "confirmado pela fonte de verdade"
            dec["resulting_belief_id"] = nova.id
        elif normalizar(existing.value) == normalizar(truth):
            existing.seen_count += 1
            dec["action"] = "conferiu e REJEITOU o novo (vigente bate com a fonte)"
            dec["resulting_belief_id"] = existing.id
        else:
            # Nenhum dos dois bate: a fonte de verdade vence ambos. Gravamos o
            # valor real, mas verifiable=True — não é cache permanente, apodrece.
            truth_src = Source(name="Fonte de verdade", kind=SourceKind.SYSTEM)
            nova = self._supersede(
                existing,
                truth,
                truth_src,
                Justification(why="valor real da fonte de verdade", verifiable=True),
                domain,
                1.0,
                "ambos divergiam da fonte de verdade",
            )
            dec["action"] = f"conferiu: nenhum batia; gravou o real ({truth})"
            dec["reason"] = "ambos divergiam da fonte de verdade"
            dec["resulting_belief_id"] = nova.id

    # ---- mutações internas (nunca deletam) -------------------------------- #

    def _insert(
        self, key, value, source, justification, domain, conf
    ) -> Belief:
        belief = Belief(
            key=key,
            value=value,
            source=source,
            justification=justification,
            domain=domain,
            confidence=conf,
        )
        self.store.add_belief(belief)
        return belief

    def _supersede(
        self, old: Belief, value, source, justification, domain, conf, reason
    ) -> Belief:
        """Rebaixa a crença antiga (bi-temporal) e insere a nova com linhagem."""
        old.status = Status.SUPERSEDED  # rebaixa, NÃO apaga
        old.invalid_at = agora()  # até quando valeu
        nova = self._insert(old.key, value, source, justification, domain, conf)
        nova.supersedes = old.id
        nova.reason_for_change = reason  # o porquê vira campo de 1ª classe
        return nova
