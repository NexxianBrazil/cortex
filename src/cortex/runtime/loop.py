"""O loop do agente — o coração da Fase 2.

Iterativo, explícito e auditável: cada volta (a) monta o contexto, (b) chama
o LLMProvider, (c) se o LLM pediu tool, executa via registry e devolve o
resultado ao histórico, voltando ao (b); (d) se o LLM respondeu em texto,
encerra o turno. Um teto de iterações garante que o loop NUNCA roda infinito
— guardrail de custo e segurança.

Cada volta é logada (tool, args, resultado) — é a semente da auditoria que a
governança da Fase 4 vai formalizar.
"""

import json
import logging
from collections.abc import Sequence

from cortex.governance.engine import DecisionEngine
from cortex.identity.models import Persona
from cortex.memory.engine import MemoryEngine
from cortex.memory.semantic import Belief
from cortex.runtime.messages import Message, Role
from cortex.runtime.promotion import promover
from cortex.runtime.providers.base import LLMProvider
from cortex.runtime.recall import formatar_beliefs, recuperar_beliefs
from cortex.runtime.session import Session
from cortex.runtime.tools import ToolError, ToolRegistry

logger = logging.getLogger("cortex.runtime")


class LoopLimiteExcedidoError(RuntimeError):
    """O turno estourou o teto de iterações sem o LLM concluir em texto."""


def montar_system_prompt(
    persona: Persona, beliefs_relevantes: Sequence[Belief] | None = None
) -> str:
    """Monta o contexto da persona: SOUL + autoridade/relacionamento + playbooks.

    A prosa entra como persona para o LLM absorver; os comportamentos e
    escalonamentos entram explicitados porque são as regras que o modelo
    precisa seguir À RISCA (na Fase 4 eles também serão verificados por
    engine, não só por prompt).

    `beliefs_relevantes` (Fase 3c) injeta, de forma enxuta, o que a memória já
    sabe sobre o que se conversa — para o Cortex "saber o que já sabe" antes de
    responder. Vazio/None mantém o comportamento da Fase 2.
    """
    soul = persona.soul
    user = persona.user
    partes: list[str] = []

    partes.append(f"# Você é {soul.nome} — {soul.papel}\n\n{soul.prosa}")

    comportamentos = "\n".join(
        f"- [{c.id}] Quando {c.gatilho}: {c.acao}." for c in soul.comportamentos
    )
    partes.append(f"## Comportamentos inegociáveis\n\n{comportamentos}")

    autoridade = user.autoridade
    escalonamentos_gestor = "\n".join(f"- {item}" for item in autoridade.escalar)
    partes.append(
        "## Autoridade\n\n"
        f"Seu gestor é {autoridade.gestor.nome} ({autoridade.gestor.cargo}).\n"
        f"Teto da sua autonomia: {autoridade.teto_autoridade}.\n"
        f"Escale SEMPRE para o gestor:\n{escalonamentos_gestor}"
    )

    colegas = "\n".join(
        f"- {c.nome} ({c.papel}): escale para essa pessoa {'; '.join(c.escalar)}."
        for c in user.relacionamento
    )
    partes.append(f"## Relacionamento\n\n{colegas}\n\n{user.prosa}")

    for playbook in persona.playbooks.values():
        escalonamento = "\n".join(
            f"- Quando {p.quando}: escalar para {p.para}." for p in playbook.escalonamento
        )
        partes.append(
            f"## Playbook: {playbook.operacao}\n\n{playbook.descricao}\n\n"
            f"{playbook.prosa}\n\n### Pontos de escalonamento\n{escalonamento}"
        )

    if beliefs_relevantes:
        partes.append(formatar_beliefs(beliefs_relevantes))

    return "\n\n".join(partes)


class AgentLoop:
    """Executa turnos de conversa: LLM decide, runtime executa, LLM conclui."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        max_iteracoes: int = 10,
        memory: MemoryEngine | None = None,
        recall_limite: int = 5,
        decision: DecisionEngine | None = None,
    ) -> None:
        if max_iteracoes < 1:
            raise ValueError("max_iteracoes deve ser >= 1")
        self._provider = provider
        self._registry = registry
        self._max_iteracoes = max_iteracoes
        # memory=None reproduz EXATAMENTE o loop da Fase 2 (sem recall nem
        # promoção). Com memória, o turno passa a ler e a escrever na 3a/3b.
        self._memory = memory
        self._recall_limite = recall_limite
        # decision=None mantém o loop sem governança. Com o DecisionEngine,
        # cada chamada de tool passa pelo crivo de risco antes de executar
        # (observe: só loga; enforce: barra MEDIUM+). Ver Fase 4a.
        self._decision = decision

    def executar_turno(self, session: Session, entrada_usuario: str) -> str:
        """Roda um turno completo e devolve a resposta final em texto.

        Com memória conectada: recupera beliefs relevantes ao montar o contexto
        (leitura) e, ao concluir o turno, promove os candidatos claros à
        memória persistente (escrita). Levanta LoopLimiteExcedidoError se o LLM
        não concluir dentro do teto — preferimos falhar alto a queimar tokens.
        """
        # Marca o início do turno para a promoção olhar só o que aconteceu agora.
        inicio_turno = len(session.historico)
        session.historico.append(Message(role=Role.USER, content=entrada_usuario))

        beliefs = (
            recuperar_beliefs(self._memory, entrada_usuario, self._recall_limite)
            if self._memory is not None
            else []
        )
        system = montar_system_prompt(session.persona, beliefs)
        tools = list(session.persona.tools.values())

        for iteracao in range(1, self._max_iteracoes + 1):
            resposta = self._provider.gerar(system, session.historico, tools)

            if not resposta.pediu_tool:
                texto = resposta.texto or ""
                session.historico.append(Message(role=Role.ASSISTANT, content=texto))
                logger.info("turno concluído na iteração %d", iteracao)
                if self._memory is not None:
                    # Promoção no FIM do turno (decisão 1): só candidatos claros,
                    # pelo crivo do observe(). Ver runtime/promotion.py.
                    promover(self._memory, session.historico[inicio_turno:])
                return texto

            # O LLM pediu tools: registra o pedido e executa cada uma.
            session.historico.append(
                Message(
                    role=Role.ASSISTANT,
                    content=resposta.texto or "",
                    tool_calls=resposta.tool_calls,
                )
            )
            for pedido in resposta.tool_calls:
                logger.info(
                    "iteração %d: tool=%s args=%s", iteracao, pedido.nome, pedido.argumentos
                )

                # Crivo de risco (Fase 4a): avalia ANTES de executar. Em
                # observe, decisao.executou é sempre True (só loga). Em
                # enforce, MEDIUM+ não executa e vira resultado tratável de
                # "precisa aprovação" — o loop não cai, o LLM se adapta.
                if self._decision is not None:
                    decisao = self._decision.avaliar(pedido.nome, pedido.argumentos)
                    if not decisao.executou:
                        conteudo = (
                            f"AÇÃO BLOQUEADA pela governança — precisa de aprovação "
                            f"(risco {decisao.risco.value}): {'; '.join(decisao.motivos)}. "
                            "A fila de aprovação será tratada na Fase 4c."
                        )
                        logger.warning(
                            "iteração %d: tool=%s BLOQUEADA (enforce, risco=%s)",
                            iteracao,
                            pedido.nome,
                            decisao.risco.value,
                        )
                        session.historico.append(
                            Message(
                                role=Role.TOOL,
                                content=conteudo,
                                tool_call_id=pedido.id,
                                nome_tool=pedido.nome,
                                erro=True,
                            )
                        )
                        continue

                try:
                    resultado = self._registry.executar(pedido.nome, pedido.argumentos)
                    conteudo = json.dumps(resultado, ensure_ascii=False)
                    erro = False
                    logger.info(
                        "iteração %d: tool=%s resultado=%s", iteracao, pedido.nome, conteudo
                    )
                except ToolError as exc:
                    # Erro TRATÁVEL: volta para o LLM como resultado de erro,
                    # para ele se corrigir — o processo nunca cai por isso.
                    conteudo = f"ERRO: {exc}"
                    erro = True
                    logger.warning(
                        "iteração %d: tool=%s falhou: %s", iteracao, pedido.nome, exc
                    )
                session.historico.append(
                    Message(
                        role=Role.TOOL,
                        content=conteudo,
                        tool_call_id=pedido.id,
                        nome_tool=pedido.nome,
                        erro=erro,
                    )
                )
            # Resultados anexados — volta ao passo (b): nova chamada ao LLM.

        raise LoopLimiteExcedidoError(
            f"turno excedeu o teto de {self._max_iteracoes} iterações sem resposta "
            "final em texto — encerrado por guardrail de custo/segurança"
        )
