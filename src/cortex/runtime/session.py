"""Runtime state efêmero da Fase 2 — a Session.

EFÊMERA POR DESIGN: tudo aqui vive em memória do processo e MORRE ao fim da
sessão. Nada é escrito em disco ou banco. Nesta fase o profissional digital
trabalha, mas não lembra — a memória persistente é a Fase 3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cortex.identity.models import Persona
from cortex.runtime.messages import Message

if TYPE_CHECKING:
    from cortex.runtime.identidade import Identidade


class Session:
    """Estado de uma sessão de conversa com a persona.

    Guarda a persona carregada e o histórico de mensagens do turno em
    andamento (no formato interno). Criar uma Session nova = começar do zero,
    sem nenhum resquício da anterior.

    `identidade` (Fase 7a) é quem fala nesta conversa, autenticado pelo CANAL
    (não pelo texto). Fica disponível para o loop demarcar conteúdo externo e
    auditar o turno — e, na 7b, será o Source dos fatos aprendidos da conversa.
    None = modo dev sem canal (a entrada não é demarcada).
    """

    def __init__(self, persona: Persona, identidade: Identidade | None = None) -> None:
        self.persona = persona
        self.identidade = identidade
        self.historico: list[Message] = []

    # ------------------------------------------------------------------
    # Memória (Fase 3c): a Session SEGUE efêmera — este `historico` morre com
    # a sessão. O que sobrevive é o que o loop PROMOVE ao MemoryEngine no fim
    # de cada turno (ver AgentLoop.executar_turno + runtime/promotion.py).
    # A promoção mora no loop (não aqui) de propósito: a Session é só o estado
    # da conversa; quem orquestra leitura/escrita da memória é o loop. Assim a
    # Session não precisa conhecer o motor de memória.
    # ------------------------------------------------------------------
