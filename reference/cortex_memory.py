"""
Cortex — Núcleo do Motor de Memória (protótipo rodável)
========================================================

Materializa o conceito que fechamos nas discussões de desenho:

  1. A unidade de memória NÃO é o fato — é a CRENÇA JUSTIFICADA:
     fato + por quê + evidência + fonte + verificabilidade.
  2. Nunca apaga. Rebaixa/invalida (bi-temporal), guardando a RAZÃO da
     mudança e a linhagem (quem disse, quando, de onde veio).
  3. DOIS scores separados, nunca um só:
         salience   (recência + frequência)  -> manda no RANKING
         confidence (autoridade da fonte)     -> manda na VERDADE
     Frequência != verdade: correção autoritativa sobrepõe erro repetido.
  4. Camada ATIVA de ceticismo. Ao chegar algo que CONTRADIZ uma crença,
     o agente não é gravador passivo — avalia risco e ROTEIA:
         baixo  -> aceita e registra
         médio  -> delibera consigo, loga o raciocínio e aceita
         alto   -> verificável:     checa a fonte de verdade (não discute)
                   não-verificável: ESCALA para aprovação humana
  5. Split verificável vs não-verificável é o que decide entre "vai conferir"
     e "vai escalar".

As bordas externas entram como STUBS, marcadas com `# >>> SEAM`, para trocar
por infra real no passo 2 (LLM de julgamento, mapa de autoridade do Data
Plane, fonte de verdade tipo SAP, persistência Graphiti/SQL).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import itertools
import re

now = lambda: datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Tipos de base                                                               #
# --------------------------------------------------------------------------- #

class SourceKind(Enum):
    HUMAN = "humano"
    SYSTEM = "sistema"          # ex.: SAP, banco
    DOCUMENT = "documento"      # ex.: política em PDF
    TOOL = "ferramenta"
    AGENT = "inferência do agente"


class Relationship(Enum):
    INDEPENDENT = "independente"   # assunto novo, não toca nada
    REINFORCES = "reforça"         # mesmo valor de novo -> ganha saliência
    CONTRADICTS = "contradiz"      # valor diferente para a mesma chave


class Risk(Enum):
    LOW = "baixo"
    MEDIUM = "médio"
    HIGH = "alto"


class Status(Enum):
    ACTIVE = "ativa"
    SUPERSEDED = "superada"        # invalidada, NÃO apagada
    REJECTED = "rejeitada"         # contradita pela fonte de verdade


@dataclass
class Source:
    name: str
    kind: SourceKind


@dataclass
class Justification:
    """O 'porquê' que dá lastro à crença. É o que o agente cobra ao questionar."""
    why: Optional[str] = None          # razão causal
    evidence: Optional[str] = None     # prova / referência
    verifiable: bool = False           # dá pra conferir contra fonte de verdade?
    proof_pointer: Optional[str] = None  # ex.: "SAP:VBAK/4471", "politica.pdf#3"

    def quality(self) -> float:
        q = 0.0
        if self.why:
            q += 0.10
        if self.evidence:
            q += 0.10
        if self.verifiable:
            q += 0.10
        return q


_ids = itertools.count(1)


@dataclass
class Belief:
    key: str                 # chave canônica do fato (ex.: "cliente:ABC:prazo")
    value: str
    source: Source
    justification: Justification
    domain: str              # domínio do assunto (ex.: "comercial")
    confidence: float        # autoridade -> verdade
    valid_at: datetime = field(default_factory=now)
    invalid_at: Optional[datetime] = None   # bi-temporal: setado ao superar
    status: Status = Status.ACTIVE
    seen_count: int = 1
    last_seen: datetime = field(default_factory=now)
    reason_for_change: Optional[str] = None
    supersedes: Optional[int] = None        # id da crença que esta substitui
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def salience(self) -> float:
        # recência + frequência. Decaimento por recência é um SEAM (aqui só freq).
        return float(self.seen_count)


# --------------------------------------------------------------------------- #
# O motor                                                                     #
# --------------------------------------------------------------------------- #

class MemoryEngine:
    def __init__(self, authority_map: dict, source_of_truth: dict):
        # >>> SEAM: authority_map vem do Data Plane (config por cliente).
        #     domínio -> conjunto de nomes de fonte autoritativa naquele domínio.
        self.authority_map = authority_map
        # >>> SEAM: source_of_truth simula SAP/doc/tool. Na vida real é uma
        #     chamada ao vivo, não um dict.
        self.source_of_truth = source_of_truth
        self.store: dict[str, list[Belief]] = {}
        self.pending_approvals: list[dict] = []

    # ---- leitura ---------------------------------------------------------- #

    def active(self, key: str) -> Optional[Belief]:
        cands = [b for b in self.store.get(key, []) if b.status == Status.ACTIVE]
        if not cands:
            return None
        # Verdade decide por CONFIANÇA; empate desempata por saliência.
        return max(cands, key=lambda b: (b.confidence, b.salience))

    def history(self, key: str) -> list[Belief]:
        return sorted(self.store.get(key, []), key=lambda b: b.valid_at)

    # ---- autoridade e julgamento (SEAMS de LLM/config) -------------------- #

    def _authority(self, source: Source, domain: str) -> float:
        if source.name in self.authority_map.get(domain, set()):
            return 1.0
        if source.kind in (SourceKind.SYSTEM, SourceKind.TOOL, SourceKind.DOCUMENT):
            return 0.9
        if source.kind == SourceKind.HUMAN:
            return 0.5
        return 0.2  # inferência do agente / desconhecido

    def _classify(self, existing: Optional[Belief], value: str) -> Relationship:
        # >>> SEAM: aqui entra o LLM (ideia do HiMem: independente/estende/contradiz).
        #     Heurística determinística para o protótipo:
        if existing is None:
            return Relationship.INDEPENDENT
        if _norm(existing.value) == _norm(value):
            return Relationship.REINFORCES
        return Relationship.CONTRADICTS

    def _magnitude_ratio(self, a: str, b: str) -> float:
        na, nb = _as_number(a), _as_number(b)
        if na is None or nb is None or min(na, nb) == 0:
            return 1.0
        return max(na, nb) / min(na, nb)

    # ---- escrita: o coração, com a camada de ceticismo -------------------- #

    def observe(self, key, value, source: Source, justification: Justification,
                *, domain: str) -> dict:
        """Recebe uma afirmação nova e decide o que fazer. Devolve um traço."""
        existing = self.active(key)
        rel = self._classify(existing, value)
        conf = round(self._authority(source, domain) + justification.quality(), 2)
        log = {"key": key, "value": value, "fonte": source.name,
               "relacao": rel.value}

        if rel is Relationship.INDEPENDENT:
            self._insert(key, value, source, justification, domain, conf)
            log.update(acao="memorizou (assunto novo)", risco=Risk.LOW.value)
            return log

        if rel is Relationship.REINFORCES:
            existing.seen_count += 1
            existing.last_seen = now()
            existing.confidence = max(existing.confidence, conf)
            log.update(acao=f"reforçou (saliência={existing.salience:.0f})",
                       risco=Risk.LOW.value)
            return log

        # --- CONTRADIÇÃO: aqui o agente para de ser gravador e vira cético --- #

        # Regra 1: se é VERIFICÁVEL, não discute — vai à fonte de verdade.
        if justification.verifiable or existing.justification.verifiable:
            return self._verify_and_resolve(key, existing, value, source,
                                            justification, domain, conf, log)

        # Regra 2: não-verificável -> avalia risco por autoridade/magnitude/razão.
        auth_in = self._authority(source, domain)
        auth_ex = existing.confidence
        big = self._magnitude_ratio(existing.value, value) >= 5.0
        no_reason = justification.why is None
        log["magnitude"] = round(self._magnitude_ratio(existing.value, value), 1)

        if auth_in >= auth_ex and not big and not no_reason:
            risk = Risk.MEDIUM
        else:
            risk = Risk.HIGH
        log["risco"] = risk.value

        if risk is Risk.MEDIUM:
            # Delibera consigo, loga o raciocínio, aceita e supera com razão.
            reason = justification.why or "correção de fonte de autoridade ≥"
            self._supersede(existing, value, source, justification, domain,
                            conf, reason)
            log.update(acao="deliberou e superou (rebaixou a antiga, não apagou)",
                       razao=reason)
            return log

        # Risco ALTO e não dá pra conferir -> ESCALA. Não muda a memória.
        motivo = []
        if auth_in < auth_ex:
            motivo.append("fonte de autoridade menor que a vigente")
        if big:
            motivo.append(f"magnitude suspeita ({log['magnitude']}x)")
        if no_reason:
            motivo.append("veio sem razão")
        self.pending_approvals.append(
            {"key": key, "vigente": existing.value, "proposto": value,
             "fonte": source.name, "motivo": "; ".join(motivo)})
        log.update(acao="ESCALOU para humano (memória inalterada)",
                   razao="; ".join(motivo))
        return log

    # ---- verificável: confere contra a fonte de verdade ------------------- #

    def _verify_and_resolve(self, key, existing, value, source, justification,
                            domain, conf, log) -> dict:
        truth = self.source_of_truth.get(key)  # >>> SEAM: chamada SAP ao vivo
        if truth is None:
            self.pending_approvals.append(
                {"key": key, "vigente": existing.value, "proposto": value,
                 "fonte": source.name, "motivo": "verificável, mas fonte de verdade indisponível"})
            log.update(acao="ESCALOU (não consegui conferir)", risco=Risk.HIGH.value)
            return log

        log["fonte_de_verdade"] = truth
        if _norm(value) == _norm(truth):
            self._supersede(existing, value, source, justification, domain, conf,
                            "confirmado pela fonte de verdade")
            log.update(acao="conferiu e superou (novo bate com a fonte de verdade)",
                       risco=Risk.LOW.value)
        elif _norm(existing.value) == _norm(truth):
            existing.seen_count += 1
            log.update(acao="conferiu e REJEITOU o novo (vigente bate com a fonte)",
                       risco=Risk.LOW.value)
        else:
            # Nenhum dos dois bate: a fonte de verdade vence os dois.
            truth_src = Source("Fonte de verdade", SourceKind.SYSTEM)
            self._supersede(existing, truth, truth_src,
                            Justification(why="valor real da fonte de verdade",
                                          verifiable=True), domain, 1.0,
                            "ambos divergiam da fonte de verdade")
            log.update(acao=f"conferiu: nenhum batia; gravou o real ({truth})",
                       risco=Risk.LOW.value)
        return log

    # ---- mutações internas (nunca deletam) -------------------------------- #

    def _insert(self, key, value, source, justification, domain, conf):
        b = Belief(key, value, source, justification, domain, conf)
        self.store.setdefault(key, []).append(b)
        return b

    def _supersede(self, old: Belief, value, source, justification, domain,
                   conf, reason):
        old.status = Status.SUPERSEDED        # rebaixa
        old.invalid_at = now()                # bi-temporal: até quando valeu
        new = self._insert(key=old.key, value=value, source=source,
                           justification=justification, domain=domain, conf=conf)
        new.supersedes = old.id
        new.reason_for_change = reason        # o PORQUÊ vira campo de 1ª classe
        return new


# --------------------------------------------------------------------------- #
# Utilitários                                                                 #
# --------------------------------------------------------------------------- #

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _as_number(s: str) -> Optional[float]:
    m = re.search(r"[\d.]+", str(s).replace(".", "").replace(",", "."))
    if not m:
        return None
    try:
        return float(re.sub(r"[^\d.]", "", str(s).replace(".", "").replace(",", ".")))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Demo: domínio comercial / cotações (a Mariana)                              #
# --------------------------------------------------------------------------- #

def _print(log):
    extra = {k: v for k, v in log.items()
             if k not in ("key", "value", "fonte", "relacao", "acao")}
    print(f"  → [{log['relacao']}] {log['acao']}")
    if extra:
        print(f"      {extra}")


def demo():
    # >>> SEAM: isto viria do Data Plane do cliente.
    authority = {
        "comercial": {"CFO Denilson", "Fonte de verdade"},
        "estoque": {"Líder de Estoque"},
    }
    sap = {  # >>> SEAM: SAP / fonte de verdade ao vivo
        "pedido:4471:total": "R$ 12.000",
    }
    eng = MemoryEngine(authority, sap)
    H, S = SourceKind.HUMAN, SourceKind.SYSTEM

    print("\nA) Reforço — mesmo fato repetido ganha saliência, sem conflito")
    for _ in range(3):
        log = eng.observe("cliente:ABC:prazo", "30 dias",
                          Source("Vendedor João", H),
                          Justification(why="histórico de pedidos"),
                          domain="comercial")
    _print(log)

    print("\nB) Correção autoritativa NÃO-verificável (médio) — supera com razão")
    log = eng.observe("cliente:ABC:prazo", "60 dias",
                      Source("CFO Denilson", H),
                      Justification(why="renegociação contratual fechada ontem"),
                      domain="comercial")
    _print(log)
    a = eng.active("cliente:ABC:prazo")
    print(f"      ATIVO agora: {a.value}  (confiança={a.confidence})")
    velha = [b for b in eng.history("cliente:ABC:prazo") if b.status.name == "SUPERSEDED"][0]
    print(f"      Antiga preservada: '{velha.value}' invalidada em "
          f"{velha.invalid_at.strftime('%H:%M:%S')} — ainda consultável")

    print("\nC) Magnitude absurda + autoridade menor (alto) — ESCALA, não muda")
    eng.observe("cliente:X:limite", "R$ 50.000", Source("CFO Denilson", H),
                Justification(why="análise de crédito"), domain="comercial")
    log = eng.observe("cliente:X:limite", "R$ 500.000",
                      Source("Estagiário", H), Justification(), domain="comercial")
    _print(log)
    print(f"      ATIVO continua: {eng.active('cliente:X:limite').value}")
    print(f"      Fila de aprovação: {eng.pending_approvals[-1]['motivo']}")

    print("\nD) Conflito VERIFICÁVEL — não discute, confere no SAP")
    eng.observe("pedido:4471:total", "R$ 10.000", Source("Vendedor João", H),
                Justification(verifiable=True, proof_pointer="SAP:VBAK/4471"),
                domain="comercial")
    log = eng.observe("pedido:4471:total", "R$ 12.000", Source("Mariana", S),
                      Justification(verifiable=True, proof_pointer="SAP:VBAK/4471"),
                      domain="comercial")
    _print(log)
    print(f"      ATIVO: {eng.active('pedido:4471:total').value}")

    print("\nE) 'pratinho' → 'pires' — guarda quem corrigiu e por quê")
    eng.observe("objeto:7:nome", "pratinho", Source("Colega Ana", H),
                Justification(why="foi o que me falaram"), domain="comercial")
    log = eng.observe("objeto:7:nome", "pires", Source("CFO Denilson", H),
                      Justification(why="nome técnico correto da peça",
                                    evidence="catálogo do fabricante"),
                      domain="comercial")
    _print(log)
    ativo = eng.active("objeto:7:nome")
    print(f"      ATIVO: '{ativo.value}'  | razão: {ativo.reason_for_change}")
    print("      Linha do tempo (nada apagado):")
    for b in eng.history("objeto:7:nome"):
        marca = "✓ ativa" if b.status == Status.ACTIVE else "↓ superada"
        print(f"        {marca}: '{b.value}' — fonte {b.source.name}")


if __name__ == "__main__":
    demo()
