# AGENTS — Operações da Persona

<!--
  EXEMPLO / SEED — índice fictício para desenvolvimento e testes.
  NÃO é conteúdo curado de produção. Substitua antes de qualquer deploy.
-->

Este arquivo é apenas o **índice** das operações da persona. Cada operação
vira um arquivo próprio em [`playbooks/`](playbooks/) — um manual
auto-contido, em formato híbrido (frontmatter estruturado + prosa), com os
passos, as tools referenciadas e os pontos de escalonamento daquela operação.

As tools usadas pelos playbooks são declaradas **uma única vez** no catálogo
[`tools.yaml`](tools.yaml) e referenciadas pelo nome — nunca redeclaradas
dentro de um playbook.

## Catálogo de operações

| Operação         | Playbook                                                 | Descrição curta                                  | Status   |
|------------------|----------------------------------------------------------|--------------------------------------------------|----------|
| `emitir_cotacao` | [playbooks/emitir_cotacao.md](playbooks/emitir_cotacao.md) | Emite cotação formal a partir de pedido de orçamento | exemplo |

## Regras gerais de execução

Regras que valem para **todas** as operações (o que é específico de uma
operação fica no playbook dela):

- Nenhum passo com tool fora do catálogo `tools.yaml`.
- Situação sem playbook aplicável não se improvisa: escala-se
  (comportamento `escalar_quando_incerto` do SOUL).
- Todo escalonamento segue o mapa do `USER.md` — gestor aprova exceções;
  colegas respondem pelos assuntos da sua especialidade.
