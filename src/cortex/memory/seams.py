"""SEAMs nomeados da memória (Fase 3a — Ajuste 5).

Cada borda externa do protótipo (dicts soltos) vira uma interface nomeada e
documentada, para as fases seguintes plugarem a infra real sem o motor mudar:

  - AuthorityMap   → vira config do Data Plane por cliente (Fase 6);
  - SourceOfTruth  → vira consulta SAP/SQL ao vivo (Fase 5).

Os defaults desta fase são baseados em dict, em memória — equivalentes ao
protótipo, mas atrás das interfaces certas.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping

from cortex.memory.models import ModeloMemoria


class AuthorityMap(ABC):
    """Quem é fonte autoritativa em cada domínio.

    Hoje um mapeamento estático; na Fase 6 vira configuração do Data Plane
    (por cliente, versionada, possivelmente dinâmica). O motor só pergunta
    'esta fonte é autoritativa neste domínio?' — nunca sabe de onde vem a
    resposta.
    """

    @abstractmethod
    def is_authoritative(self, source_name: str, domain: str) -> bool:
        """True se `source_name` é fonte autoritativa em `domain`."""


class DictAuthorityMap(AuthorityMap):
    """Default em memória: domínio → conjunto de nomes autoritativos."""

    def __init__(self, mapa: Mapping[str, set[str]] | None = None) -> None:
        self._mapa = {d: set(nomes) for d, nomes in (mapa or {}).items()}

    def is_authoritative(self, source_name: str, domain: str) -> bool:
        return source_name in self._mapa.get(domain, set())


class TruthLookup(ModeloMemoria):
    """Resultado de uma consulta à fonte de verdade.

    `found` distingue 'consultei e não havia registro' de 'havia e vale X'.
    """

    found: bool
    value: str | None = None


class SourceOfTruth(ABC):
    """Fonte de verdade verificável (SAP/SQL/documento) — consulta ao vivo.

    Hoje um dict; na Fase 5 vira chamada ao vivo a um system of record.

    REGRA CRÍTICA (já decidida): ao consultar a fonte de verdade, o motor
    REGISTRA que a consulta aconteceu e qual decisão saiu dela (no episódio,
    para lineage) — mas NÃO memoriza o valor consultado como fato permanente.
    O valor de um system of record "apodrece": o que era verdade hoje muda
    amanhã. Por isso o que persiste é o PONTEIRO de verificabilidade (a crença
    fica `verifiable=True` e será reconferida no próximo conflito), não o
    valor congelado. Quem implementar a consulta SAP na Fase 5 deve honrar
    isto: devolver o valor para a decisão do momento, não para virar cache.
    """

    @abstractmethod
    def lookup(self, key: str) -> TruthLookup:
        """Consulta a fonte de verdade pela chave canônica do fato."""


class DictSourceOfTruth(SourceOfTruth):
    """Default em memória: simula o system of record com um dict."""

    def __init__(self, mapa: Mapping[str, str] | None = None) -> None:
        self._mapa = dict(mapa or {})

    def lookup(self, key: str) -> TruthLookup:
        if key in self._mapa:
            return TruthLookup(found=True, value=self._mapa[key])
        return TruthLookup(found=False)
