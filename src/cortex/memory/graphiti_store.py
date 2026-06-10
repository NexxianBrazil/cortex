"""Persistência da memória no Graphiti + Kuzu (Fase 3b).

ABORDAGEM (decisão validada): usamos APENAS o KuzuDriver do graphiti-core,
com um schema NOSSO de nós/arestas, lido e escrito via Cypher. NUNCA
instanciamos a classe `Graphiti` de alto nível — ela rodaria extração e
resolução de conflitos por LLM, exatamente o que proibimos. Aqui o Graphiti é
só a CAMADA DE PERSISTÊNCIA estruturada e bi-temporal; toda a inteligência de
memória (classificar relação, ceticismo, supersessão, escalonamento) continua
no nosso código (3a). Importar/usar só o driver não exige chave de LLM nenhuma.

Importar este módulo já é o portão de disponibilidade: se graphiti-core[kuzu]
não estiver instalado, o import falha (ImportError) — a factory trata e os
testes de persistência se pulam. O CI nunca depende do Kuzu.

PONTE async→sync E O PROBLEMA DO 'OBJETO VIVO':
O motor da 3a é síncrono e MUTA objetos vivos (reforço faz seen_count++;
supersessão seta status/invalid_at no objeto antigo) e nem sempre chama
add_belief. O InMemoryStore funciona porque devolve as MESMAS referências.
Para um backend durável isso é o clássico problema de identity-map de ORM.
Solução: o GraphitiStore mantém um WORKING SET em memória (um InMemoryStore
interno) como cópia viva da sessão — então a semântica em-sessão é IDÊNTICA
à do InMemoryStore — e usa o Kuzu como backing store: re-serializa o working
set para o Kuzu (checkpoint) a cada escrita e no close(), e HIDRATA o working
set a partir do Kuzu na abertura. Em-sessão = idêntico; durabilidade = reabrir
reconstrói do Kuzu. O observe() não muda.

MAPEAMENTO supersessão → bi-temporal nativo: cada Belief vira um nó com
valid_at/invalid_at e status; o nó antigo NUNCA é apagado (status SUPERSEDED,
invalid_at setado) e ganha uma aresta SUPERSEDES (nova)→(antiga) com a razão.
active() = nós ACTIVE; history() = todos os nós da chave por valid_at.

DÍVIDA TÉCNICA CONSCIENTE (não otimizar agora; resolver junto, na escrita
incremental futura):
  - re-serialização O(n) a cada checkpoint (DETACH DELETE + regravar tudo);
  - backend Kuzu DEPRECADO no graphiti-core upstream (sem manutenção; ele
    recomenda Neo4j/FalkorDB). A migração é localizada porque tudo fala com a
    interface MemoryStore — trocar de backend é uma nova implementação dela;
  - o checkpoint NÃO é atômico: o DETACH DELETE e a regravação são statements
    separados, então uma falha no meio perde dados já persistidos. A escrita
    incremental futura (upsert por nó, sem apagar tudo) resolve isto e o O(n).
"""

import asyncio
import logging
from collections.abc import Coroutine
from datetime import datetime
from pathlib import Path
from typing import Any

from graphiti_core.driver.kuzu_driver import KuzuDriver

from cortex.memory.entity import (
    AttributeOrigin,
    Entity,
    EntityAttribute,
    EntityKind,
    ids_entidade,
)
from cortex.memory.episodic import Episode, ids_episodio
from cortex.memory.learning import (
    Proposal,
    ProposalKind,
    ProposalStatus,
    ids_proposta,
)
from cortex.memory.models import (
    Justification,
    Procedencia,
    Relationship,
    Source,
    SourceKind,
    Status,
)
from cortex.memory.semantic import Belief, ids_crenca
from cortex.memory.store import InMemoryStore, MemoryStore
from cortex.risk import RiskLevel

logger = logging.getLogger("cortex.memory")


def _iso(dt: datetime | None) -> str | None:
    """datetime tz-aware → ISO 8601 (lossless). Guardamos tempo como STRING
    para preservar fuso exatamente, evitando truncamento do TIMESTAMP do Kuzu."""
    return dt.isoformat() if dt is not None else None


def _dt(s: str | None) -> datetime | None:
    """ISO 8601 → datetime (inverso de _iso)."""
    return datetime.fromisoformat(s) if s else None


# NOSSAS tabelas são prefixadas (CortexBelief, ...) porque a KuzuDriver cria
# automaticamente o schema do Graphiti (Entity, Episodic, Community, Saga, ...).
# O prefixo evita qualquer colisão de nome — nós só tocamos nas nossas tabelas.
# DDL: nós primeiro, depois as arestas que os referenciam.
_SCHEMA = [
    """CREATE NODE TABLE IF NOT EXISTS CortexBelief(
        id INT64, key STRING, value STRING,
        source_name STRING, source_kind STRING, source_procedencia STRING,
        why STRING, evidence STRING, verifiable BOOLEAN, proof_pointer STRING,
        domain STRING, confidence DOUBLE,
        valid_at STRING, invalid_at STRING, status STRING,
        seen_count INT64, last_seen STRING,
        reason_for_change STRING, supersedes INT64,
        PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS CortexEpisode(
        id INT64, key STRING, asserted_value STRING,
        source_name STRING, source_kind STRING, source_procedencia STRING,
        why STRING, evidence STRING, verifiable BOOLEAN, proof_pointer STRING,
        domain STRING, occurred_at STRING,
        relationship STRING, risk STRING, action STRING, reason STRING,
        magnitude_ratio DOUBLE, escalated BOOLEAN,
        source_of_truth_consulted BOOLEAN, source_of_truth_value STRING,
        resulting_belief_id INT64,
        PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS CortexEntity(
        id INT64, kind STRING, name STRING, created_at STRING,
        PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS CortexAttribute(
        attr_id STRING, name STRING, value STRING, origin STRING,
        source_name STRING, source_kind STRING,
        PRIMARY KEY(attr_id))""",
    """CREATE NODE TABLE IF NOT EXISTS CortexProposal(
        id INT64, key STRING, current_value STRING, proposed_value STRING,
        source_name STRING, source_kind STRING, source_procedencia STRING,
        why STRING, evidence STRING, verifiable BOOLEAN, proof_pointer STRING,
        domain STRING, risk STRING, reason STRING, origin_episode_id INT64,
        kind STRING, created_at STRING, status STRING,
        decided_at STRING, decided_by STRING, decision_reason STRING,
        consumed_at STRING,
        PRIMARY KEY(id))""",
    "CREATE REL TABLE IF NOT EXISTS CX_SUPERSEDES"
    "(FROM CortexBelief TO CortexBelief, reason STRING)",
    "CREATE REL TABLE IF NOT EXISTS CX_RESULTED_IN(FROM CortexEpisode TO CortexBelief)",
    "CREATE REL TABLE IF NOT EXISTS CX_HAS_ATTRIBUTE(FROM CortexEntity TO CortexAttribute)",
]

# Nossas tabelas de nós (alvo do DETACH DELETE em cada checkpoint).
_NODE_TABLES = (
    "CortexBelief",
    "CortexEpisode",
    "CortexEntity",
    "CortexAttribute",
    "CortexProposal",
)


class GraphitiStore(MemoryStore):
    """MemoryStore durável: working set em memória + persistência em Kuzu."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = str(db_path)
        # Loop privado: o driver é assíncrono; o ABC e o observe() são síncronos.
        self._loop = asyncio.new_event_loop()
        self._driver = KuzuDriver(db=self._db_path)
        self._working = InMemoryStore()  # identity map / cópia viva da sessão
        self._run(self._criar_schema())
        self._hidratar()

    # ---- ponte async→sync ------------------------------------------------- #

    def _run(self, coro: Coroutine) -> Any:
        return self._loop.run_until_complete(coro)

    async def _q(self, cypher: str, **params: Any) -> list[dict]:
        rows, _, _ = await self._driver.execute_query(cypher, **params)
        return rows  # type: ignore[return-value]

    async def _criar_schema(self) -> None:
        for ddl in _SCHEMA:
            await self._q(ddl)
        await self._migrar()

    async def _migrar(self) -> None:
        """Migração best-effort de bancos dev anteriores à coluna de procedência.

        Kuzu suporta ALTER TABLE ADD; em banco já com a coluna (ou recém-criado
        pelo _SCHEMA acima), o ADD falha e é ignorado. É migração MÍNIMA de fase
        dev — o versionamento formal de schema é item aberto do projeto.
        """
        migracoes = [
            ("CortexBelief", "source_procedencia STRING"),
            ("CortexEpisode", "source_procedencia STRING"),
            ("CortexProposal", "kind STRING"),
            ("CortexProposal", "consumed_at STRING"),
        ]
        for tabela, coluna in migracoes:
            try:
                await self._q(f"ALTER TABLE {tabela} ADD {coluna}")
            except Exception:  # noqa: BLE001 — coluna já existe / nada a migrar
                pass

    # ---- contrato MemoryStore: leituras delegam ao working set ----------- #

    def beliefs_for(self, key: str) -> list[Belief]:
        return self._working.beliefs_for(key)

    def all_beliefs(self) -> list[Belief]:
        return self._working.all_beliefs()

    def episodes(self) -> list[Episode]:
        return self._working.episodes()

    def episodes_for(self, key: str) -> list[Episode]:
        return self._working.episodes_for(key)

    def entities(self) -> list[Entity]:
        return self._working.entities()

    def proposals(self, status: ProposalStatus | None = None) -> list[Proposal]:
        return self._working.proposals(status)

    def proposal_by_id(self, proposal_id: int) -> Proposal | None:
        return self._working.proposal_by_id(proposal_id)

    # ---- contrato MemoryStore: escritas atualizam o working set e fazem
    #      checkpoint no Kuzu (captura inclusive mutações in-place do motor) -- #

    def add_belief(self, belief: Belief) -> None:
        self._working.add_belief(belief)
        self._checkpoint()

    def add_episode(self, episode: Episode) -> None:
        self._working.add_episode(episode)
        self._checkpoint()

    def upsert_entity(self, entity: Entity) -> None:
        self._working.upsert_entity(entity)
        self._checkpoint()

    def add_proposal(self, proposal: Proposal) -> None:
        self._working.add_proposal(proposal)
        self._checkpoint()

    # ---- persistência ----------------------------------------------------- #

    def flush(self) -> None:
        """Re-serializa o working set inteiro para o Kuzu (checkpoint público)."""
        self._checkpoint()

    def close(self) -> None:
        """Faz o checkpoint final e encerra driver e loop."""
        self._checkpoint()
        self._run(self._driver.close())
        self._loop.close()

    def _checkpoint(self) -> None:
        self._run(self._aescrever_tudo())

    async def _aescrever_tudo(self) -> None:
        """Re-serialização completa: apaga nossos nós e regrava do working set.

        A crença/episódio em memória é a verdade da sessão; reescrevê-los por
        inteiro é simples e provadamente consistente — captura mutações
        in-place (reforço, mudança de status na supersessão) sem precisar de
        hooks no motor. Custo O(n) por checkpoint; aceitável na escala da
        memória de uma persona e otimizável na 3c.
        """
        for tabela in _NODE_TABLES:
            await self._q(f"MATCH (n:{tabela}) DETACH DELETE n")

        for b in self._working.all_beliefs():
            await self._q(_CREATE_BELIEF, **_belief_params(b))
        # Arestas de lineage da supersessão (nova)→(antiga).
        for b in self._working.all_beliefs():
            if b.supersedes is not None:
                await self._q(
                    "MATCH (n:CortexBelief {id:$nova}),(o:CortexBelief {id:$antiga}) "
                    "CREATE (n)-[:CX_SUPERSEDES {reason:$reason}]->(o)",
                    nova=b.id,
                    antiga=b.supersedes,
                    reason=b.reason_for_change,
                )

        for e in self._working.all_episodes():
            await self._q(_CREATE_EPISODE, **_episode_params(e))
            if e.resulting_belief_id is not None:
                await self._q(
                    "MATCH (e:CortexEpisode {id:$eid}),(b:CortexBelief {id:$bid}) "
                    "CREATE (e)-[:CX_RESULTED_IN]->(b)",
                    eid=e.id,
                    bid=e.resulting_belief_id,
                )

        for ent in self._working.all_entities():
            await self._q(_CREATE_ENTITY, **_entity_params(ent))
            for idx, attr in enumerate(ent.attributes):
                attr_id = f"{ent.id}:{idx}"
                await self._q(_CREATE_ATTRIBUTE, attr_id=attr_id, **_attr_params(attr))
                await self._q(
                    "MATCH (x:CortexEntity {id:$eid}),(a:CortexAttribute {attr_id:$aid}) "
                    "CREATE (x)-[:CX_HAS_ATTRIBUTE]->(a)",
                    eid=ent.id,
                    aid=attr_id,
                )

        for p in self._working.all_proposals():
            await self._q(_CREATE_PROPOSAL, **_proposal_params(p))

    # ---- hidratação: reconstrói o working set a partir do Kuzu ------------ #

    def _hidratar(self) -> None:
        self._run(self._ahidratar())

    async def _ahidratar(self) -> None:
        for row in await self._q(_SELECT_BELIEFS):
            self._working.add_belief(_belief_de_row(row))
        for row in await self._q(_SELECT_EPISODES):
            self._working.add_episode(_episode_de_row(row))
        entidades: dict[int, Entity] = {}
        for row in await self._q(_SELECT_ENTITIES):
            ent = Entity(
                id=row["id"],
                kind=EntityKind(row["kind"]),
                name=row["name"],
                created_at=_dt(row["created_at"]),
                attributes=[],
            )
            entidades[ent.id] = ent
        for row in await self._q(_SELECT_ATTRIBUTES):
            ent = entidades.get(row["eid"])
            if ent is not None:
                ent.attributes.append(
                    EntityAttribute(
                        name=row["name"],
                        value=row["value"],
                        origin=AttributeOrigin(row["origin"]),
                        source=(
                            Source(name=row["source_name"], kind=SourceKind(row["source_kind"]))
                            if row["source_name"] is not None
                            else None
                        ),
                    )
                )
        for ent in entidades.values():
            self._working.upsert_entity(ent)

        for row in await self._q(_SELECT_PROPOSALS):
            self._working.add_proposal(_proposal_de_row(row))

        # CRÍTICO: avança os contadores de id para além do que foi hidratado.
        # Sem isto, num processo novo os contadores recomeçam do 1 e colidem
        # com a PRIMARY KEY dos nós já no Kuzu — a 1ª escrita quebraria o
        # checkpoint (e, como ele faz DETACH DELETE antes de regravar,
        # corromperia o banco). Ver Contador.garantir_minimo em models.py.
        beliefs = self._working.all_beliefs()
        episodes = self._working.episodes()
        entidades_lst = self._working.entities()
        propostas = self._working.all_proposals()
        ids_crenca.garantir_minimo(max((b.id for b in beliefs), default=0) + 1)
        ids_episodio.garantir_minimo(max((e.id for e in episodes), default=0) + 1)
        ids_entidade.garantir_minimo(max((x.id for x in entidades_lst), default=0) + 1)
        ids_proposta.garantir_minimo(max((p.id for p in propostas), default=0) + 1)


# --------------------------------------------------------------------------- #
# Serialização modelo ↔ linha (sem reescrever modelos: só mapeamento)         #
# --------------------------------------------------------------------------- #

_CREATE_BELIEF = """CREATE (b:CortexBelief {
    id:$id, key:$key, value:$value,
    source_name:$source_name, source_kind:$source_kind, source_procedencia:$source_procedencia,
    why:$why, evidence:$evidence, verifiable:$verifiable, proof_pointer:$proof_pointer,
    domain:$domain, confidence:$confidence,
    valid_at:$valid_at, invalid_at:$invalid_at, status:$status,
    seen_count:$seen_count, last_seen:$last_seen,
    reason_for_change:$reason_for_change, supersedes:$supersedes})"""

_CREATE_EPISODE = """CREATE (e:CortexEpisode {
    id:$id, key:$key, asserted_value:$asserted_value,
    source_name:$source_name, source_kind:$source_kind, source_procedencia:$source_procedencia,
    why:$why, evidence:$evidence, verifiable:$verifiable, proof_pointer:$proof_pointer,
    domain:$domain, occurred_at:$occurred_at,
    relationship:$relationship, risk:$risk, action:$action, reason:$reason,
    magnitude_ratio:$magnitude_ratio, escalated:$escalated,
    source_of_truth_consulted:$source_of_truth_consulted,
    source_of_truth_value:$source_of_truth_value,
    resulting_belief_id:$resulting_belief_id})"""

_CREATE_ENTITY = "CREATE (x:CortexEntity {id:$id, kind:$kind, name:$name, created_at:$created_at})"

_CREATE_ATTRIBUTE = """CREATE (a:CortexAttribute {
    attr_id:$attr_id, name:$name, value:$value, origin:$origin,
    source_name:$source_name, source_kind:$source_kind})"""

_SELECT_BELIEFS = """MATCH (b:CortexBelief) RETURN
    b.id AS id, b.key AS key, b.value AS value,
    b.source_name AS source_name, b.source_kind AS source_kind,
    b.source_procedencia AS source_procedencia,
    b.why AS why, b.evidence AS evidence, b.verifiable AS verifiable,
    b.proof_pointer AS proof_pointer, b.domain AS domain, b.confidence AS confidence,
    b.valid_at AS valid_at, b.invalid_at AS invalid_at, b.status AS status,
    b.seen_count AS seen_count, b.last_seen AS last_seen,
    b.reason_for_change AS reason_for_change, b.supersedes AS supersedes"""

_SELECT_EPISODES = """MATCH (e:CortexEpisode) RETURN
    e.id AS id, e.key AS key, e.asserted_value AS asserted_value,
    e.source_name AS source_name, e.source_kind AS source_kind,
    e.source_procedencia AS source_procedencia,
    e.why AS why, e.evidence AS evidence, e.verifiable AS verifiable,
    e.proof_pointer AS proof_pointer, e.domain AS domain, e.occurred_at AS occurred_at,
    e.relationship AS relationship, e.risk AS risk, e.action AS action, e.reason AS reason,
    e.magnitude_ratio AS magnitude_ratio, e.escalated AS escalated,
    e.source_of_truth_consulted AS source_of_truth_consulted,
    e.source_of_truth_value AS source_of_truth_value,
    e.resulting_belief_id AS resulting_belief_id
    ORDER BY e.id"""

_SELECT_ENTITIES = """MATCH (x:CortexEntity) RETURN
    x.id AS id, x.kind AS kind, x.name AS name, x.created_at AS created_at"""

_SELECT_ATTRIBUTES = """MATCH (x:CortexEntity)-[:CX_HAS_ATTRIBUTE]->(a:CortexAttribute) RETURN
    x.id AS eid, a.name AS name, a.value AS value, a.origin AS origin,
    a.source_name AS source_name, a.source_kind AS source_kind
    ORDER BY a.attr_id"""


def _proc(source: Source) -> str:
    """Valor da procedência da fonte para persistir."""
    return source.procedencia.value


def _source_de_row(r: dict) -> Source:
    """Reconstrói Source da linha; procedência ausente/None → INTERNA (migração)."""
    proc = r.get("source_procedencia")
    return Source(
        name=r["source_name"],
        kind=SourceKind(r["source_kind"]),
        procedencia=Procedencia(proc) if proc else Procedencia.INTERNA,
    )


def _belief_params(b: Belief) -> dict[str, Any]:
    return {
        "id": b.id,
        "key": b.key,
        "value": b.value,
        "source_name": b.source.name,
        "source_kind": b.source.kind.value,
        "source_procedencia": _proc(b.source),
        "why": b.justification.why,
        "evidence": b.justification.evidence,
        "verifiable": b.justification.verifiable,
        "proof_pointer": b.justification.proof_pointer,
        "domain": b.domain,
        "confidence": b.confidence,
        "valid_at": _iso(b.valid_at),
        "invalid_at": _iso(b.invalid_at),
        "status": b.status.value,
        "seen_count": b.seen_count,
        "last_seen": _iso(b.last_seen),
        "reason_for_change": b.reason_for_change,
        "supersedes": b.supersedes,
    }


def _belief_de_row(r: dict) -> Belief:
    return Belief(
        id=r["id"],
        key=r["key"],
        value=r["value"],
        source=_source_de_row(r),
        justification=Justification(
            why=r["why"],
            evidence=r["evidence"],
            verifiable=r["verifiable"],
            proof_pointer=r["proof_pointer"],
        ),
        domain=r["domain"],
        confidence=r["confidence"],
        valid_at=_dt(r["valid_at"]),
        invalid_at=_dt(r["invalid_at"]),
        status=Status(r["status"]),
        seen_count=r["seen_count"],
        last_seen=_dt(r["last_seen"]),
        reason_for_change=r["reason_for_change"],
        supersedes=r["supersedes"],
    )


def _episode_params(e: Episode) -> dict[str, Any]:
    return {
        "id": e.id,
        "key": e.key,
        "asserted_value": e.asserted_value,
        "source_name": e.source.name,
        "source_kind": e.source.kind.value,
        "source_procedencia": _proc(e.source),
        "why": e.justification.why,
        "evidence": e.justification.evidence,
        "verifiable": e.justification.verifiable,
        "proof_pointer": e.justification.proof_pointer,
        "domain": e.domain,
        "occurred_at": _iso(e.occurred_at),
        "relationship": e.relationship.value,
        "risk": e.risk.value,
        "action": e.action,
        "reason": e.reason,
        "magnitude_ratio": e.magnitude_ratio,
        "escalated": e.escalated,
        "source_of_truth_consulted": e.source_of_truth_consulted,
        "source_of_truth_value": e.source_of_truth_value,
        "resulting_belief_id": e.resulting_belief_id,
    }


def _episode_de_row(r: dict) -> Episode:
    return Episode(
        id=r["id"],
        key=r["key"],
        asserted_value=r["asserted_value"],
        source=_source_de_row(r),
        justification=Justification(
            why=r["why"],
            evidence=r["evidence"],
            verifiable=r["verifiable"],
            proof_pointer=r["proof_pointer"],
        ),
        domain=r["domain"],
        occurred_at=_dt(r["occurred_at"]),
        relationship=Relationship(r["relationship"]),
        risk=RiskLevel(r["risk"]),
        action=r["action"],
        reason=r["reason"],
        magnitude_ratio=r["magnitude_ratio"],
        escalated=r["escalated"],
        source_of_truth_consulted=r["source_of_truth_consulted"],
        source_of_truth_value=r["source_of_truth_value"],
        resulting_belief_id=r["resulting_belief_id"],
    )


def _entity_params(ent: Entity) -> dict[str, Any]:
    return {
        "id": ent.id,
        "kind": ent.kind.value,
        "name": ent.name,
        "created_at": _iso(ent.created_at),
    }


def _attr_params(attr: EntityAttribute) -> dict[str, Any]:
    return {
        "name": attr.name,
        "value": attr.value,
        "origin": attr.origin.value,
        "source_name": attr.source.name if attr.source else None,
        "source_kind": attr.source.kind.value if attr.source else None,
    }


# --- learning queue (Plano 6) ---------------------------------------------- #

_CREATE_PROPOSAL = """CREATE (p:CortexProposal {
    id:$id, key:$key, current_value:$current_value, proposed_value:$proposed_value,
    source_name:$source_name, source_kind:$source_kind, source_procedencia:$source_procedencia,
    why:$why, evidence:$evidence, verifiable:$verifiable, proof_pointer:$proof_pointer,
    domain:$domain, risk:$risk, reason:$reason, origin_episode_id:$origin_episode_id,
    kind:$kind, created_at:$created_at, status:$status,
    decided_at:$decided_at, decided_by:$decided_by, decision_reason:$decision_reason,
    consumed_at:$consumed_at})"""

_SELECT_PROPOSALS = """MATCH (p:CortexProposal) RETURN
    p.id AS id, p.key AS key, p.current_value AS current_value,
    p.proposed_value AS proposed_value,
    p.source_name AS source_name, p.source_kind AS source_kind,
    p.source_procedencia AS source_procedencia,
    p.why AS why, p.evidence AS evidence, p.verifiable AS verifiable,
    p.proof_pointer AS proof_pointer, p.domain AS domain, p.risk AS risk,
    p.reason AS reason, p.origin_episode_id AS origin_episode_id,
    p.kind AS kind, p.created_at AS created_at, p.status AS status,
    p.decided_at AS decided_at, p.decided_by AS decided_by,
    p.decision_reason AS decision_reason, p.consumed_at AS consumed_at
    ORDER BY p.id"""


def _proposal_params(p: Proposal) -> dict[str, Any]:
    return {
        "id": p.id,
        "key": p.key,
        "current_value": p.current_value,
        "proposed_value": p.proposed_value,
        "source_name": p.source.name,
        "source_kind": p.source.kind.value,
        "source_procedencia": _proc(p.source),
        "why": p.justification.why,
        "evidence": p.justification.evidence,
        "verifiable": p.justification.verifiable,
        "proof_pointer": p.justification.proof_pointer,
        "domain": p.domain,
        "risk": p.risk.value,
        "reason": p.reason,
        "origin_episode_id": p.origin_episode_id,
        "kind": p.kind.value,
        "created_at": _iso(p.created_at),
        "status": p.status.value,
        "decided_at": _iso(p.decided_at),
        "decided_by": p.decided_by,
        "decision_reason": p.decision_reason,
        "consumed_at": _iso(p.consumed_at),
    }


def _proposal_de_row(r: dict) -> Proposal:
    return Proposal(
        id=r["id"],
        key=r["key"],
        current_value=r["current_value"],
        proposed_value=r["proposed_value"],
        source=_source_de_row(r),
        justification=Justification(
            why=r["why"],
            evidence=r["evidence"],
            verifiable=r["verifiable"],
            proof_pointer=r["proof_pointer"],
        ),
        domain=r["domain"],
        risk=RiskLevel(r["risk"]),
        reason=r["reason"],
        origin_episode_id=r["origin_episode_id"],
        kind=ProposalKind(r["kind"]) if r.get("kind") else ProposalKind.MEMORIA,
        created_at=_dt(r["created_at"]),
        status=ProposalStatus(r["status"]),
        decided_at=_dt(r["decided_at"]),
        decided_by=r["decided_by"],
        decision_reason=r["decision_reason"],
        consumed_at=_dt(r.get("consumed_at")),
    )
