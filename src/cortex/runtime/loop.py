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

from cortex.identity.models import Persona
from cortex.runtime.messages import Message, Role
from cortex.runtime.providers.base import LLMProvider
from cortex.runtime.session import Session
from cortex.runtime.tools import ToolError, ToolRegistry

logger = logging.getLogger("cortex.runtime")


class LoopLimiteExcedidoError(RuntimeError):
    """O turno estourou o teto de iterações sem o LLM concluir em texto."""


def montar_system_prompt(persona: Persona) -> str:
    """Monta o contexto da persona: SOUL + autoridade/relacionamento + playbooks.

    A prosa entra como persona para o LLM absorver; os comportamentos e
    escalonamentos entram explicitados porque são as regras que o modelo
    precisa seguir À RISCA (na Fase 4 eles também serão verificados por
    engine, não só por prompt).
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

    return "\n\n".join(partes)


class AgentLoop:
    """Executa turnos de conversa: LLM decide, runtime executa, LLM conclui."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        max_iteracoes: int = 10,
    ) -> None:
        if max_iteracoes < 1:
            raise ValueError("max_iteracoes deve ser >= 1")
        self._provider = provider
        self._registry = registry
        self._max_iteracoes = max_iteracoes

    def executar_turno(self, session: Session, entrada_usuario: str) -> str:
        """Roda um turno completo e devolve a resposta final em texto.

        Levanta LoopLimiteExcedidoError se o LLM não concluir dentro do teto
        — preferimos falhar alto a queimar tokens em um loop sem fim.
        """
        session.historico.append(Message(role=Role.USER, content=entrada_usuario))
        system = montar_system_prompt(session.persona)
        tools = list(session.persona.tools.values())

        for iteracao in range(1, self._max_iteracoes + 1):
            resposta = self._provider.gerar(system, session.historico, tools)

            if not resposta.pediu_tool:
                texto = resposta.texto or ""
                session.historico.append(Message(role=Role.ASSISTANT, content=texto))
                logger.info("turno concluído na iteração %d", iteracao)
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
