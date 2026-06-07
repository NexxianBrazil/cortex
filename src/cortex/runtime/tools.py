"""Registry de tools: mapeia declaração (Fase 1) → implementação executável.

Separação deliberada: a ToolDeclaration diz O QUE a tool é (contrato,
risco); o registry diz COMO executá-la. Nesta fase as implementações são
mockadas, mas o contrato do registry já é o definitivo — a Fase 5 vai
registrar implementações reais sem tocar no loop.

Erros aqui são TRATÁVEIS por design: tool inexistente ou argumentos errados
viram exceções tipadas que o loop captura e devolve ao LLM como resultado de
erro — o processo nunca cai por um pedido ruim do modelo.
"""

from collections.abc import Callable, Mapping

from cortex.identity.models import ToolDeclaration

# Implementação de tool: recebe os argumentos validados como kwargs e devolve
# um dict serializável (que o loop transforma em resultado para o LLM).
ToolFunc = Callable[..., dict]


class ToolError(Exception):
    """Base dos erros tratáveis de execução de tool."""


class ToolNaoEncontradaError(ToolError):
    """O LLM pediu uma tool que não existe no registry."""


class ArgumentosInvalidosError(ToolError):
    """Os argumentos do pedido não batem com a ToolDeclaration."""


class ToolRegistry:
    """Catálogo executável: nome da tool → função que a implementa.

    Construído a partir das declarações da persona — só aceita registrar
    implementação para tool DECLARADA, mantendo declaração e execução
    sempre em sincronia.
    """

    def __init__(self, declaracoes: Mapping[str, ToolDeclaration]) -> None:
        self._declaracoes = dict(declaracoes)
        self._implementacoes: dict[str, ToolFunc] = {}

    def registrar(self, nome: str, func: ToolFunc) -> None:
        """Associa uma implementação a uma tool declarada no catálogo."""
        if nome not in self._declaracoes:
            raise ValueError(
                f"tool '{nome}' não está declarada no catálogo — declare-a em "
                "tools.yaml antes de registrar uma implementação"
            )
        self._implementacoes[nome] = func

    def _validar_argumentos(self, nome: str, argumentos: dict) -> None:
        """Confere o pedido do LLM contra a declaração (presença, não tipos).

        Validação de tipos fica para quando as tools forem reais (Fase 5);
        aqui garantimos que obrigatórios existem e que não há argumento
        desconhecido — os dois erros mais comuns de um LLM.
        """
        declaracao = self._declaracoes[nome]
        conhecidos = {p.nome for p in declaracao.parametros}
        obrigatorios = {p.nome for p in declaracao.parametros if p.obrigatorio}

        faltando = sorted(obrigatorios - argumentos.keys())
        if faltando:
            raise ArgumentosInvalidosError(
                f"tool '{nome}': argumentos obrigatórios ausentes: {faltando}"
            )
        desconhecidos = sorted(argumentos.keys() - conhecidos)
        if desconhecidos:
            raise ArgumentosInvalidosError(
                f"tool '{nome}': argumentos desconhecidos: {desconhecidos} "
                f"(declarados: {sorted(conhecidos)})"
            )

    def executar(self, nome: str, argumentos: dict) -> dict:
        """Valida e executa a tool pedida pelo LLM, devolvendo o resultado."""
        if nome not in self._implementacoes:
            disponiveis = sorted(self._implementacoes)
            raise ToolNaoEncontradaError(
                f"tool '{nome}' não existe no registry — disponíveis: {disponiveis}"
            )
        self._validar_argumentos(nome, argumentos)
        return self._implementacoes[nome](**argumentos)
