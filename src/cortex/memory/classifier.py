"""Classificador de relação — interface trocável (Fase 3a — Ajuste 3).

Mesmo padrão da camada de LLM provider da Fase 2: uma ABC com uma
implementação HEURÍSTICA determinística como default (roda no CI) e um SEAM
claro para a futura implementação por LLM. A escolha vem da config.

Classificar a relação (independente/reforça/contradiz) é o pivô do motor: é
o que decide se a camada de ceticismo liga. Por isso é trocável — a
heurística resolve o caso comum, mas a leitura semântica fina ('isto ESTENDE
a crença' vs 'isto a CONTRADIZ') é exatamente onde um LLM agrega valor.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from cortex.memory.models import Relationship
from cortex.memory.semantic import Belief
from cortex.memory.text import normalizar

if TYPE_CHECKING:
    # Import só para tipagem: o LLMClassifier (SEAM) usaria o LLMProvider da
    # Fase 2, mas não acoplamos o módulo de memória ao runtime em tempo de
    # execução enquanto isso não existe.
    from cortex.runtime.providers.base import LLMProvider


class Classifier(ABC):
    """Contrato: dada a crença vigente e um valor novo, classifica a relação."""

    @abstractmethod
    def classify(self, existing: Belief | None, value: str) -> Relationship:
        """Devolve INDEPENDENT, REINFORCES ou CONTRADICTS."""


class HeuristicClassifier(Classifier):
    """Heurística determinística portada do protótipo — o DEFAULT.

    Sem rede, sem LLM: assunto sem crença vigente é independente; valor igual
    (após normalização) reforça; valor diferente contradiz. Suficiente para o
    caso comum e 100% reprodutível no CI.
    """

    def classify(self, existing: Belief | None, value: str) -> Relationship:
        if existing is None:
            return Relationship.INDEPENDENT
        if normalizar(existing.value) == normalizar(value):
            return Relationship.REINFORCES
        return Relationship.CONTRADICTS


class LLMClassifier(Classifier):
    """SEAM — classificação por LLM (NÃO implementada nesta fase).

    Quando ligada, recebe um LLMProvider da Fase 2 e pede ao modelo a leitura
    semântica da relação (inspiração HiMem: independente/estende/contradiz),
    em vez da igualdade literal da heurística. Deixada aqui, nomeada e
    documentada, para a fase futura plugar sem redesenhar nada: o motor já
    fala só com a interface `Classifier`.
    """

    def __init__(self, provider: "LLMProvider") -> None:
        self._provider = provider

    def classify(self, existing: Belief | None, value: str) -> Relationship:
        raise NotImplementedError(
            "LLMClassifier é um SEAM da Fase 3a — a classificação por LLM será "
            "implementada quando a memória for conectada ao runtime (Fase 3c)."
        )
