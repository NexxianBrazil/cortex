"""Policy de risco de EXEMPLO/SEED para as tools mockadas (Fase 4a).

NÃO é política curada de produção — é seed de desenvolvimento que DEMONSTRA o
risk-by-scope: a mesma tool sai com risco diferente conforme os argumentos.
Substitua antes de qualquer deploy.

O risco BASE de cada tool vem da própria ToolDeclaration (Fase 1, tools.yaml)
— fonte única do base. Aqui declaramos apenas os ESCALADORES (os predicados
que elevam o risco por escopo), como dados. Trocar/expandir regras é editar
este dado, não o engine.
"""

from collections.abc import Mapping

from cortex.governance.policy import (
    Condition,
    Operador,
    RiskEscalator,
    RiskPolicy,
    ToolRiskPolicy,
)
from cortex.identity.models import ToolDeclaration
from cortex.risk import RiskLevel

# Domínio considerado interno: e-mail que não termina nele "sai da empresa".
DOMINIO_INTERNO = "@nexxian.com"
# Teto de valor de cotação acima do qual a operação vira crítica.
TETO_COTACAO = 50000

# Escaladores por tool (DADOS). Tool sem entrada aqui = só risco base.
ESCALADORES_SEED: dict[str, list[RiskEscalator]] = {
    "enviar_email": [
        # E-mail externo: sai da empresa, é visível e irreversível → HIGH.
        RiskEscalator(
            condicoes=[
                Condition(param="destinatario", op=Operador.NOT_ENDSWITH, value=DOMINIO_INTERNO)
            ],
            eleva_para=RiskLevel.HIGH,
            motivo="destinatário externo",
        ),
        # Externo COM anexo (possível dado de cliente saindo) → CRITICAL.
        RiskEscalator(
            condicoes=[
                Condition(param="destinatario", op=Operador.NOT_ENDSWITH, value=DOMINIO_INTERNO),
                Condition(param="anexos", op=Operador.TRUTHY),
            ],
            eleva_para=RiskLevel.CRITICAL,
            motivo="anexo (possível dado de cliente) para destinatário externo",
        ),
    ],
    "emitir_cotacao": [
        # Valor acima do teto: fora da alçada → CRITICAL.
        RiskEscalator(
            condicoes=[Condition(param="valor_total", op=Operador.GT, value=TETO_COTACAO)],
            eleva_para=RiskLevel.CRITICAL,
            motivo=f"valor acima do teto de R$ {TETO_COTACAO}",
        ),
    ],
    # consultar_preco: somente leitura — sem escaladores, sempre o base (LOW).
}


def construir_policy_exemplo(tools: Mapping[str, ToolDeclaration]) -> RiskPolicy:
    """Monta a RiskPolicy do catálogo: base de cada ToolDeclaration + escaladores seed."""
    return RiskPolicy(
        tools={
            nome: ToolRiskPolicy(
                risco_base=decl.risco_base,
                escaladores=ESCALADORES_SEED.get(nome, []),
            )
            for nome, decl in tools.items()
        }
    )
