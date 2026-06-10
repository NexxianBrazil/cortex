"""Modelos base da memória — os tijolos compartilhados (Fase 3a).

Migrados de dataclass para pydantic (Ajuste 2), com `extra="forbid"` como no
resto do projeto. Aqui ficam os enums e as duas peças que aparecem nos três
planos (episódica/entidade/semântica): de onde veio a informação (`Source`) e
o porquê que lhe dá lastro (`Justification`).
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


def agora() -> datetime:
    """Instante atual em UTC — ponto único de 'agora' para a bi-temporalidade."""
    return datetime.now(UTC)


class Contador:
    """Gerador de ids sequenciais chamável, seguro com persistência durável.

    Por que não um `itertools.count` solto: os ids viram PRIMARY KEY no backend
    durável (Kuzu, Fase 3b). Num processo novo, um contador que recomeça do 1
    COLIDE com os ids já hidratados do banco — a primeira escrita quebra o
    checkpoint. Por isso a hidratação chama `garantir_minimo()` para avançar o
    contador PARA ALÉM do maior id persistido.
    """

    def __init__(self, inicio: int = 1) -> None:
        self._proximo = inicio

    def __call__(self) -> int:
        """Devolve o próximo id e avança."""
        valor = self._proximo
        self._proximo += 1
        return valor

    def garantir_minimo(self, proximo: int) -> None:
        """Garante que o próximo id seja >= `proximo`. Só anda PARA FRENTE.

        Nunca reduz o contador — seguro mesmo com vários stores no mesmo
        processo (cada hidratação só pode empurrar o piso para cima).
        """
        if proximo > self._proximo:
            self._proximo = proximo


class ModeloMemoria(BaseModel):
    """Base comum: proíbe campos extras (typo vira erro, não silêncio)."""

    model_config = ConfigDict(extra="forbid")


class SourceKind(StrEnum):
    """Natureza da fonte — entra no cálculo de autoridade (ver motor)."""

    HUMAN = "humano"
    SYSTEM = "sistema"  # ex.: SAP, banco
    DOCUMENT = "documento"  # ex.: política em PDF
    TOOL = "ferramenta"
    AGENT = "inferência do agente"


class Relationship(StrEnum):
    """Relação de uma afirmação nova com a crença vigente.

    É a classificação que o `Classifier` produz — e o pivô do motor: só a
    CONTRADIÇÃO liga a camada de ceticismo.
    """

    INDEPENDENT = "independente"  # assunto novo, não toca nada
    REINFORCES = "reforça"  # mesmo valor de novo -> ganha saliência
    CONTRADICTS = "contradiz"  # valor diferente para a mesma chave


class Status(StrEnum):
    """Estado de uma crença. Nunca apagamos: rebaixamos para SUPERSEDED/REJECTED."""

    ACTIVE = "ativa"
    SUPERSEDED = "superada"  # invalidada por outra crença, NÃO apagada
    REJECTED = "rejeitada"  # contradita pela fonte de verdade


class Source(ModeloMemoria):
    """Quem afirmou: nome e natureza. Base da autoridade e da linhagem."""

    name: str
    kind: SourceKind


class Justification(ModeloMemoria):
    """O 'porquê' que dá lastro à crença — o que o agente cobra ao questionar.

    `verifiable` é o que decide, numa contradição, entre 'vai conferir na
    fonte de verdade' e 'vai escalar para um humano'.
    """

    why: str | None = None  # razão causal
    evidence: str | None = None  # prova / referência
    verifiable: bool = False  # dá para conferir contra fonte de verdade?
    proof_pointer: str | None = None  # ex.: "SAP:VBAK/4471", "politica.pdf#3"

    def quality(self) -> float:
        """Bônus de confiança por ter razão/prova/verificabilidade (0.0–0.3).

        Justificação melhor = crença mais defensável. Some-se à autoridade da
        fonte para compor a confiança total.
        """
        q = 0.0
        if self.why:
            q += 0.10
        if self.evidence:
            q += 0.10
        if self.verifiable:
            q += 0.10
        return q
