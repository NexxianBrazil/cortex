"""Sessões por contato, com TTL de inatividade (Fase 7a).

Um mapa (canal, canal_id) → Session, criada sob demanda. A Session segue
EFÊMERA por design (Runtime State); o TTL apenas a recicla após inatividade — o
que LEMBRA entre conversas é a memória persistente, não a Session. O relógio é
injetável para os testes exercitarem o vencimento sem dormir.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from cortex.identity.models import Persona
from cortex.runtime import Session
from cortex.runtime.identidade import Identidade

ChaveContato = tuple[str, str]


class GerenciadorSessoes:
    """Guarda uma Session viva por contato e a recicla quando o TTL vence."""

    def __init__(
        self,
        persona: Persona,
        ttl_minutos: int,
        *,
        agora: Callable[[], datetime] | None = None,
    ) -> None:
        self._persona = persona
        self._ttl = timedelta(minutes=ttl_minutos)
        self._agora = agora or (lambda: datetime.now(UTC))
        self._sessoes: dict[ChaveContato, tuple[Session, datetime]] = {}

    def obter(self, canal: str, canal_id: str, identidade: Identidade) -> Session:
        """A Session do contato — a mesma se ativa; uma nova se o TTL venceu.

        A identidade é re-resolvida a cada turno (o canal pode ser remapeado),
        então ela é sempre atualizada na Session devolvida.
        """
        chave = (canal, canal_id)
        agora = self._agora()
        existente = self._sessoes.get(chave)
        if existente is not None and agora - existente[1] <= self._ttl:
            sessao = existente[0]
            sessao.identidade = identidade
            self._sessoes[chave] = (sessao, agora)
            return sessao

        sessao = Session(self._persona, identidade=identidade)
        self._sessoes[chave] = (sessao, agora)
        return sessao

    def descartar(self, canal: str, canal_id: str) -> bool:
        """Joga fora a Session do contato — o próximo turno começa do zero.

        É a efemeridade sob comando ("nova conversa" no painel): o histórico
        do turno morre; o que a persona aprendeu já foi promovido à memória.
        """
        return self._sessoes.pop((canal, canal_id), None) is not None

    def espiar(self, canal: str, canal_id: str) -> Session | None:
        """A Session viva do contato, se houver — sem criar nem renovar o TTL."""
        existente = self._sessoes.get((canal, canal_id))
        return existente[0] if existente else None

    @property
    def ativas(self) -> int:
        return len(self._sessoes)
