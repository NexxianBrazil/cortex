# AGENTS — Operações de $nome

Índice das operações desta persona. Cada operação é um manual auto-contido em
[`playbooks/`](playbooks/) (frontmatter estruturado + prosa), com passos, tools
referenciadas e pontos de escalonamento. As tools são declaradas uma única vez
no catálogo [`tools.yaml`](tools.yaml) e referenciadas pelo nome.

## Catálogo de operações

| Operação           | Playbook                                                       | Status  |
|--------------------|----------------------------------------------------------------|---------|
| `exemplo_operacao` | [playbooks/exemplo_operacao.md](playbooks/exemplo_operacao.md) | exemplo |

## Regras gerais

- Nenhum passo com tool fora do catálogo `tools.yaml`.
- Situação sem playbook aplicável não se improvisa: escala-se (`escalar_quando_incerto`).
- Todo escalonamento segue o `USER.md` — gestor aprova; colegas respondem pela especialidade.
