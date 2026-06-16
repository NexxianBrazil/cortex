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
from cortex.memory.learning import Proposal, ProposalKind, ProposalStatus
from cortex.memory.models import (
    Justification,
    Procedencia,
    Relationship,
    Source,
    SourceKind,
    Status,
    agora,
)
from cortex.memory.seams import AuthorityMap, SourceOfTruth
from cortex.memory.semantic import Belief
from cortex.memory.store import MemoryStore
from cortex.memory.text import como_numero, valores_equivalentes
from cortex.risk import RiskLevel

logger = logging.getLogger("cortex.memory")

# Limiar de magnitude suspeita: valor >= 5x o vigente cheira a erro de digitação
# ou fraude e, sem verificabilidade, puxa o risco para alto.
_MAGNITUDE_SUSPEITA = 5.0


class AutoridadeInsuficienteError(Exception):
    """Quem tenta decidir não é autoritativo no domínio da proposta.

    Fecha o ciclo USER.md → authority map → decisão: só quem MANDA no domínio
    pode aprovar/rejeitar uma proposta.
    """


class PropostaJaDecididaError(Exception):
    """Tentativa de decidir uma proposta que já saiu de PENDENTE."""


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

    # ---- leitura ---------------------------------------------------------- #

    @property
    def pending_approvals(self) -> list[Proposal]:
        """Propostas pendentes — a Learning Queue persistida (não mais volátil)."""
        return self.store.proposals(ProposalStatus.PENDENTE)

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
        """Autoridade da fonte no domínio → componente de verdade da confiança.

        A autoridade segue o CANAL, não o conteúdo (ver Procedencia):
          - o branch autoritativo (1.0) só vale para procedência INTERNA — um
            nome igual ao do gestor vindo de canal EXTERNO não compra 1.0
            (anti-spoofing do authority map);
          - procedência EXTERNA tem TETO 0.4 (abaixo do humano interno 0.5): o
            cliente é dono do fato dele e a informação entra, mas qualquer
            contradição relevante liga o cético e tende a escalar.
        """
        externa = source.procedencia is Procedencia.EXTERNA

        # Autoridade plena por authority map: SÓ para canal interno autenticado.
        if not externa and self.authority_map.is_authoritative(source.name, domain):
            return 1.0

        if source.kind in (SourceKind.SYSTEM, SourceKind.TOOL, SourceKind.DOCUMENT):
            base = 0.9
        elif source.kind is SourceKind.HUMAN:
            base = 0.5
        else:
            base = 0.2  # inferência do agente / desconhecido

        return min(base, 0.4) if externa else base

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

        # Escalonamento → cria a PROPOSTA (Learning Queue, Plano 6). Ponto ÚNICO
        # de criação: os ramos só preencheram dec["escalated"]; o warrant
        # completo (com a linhagem ao episódio de origem) nasce aqui.
        if dec["escalated"]:
            self.store.add_proposal(
                Proposal(
                    key=key,
                    current_value=existing.value if existing is not None else None,
                    proposed_value=value,
                    source=source,
                    justification=justification,
                    domain=domain,
                    risk=dec["risk"],
                    reason=dec["reason"] or "",
                    origin_episode_id=episodio.id,
                )
            )

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
        dec["action"] = "ESCALOU para humano (memória inalterada)"
        dec["reason"] = motivo
        dec["escalated"] = True
        # resulting_belief_id fica None: nada mudou na semântica. A PROPOSTA é
        # criada no observe() (ponto único), a partir de dec["escalated"].

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
            dec["action"] = "ESCALOU (não consegui conferir)"
            dec["reason"] = "verificável, mas fonte de verdade indisponível"
            dec["escalated"] = True
            return

        truth = consulta.value
        dec["source_of_truth_value"] = truth

        # Comparação NUMÉRICA quando ambos os lados são números (valores do
        # domínio são reais): '1250' do humano CONFIRMA truth 'R$ 1.250,00' em
        # vez de ser falsamente rejeitado por diferença de formatação. Para
        # valores não-numéricos, cai no textual normalizado (ver text.py).
        if valores_equivalentes(value, truth):
            nova = self._supersede(
                existing, value, source, justification, domain, conf,
                "confirmado pela fonte de verdade",
            )
            dec["action"] = "conferiu e superou (novo bate com a fonte de verdade)"
            dec["reason"] = "confirmado pela fonte de verdade"
            dec["resulting_belief_id"] = nova.id
        elif valores_equivalentes(existing.value, truth):
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

    # ---- decisão humana sobre a Learning Queue (Plano 6) ------------------ #

    def _validar_decisao(self, proposal_id: int, autor: str) -> Proposal:
        """Garante que a proposta existe, está PENDENTE e que `autor` pode decidir.

        Governança: só quem é autoritativo no domínio da proposta decide — o
        ciclo USER.md → authority map → decisão.
        """
        p = self.store.proposal_by_id(proposal_id)
        if p is None:
            raise ValueError(f"proposta {proposal_id} inexistente")
        if p.status is not ProposalStatus.PENDENTE:
            raise PropostaJaDecididaError(
                f"proposta {proposal_id} já está {p.status.value}"
            )
        if not self.authority_map.is_authoritative(autor, p.domain):
            raise AutoridadeInsuficienteError(
                f"'{autor}' não é autoritativo no domínio '{p.domain}' — não pode decidir"
            )
        return p

    def _registrar_decisao(
        self,
        p: Proposal,
        autor: str,
        *,
        action: str,
        reason: str,
        resulting_belief_id: int | None,
        escalated: bool = False,
    ) -> Episode:
        """Gera o episódio da decisão humana — a decisão é MEMÓRIA com autor.

        A fonte é o humano que decidiu (HUMAN, canal interno). Gravar o episódio
        dispara o checkpoint do GraphitiStore, que captura também a mutação de
        status da proposta no working set (mesmo mecanismo do reforço de crença).
        """
        episodio = Episode(
            key=p.key,
            asserted_value=p.proposed_value,
            source=Source(name=autor, kind=SourceKind.HUMAN),
            justification=Justification(why=reason),
            domain=p.domain,
            relationship=Relationship.CONTRADICTS,
            risk=p.risk,
            action=action,
            reason=reason,
            escalated=escalated,
            resulting_belief_id=resulting_belief_id,
        )
        self.store.add_episode(episodio)
        return episodio

    def aprovar(self, proposal_id: int, autor: str, razao: str) -> Episode:
        """Humano autoritativo APROVA a proposta: aplica a escrita governada.

        Checa CADUCIDADE: se a crença vigente da chave mudou desde o
        escalonamento, a proposta não se aplica (aplicar uma escrita aprovada
        sobre um mundo que já mudou seria mutação cega) — status CADUCADA e
        episódio explicando. Caso vigente, aplica a supersessão (ou inserção,
        se era assunto novo) com a FONTE e a JUSTIFICAÇÃO ORIGINAIS — quem
        afirmou continua autor do fato; o humano apenas autoriza.
        """
        p = self._validar_decisao(proposal_id, autor)

        # Proposta de AÇÃO (Fase 4c): SEM escrita de crença e SEM caducidade
        # (não se aplica). Aprovar = liberar a exceção one-shot; consumida
        # depois pelo DecisionEngine via consumir_excecao.
        if p.kind is ProposalKind.ACAO:
            tool = p.key.removeprefix("acao:")
            p.status = ProposalStatus.APROVADA
            p.decided_at = agora()
            p.decided_by = autor
            p.decision_reason = razao
            return self._registrar_decisao(
                p,
                autor,
                action=f"humano APROVOU a ação {tool} (proposta {p.id})",
                reason=razao,
                resulting_belief_id=None,
            )

        # Proposta de REVISÃO (Fase 8): pedido de atenção da reflexão batch. NÃO
        # propõe valor — aprovar é RECONHECER (a régua foi revista), sem mutar
        # crença nem caducar. A decisão vira episódio com autor, como as demais.
        if p.kind is ProposalKind.REVISAR:
            p.status = ProposalStatus.APROVADA
            p.decided_at = agora()
            p.decided_by = autor
            p.decision_reason = razao
            return self._registrar_decisao(
                p,
                autor,
                action=f"humano RECONHECEU a revisão de '{p.key}' (proposta {p.id})",
                reason=razao,
                resulting_belief_id=None,
            )

        vigente = self.active(p.key)
        vigente_valor = vigente.value if vigente is not None else None
        if vigente_valor != p.current_value:
            p.status = ProposalStatus.CADUCADA
            p.decided_at = agora()
            p.decided_by = autor
            p.decision_reason = razao
            return self._registrar_decisao(
                p,
                autor,
                action="proposta caducou: a crença vigente mudou desde o escalonamento",
                reason=razao,
                resulting_belief_id=None,
            )

        conf = round(self._authority(p.source, p.domain) + p.justification.quality(), 2)
        reason_change = f"aprovado por {autor}: {razao}"
        if vigente is None:
            nova = self._insert(
                p.key, p.proposed_value, p.source, p.justification, p.domain, conf
            )
            nova.reason_for_change = reason_change
        else:
            nova = self._supersede(
                vigente, p.proposed_value, p.source, p.justification, p.domain,
                conf, reason_change,
            )

        p.status = ProposalStatus.APROVADA
        p.decided_at = agora()
        p.decided_by = autor
        p.decision_reason = razao
        return self._registrar_decisao(
            p,
            autor,
            action=f"humano APROVOU a proposta {p.id} — crença superada",
            reason=razao,
            resulting_belief_id=nova.id,
        )

    def rejeitar(self, proposal_id: int, autor: str, razao: str) -> Episode:
        """Humano autoritativo REJEITA: memória INALTERADA, mas vira episódio.

        A rejeição é episódio de PRIMEIRA CLASSE — revisitável numa conversa
        futura ('o chefe pode errar'). A governança presta contas dos dois lados.
        """
        p = self._validar_decisao(proposal_id, autor)
        p.status = ProposalStatus.REJEITADA
        p.decided_at = agora()
        p.decided_by = autor
        p.decision_reason = razao
        if p.kind is ProposalKind.ACAO:
            tool = p.key.removeprefix("acao:")
            action = f"humano REJEITOU a ação {tool} (proposta {p.id})"
        else:
            action = f"humano REJEITOU a proposta {p.id} — memória inalterada"
        return self._registrar_decisao(
            p, autor, action=action, reason=razao, resulting_belief_id=None
        )

    def consumir_excecao(self, tool: str, argumentos_json: str) -> Proposal | None:
        """Consome (one-shot) uma exceção de AÇÃO aprovada para esta chamada.

        Procura proposta ACAO + APROVADA + NÃO consumida cujo proposed_value
        bata EXATAMENTE com `argumentos_json` (match exato de tool+args — a
        aprovação não é carta branca, é para AQUELA chamada). Se achar, marca
        consumed_at, registra um episódio e devolve a proposta; senão, None.

        Injetada no DecisionEngine como callable (a governança não importa a
        memória). Gravar o episódio dispara o checkpoint do GraphitiStore, que
        captura a mutação de consumed_at no working set.
        """
        chave = f"acao:{tool}"
        for p in self.store.proposals(ProposalStatus.APROVADA):
            if (
                p.kind is ProposalKind.ACAO
                and p.consumed_at is None
                and p.key == chave
                and p.proposed_value == argumentos_json
            ):
                p.consumed_at = agora()
                self._registrar_decisao(
                    p,
                    p.decided_by or "humano",
                    action=(
                        f"exceção da proposta {p.id} consumida — ação executada "
                        "com aprovação humana"
                    ),
                    reason="exceção one-shot consumida",
                    resulting_belief_id=None,
                )
                return p
        return None

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
