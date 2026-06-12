"""Modelos tipados da camada de identidade (Fase 1).

Por que pydantic: os arquivos de formação (SOUL.md, USER.md, playbooks,
tools.yaml) são editados por humanos curadores, então erros de digitação e
campos faltando são esperados. Pydantic valida na fronteira (no parse) e
produz mensagens de erro claras de graça — exatamente o contrato desta fase.

Todos os modelos proíbem campos extras (`extra="forbid"`): um typo no nome de
um campo do YAML vira erro explícito em vez de ser ignorado em silêncio.

Convenção de nomes: classes em inglês (vocabulário da arquitetura), campos em
português (espelham 1:1 as chaves dos arquivos YAML que o curador escreve).
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cortex.risk import RiskLevel


class _ModeloBase(BaseModel):
    """Base comum: proíbe campos extras e normaliza espaços em strings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# SOUL — quem a persona É
# ---------------------------------------------------------------------------


class Behavior(_ModeloBase):
    """Comportamento sob risco — a parte ESTRUTURADA do SOUL.md.

    Por quê estruturado: a prosa do SOUL é absorvida pelo LLM como persona,
    mas comportamentos críticos ("conferir antes de enviar") precisam ser
    avaliáveis programaticamente pelas engines futuras (Fase 4). Prosa não dá
    garantia de execução; frontmatter dá.
    """

    id: str = Field(description="Identificador estável do comportamento")
    gatilho: str = Field(description="Situação em que o comportamento se aplica")
    acao: str = Field(description="O que a persona deve fazer quando o gatilho ocorre")


class Soul(_ModeloBase):
    """Identidade e caráter da ACP — o documento mais protegido da formação.

    Formato híbrido: o frontmatter YAML carrega os comportamentos sob risco
    (estruturados); a prosa carrega identidade, tom e valores (absorvidos
    pelo LLM como persona). Editado apenas por humano curador.
    """

    nome: str = Field(description="Nome da persona")
    papel: str = Field(description="Papel profissional que a persona exerce")
    comportamentos: list[Behavior] = Field(
        min_length=1, description="Comportamentos sob risco, estruturados"
    )
    prosa: str = Field(min_length=1, description="Identidade, tom e valores em prosa")


# ---------------------------------------------------------------------------
# TOOLS — ações atômicas declaradas uma única vez
# ---------------------------------------------------------------------------


class ToolParameter(_ModeloBase):
    """Parâmetro de uma tool — declaração apenas, sem execução nesta fase."""

    nome: str
    tipo: str = Field(description="Tipo do parâmetro (string, integer, ...)")
    descricao: str
    obrigatorio: bool = True


class ToolDeclaration(_ModeloBase):
    """Declaração de uma ação atômica reutilizável.

    Por que um catálogo central: a tool é declarada UMA vez e referenciada
    pelos playbooks por nome — assim a descrição e o risco base não se
    duplicam (e divergem) em cada playbook. Nesta fase a tool NÃO executa
    nada; é só o contrato.
    """

    nome: str = Field(description="Nome único da tool no catálogo")
    descricao: str
    parametros: list[ToolParameter] = Field(default_factory=list)
    risco_base: RiskLevel = Field(
        description="Risco intrínseco da ação; consumido pela Decision Engine (Fase 4)"
    )
    system_of_record: bool = Field(
        default=False,
        description=(
            "Dado VIVO de system of record (SAP/SQL): consulta-se ao vivo, nunca "
            "se memoriza o valor (Plano 4, Fase 5b). A consulta vira evento de audit."
        ),
    )


# ---------------------------------------------------------------------------
# PLAYBOOK — o procedimento canônico de uma operação
# ---------------------------------------------------------------------------


class PlaybookStep(_ModeloBase):
    """Um passo do procedimento, com ordem, dependências e tool opcional."""

    id: str = Field(description="Identificador estável do passo dentro do playbook")
    ordem: int = Field(ge=1, description="Posição canônica do passo na operação")
    descricao: str
    tool: str | None = Field(
        default=None, description="Nome da tool do catálogo usada neste passo, se houver"
    )
    depende_de: list[str] = Field(
        default_factory=list, description="Ids de passos que precisam concluir antes"
    )


class EscalationPoint(_ModeloBase):
    """Ponto de escalonamento: condição que tira a operação do caminho feliz."""

    quando: str = Field(description="Condição que dispara o escalonamento")
    para: str = Field(description="Para quem escalar (papel ou pessoa do USER.md)")


class Playbook(_ModeloBase):
    """Manual auto-contido de UMA operação — 'a regra da casa'.

    Formato híbrido: o frontmatter estrutura passos, dependências, tools e
    pontos de escalonamento (o que as engines vão orquestrar na Fase 2+);
    a prosa é o manual em linguagem natural que o LLM lê.
    """

    operacao: str = Field(description="Nome único da operação (ex.: emitir_cotacao)")
    descricao: str
    passos: list[PlaybookStep] = Field(min_length=1)
    escalonamento: list[EscalationPoint] = Field(default_factory=list)
    prosa: str = Field(min_length=1, description="O manual da operação em linguagem natural")

    @model_validator(mode="after")
    def _validar_passos(self) -> Self:
        """Garante integridade interna: ids únicos e dependências existentes.

        Validamos aqui (e não no parser) para que QUALQUER caminho que
        construa um Playbook — parser, testes, código futuro — receba as
        mesmas garantias.
        """
        ids = [p.id for p in self.passos]
        duplicados = {i for i in ids if ids.count(i) > 1}
        if duplicados:
            raise ValueError(f"ids de passos duplicados: {sorted(duplicados)}")
        conjunto = set(ids)
        for passo in self.passos:
            faltantes = [d for d in passo.depende_de if d not in conjunto]
            if faltantes:
                raise ValueError(
                    f"passo '{passo.id}' depende de passos inexistentes: {faltantes}"
                )
        return self

    @property
    def tools_referenciadas(self) -> set[str]:
        """Nomes das tools que este playbook usa — base da checagem referencial."""
        return {p.tool for p in self.passos if p.tool is not None}


# ---------------------------------------------------------------------------
# USER — sob qual autoridade e com quem a persona trabalha
# ---------------------------------------------------------------------------


class Manager(_ModeloBase):
    """O gestor/chefe humano da persona."""

    nome: str
    cargo: str


class AuthorityBlock(_ModeloBase):
    """Bloco AUTORIDADE: quem MANDA na persona.

    É a base do authority map: define quem aprova, até onde vai a autonomia
    da persona (teto) e o que obrigatoriamente sobe para o gestor.
    """

    gestor: Manager = Field(description="Quem aprova — o responsável humano pela persona")
    teto_autoridade: str = Field(
        description="Limite do que a persona pode decidir sozinha, sem aprovação"
    )
    escalar: list[str] = Field(
        min_length=1, description="Situações que obrigatoriamente sobem para o gestor"
    )


class Colleague(_ModeloBase):
    """Um colega do organograma ao redor — com quem a persona TRABALHA.

    Distinção essencial mantida pelo modelo: colega não aprova nada (isso é
    do bloco autoridade); colega recebe escalonamentos de assuntos da sua
    especialidade.
    """

    nome: str
    papel: str = Field(description="Papel do colega na organização")
    escalar: list[str] = Field(
        min_length=1, description="Assuntos que a persona escala para este colega"
    )


class User(_ModeloBase):
    """USER.md tipado: autoridade (quem manda) + relacionamento (com quem trabalha)."""

    autoridade: AuthorityBlock
    relacionamento: list[Colleague] = Field(min_length=1)
    prosa: str = Field(min_length=1, description="Contexto do relacionamento em prosa")


# ---------------------------------------------------------------------------
# PERSONA — o agregado: o profissional formado, ainda não alocado
# ---------------------------------------------------------------------------


class Persona(_ModeloBase):
    """A formação completa carregada e validada.

    Quem constrói este objeto garante a integridade referencial (todo
    playbook referencia apenas tools existentes no catálogo) — ver
    `parser.carregar_persona`. A persona sabe QUEM é e COMO trabalha;
    memória e execução vêm nas fases seguintes.
    """

    soul: Soul
    user: User
    tools: dict[str, ToolDeclaration] = Field(
        description="Catálogo de tools indexado por nome"
    )
    playbooks: dict[str, Playbook] = Field(
        description="Playbooks indexados pelo nome da operação"
    )
