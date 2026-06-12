"""Promoção para a memória no fim do turno (Fase 3c).

A ponte de ESCRITA entre o runtime e a memória. Quando um turno termina, este
passo olha o que aconteceu e promove à memória apenas CANDIDATOS CLAROS,
passando cada um pelo crivo do observe() da 3a (ceticismo, dois scores,
supersessão). Promover NÃO é atalho: tudo entra pelo motor governado.

REGRA CONSERVADORA (o ponto central desta fase): não jogamos a conversa
inteira no observe(). Conversa genérica NÃO vira belief — é a maior fonte de
ruído e o anti-pattern que evitamos. Promovemos só o que tem fonte
identificável e chave/valor claros. Hoje isso significa, na prática, FATOS
RETORNADOS POR TOOLS (fonte = a tool, verificável). Melhor promover pouco e
certo do que muito e ruidoso.

A regra fica ISOLADA em `extrair_candidatos` + um registry de extratores por
tool, fácil de evoluir. SEAM documentado: a reflexão em batch (um job agendado
que relê episódios e destila crenças mais ricas) é trabalho FUTURO — não desta
fase. Aqui a promoção é síncrona e mínima.
"""

import json
import logging
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from cortex.memory.engine import MemoryEngine
from cortex.memory.episodic import Episode
from cortex.memory.models import Justification, Source
from cortex.runtime.messages import Message, Role

logger = logging.getLogger("cortex.runtime")

# Domínio padrão dos candidatos. Placeholder até a persona carregar um domínio
# próprio (Fase 6 / Data Plane). Mantido num único ponto para trocar fácil.
DOMINIO_PADRAO = "comercial"


class PromotionCandidate(BaseModel):
    """Um candidato CLARO a virar memória — já com chave/valor/fonte/lastro.

    É o que `extrair_candidatos` produz e o que vai, um a um, para o observe().
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    value: str
    source: Source
    justification: Justification
    domain: str = DOMINIO_PADRAO


# Extrator por tool: do resultado (dict) da tool tira zero ou mais candidatos.
ExtratorTool = Callable[[dict], list[PromotionCandidate]]


# Registry de extratores. Tools sem extrator simplesmente não promovem nada —
# conservador por construção. Cresce aqui conforme novas tools mereçam memória.
#
# CORREÇÃO DOUTRINÁRIA (Fase 5b): `consultar_preco` foi REMOVIDO daqui. A Fase
# 3c o usou como demo de promoção ("a Mariana lembra o preço entre sessões"),
# mas isso CONTRADIZ o Plano 4 — preço é dado VIVO do system of record e nunca
# vira crença: uma cópia apodrece quando o valor muda na fonte. O que persiste
# de uma consulta ao SOR é o RASTRO dela (evento `consulta_sor` no audit), não o
# valor congelado. Tools `system_of_record` ficam, por princípio, fora daqui.
EXTRATORES_POR_TOOL: dict[str, ExtratorTool] = {}


def extrair_candidatos(mensagens_do_turno: Sequence[Message]) -> list[PromotionCandidate]:
    """Varre as mensagens do turno e extrai apenas os candidatos claros.

    Hoje: resultados bem-sucedidos de tools com extrator registrado. Conversa
    (USER/ASSISTANT em texto) é ignorada de propósito — não vira belief.
    """
    candidatos: list[PromotionCandidate] = []
    for m in mensagens_do_turno:
        if m.role is not Role.TOOL or m.erro or m.nome_tool not in EXTRATORES_POR_TOOL:
            continue
        try:
            resultado = json.loads(m.content)
        except json.JSONDecodeError:
            continue
        if isinstance(resultado, dict):
            candidatos.extend(EXTRATORES_POR_TOOL[m.nome_tool](resultado))
    return candidatos


def promover(engine: MemoryEngine, mensagens_do_turno: Sequence[Message]) -> list[Episode]:
    """Promove os candidatos do turno pelo observe() do motor (crivo completo).

    Devolve os episódios gerados — útil para auditoria/teste. Cada candidato
    passa pelo ceticismo/supersessão; nada entra na memória por fora do motor.
    """
    episodios: list[Episode] = []
    for c in extrair_candidatos(mensagens_do_turno):
        episodios.append(
            engine.observe(c.key, c.value, c.source, c.justification, domain=c.domain)
        )
    if episodios:
        logger.info("promovidos %d candidato(s) à memória", len(episodios))
    return episodios
