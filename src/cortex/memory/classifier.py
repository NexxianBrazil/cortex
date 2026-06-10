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
    """Classificação por LLM (Fase 3c) — usa a camada de LLMProvider da Fase 2.

    Pede ao modelo a leitura semântica da relação entre a crença vigente e a
    afirmação nova — algo que a igualdade literal da heurística não capta
    (ex.: '30 dias' vs 'um mês' REFORÇA; '30 dias' vs 'trimestral' CONTRADIZ).
    Continua sendo opção de config: o default e o CI usam a heurística.

    A decisão é BINÁRIA (reforça/contradiz) de propósito: `classify` só roda
    quando JÁ existe crença para a MESMA chave de fato — 'independente' é
    semanticamente impossível aqui (são afirmações sobre o mesmo assunto). Se
    devolvêssemos INDEPENDENT, o observe() inseriria uma SEGUNDA crença ativa
    para a mesma chave, sem supersessão, sem linhagem e SEM passar pelo cético
    — a mutação silenciosa que o moat proíbe.

    Curto-circuito: sem crença vigente é sempre INDEPENDENTE — não gasta uma
    chamada de LLM para o óbvio (e aí 'independente' é correto).
    """

    _SYSTEM = (
        "Você compara uma crença existente com uma afirmação nova sobre o MESMO "
        "assunto (a mesma chave de fato). Responda com UMA palavra, sem "
        "pontuação: 'reforça' (mesmo sentido, ainda que com outras palavras) ou "
        "'contradiz' (sentido conflitante)."
    )

    def __init__(self, provider: "LLMProvider") -> None:
        self._provider = provider

    def classify(self, existing: Belief | None, value: str) -> Relationship:
        if existing is None:
            return Relationship.INDEPENDENT
        # Import local: evita acoplar o módulo de memória ao runtime no import.
        from cortex.runtime.messages import Message, Role

        pergunta = Message(
            role=Role.USER,
            content=(
                f"Crença existente: '{existing.value}'.\n"
                f"Afirmação nova: '{value}'.\n"
                "A afirmação nova reforça ou contradiz a crença existente?"
            ),
        )
        resposta = self._provider.gerar(self._SYSTEM, [pergunta], [])
        return self._interpretar(resposta.texto)

    @staticmethod
    def _interpretar(texto: str | None) -> Relationship:
        """Mapeia a resposta do LLM para o enum, binário e conservador.

        Só REFORÇA quando o modelo claramente diz 'reforça'. QUALQUER outra
        coisa (contradiz, vazio, ambíguo) → CONTRADICTS: conservador no incerto
        LIGA o ceticismo. O pior caso é deliberar/escalar à toa — nunca escrever
        por fora do cético (uma 2ª crença ativa sem supersessão).
        """
        t = normalizar(texto or "")
        if "refor" in t:
            return Relationship.REINFORCES
        return Relationship.CONTRADICTS
