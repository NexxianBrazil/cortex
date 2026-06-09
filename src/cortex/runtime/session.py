"""Runtime state efêmero da Fase 2 — a Session.

EFÊMERA POR DESIGN: tudo aqui vive em memória do processo e MORRE ao fim da
sessão. Nada é escrito em disco ou banco. Nesta fase o profissional digital
trabalha, mas não lembra — a memória persistente é a Fase 3.
"""

from cortex.identity.models import Persona
from cortex.runtime.messages import Message


class Session:
    """Estado de uma sessão de conversa com a persona.

    Guarda a persona carregada e o histórico de mensagens do turno em
    andamento (no formato interno). Criar uma Session nova = começar do zero,
    sem nenhum resquício da anterior.
    """

    def __init__(self, persona: Persona) -> None:
        self.persona = persona
        self.historico: list[Message] = []

    # ------------------------------------------------------------------
    # Memória (Fase 3c): a Session SEGUE efêmera — este `historico` morre com
    # a sessão. O que sobrevive é o que o loop PROMOVE ao MemoryEngine no fim
    # de cada turno (ver AgentLoop.executar_turno + runtime/promotion.py).
    # A promoção mora no loop (não aqui) de propósito: a Session é só o estado
    # da conversa; quem orquestra leitura/escrita da memória é o loop. Assim a
    # Session não precisa conhecer o motor de memória.
    # ------------------------------------------------------------------
