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
    # SEAM — Fase 3 (memória persistente) conecta AQUI.
    #
    # Quando a camada de memória existir (episódica/entidade/semântica via
    # Graphiti), a "promoção para memória" será um método desta classe — algo
    # como `promover_para_memoria()` — chamado ao fim do turno/sessão para
    # destilar o histórico efêmero em registros persistentes. O loop não
    # muda: ele continua só anexando ao `historico`; quem decide o que vira
    # memória é a camada da Fase 3. NÃO implementado de propósito.
    # ------------------------------------------------------------------
