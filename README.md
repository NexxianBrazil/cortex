# Cortex

**Cortex** é uma plataforma de "profissional digital" — ACP (*Artificial Cognitive Persona*) — que roda **on-premise** dentro de empresas.

> **Status:** Fase 0 — fundação do repositório. Apenas estrutura, configuração e templates; nenhuma lógica de negócio implementada ainda.

## Requisitos

- Python 3.11+

## Como rodar

```bash
# criar e ativar o ambiente virtual
python -m venv .venv
source .venv/bin/activate

# instalar o projeto em modo editável + ferramentas de dev
pip install -e ".[dev]"

# lint
ruff check .

# testes
pytest
```

## Estrutura do repositório

```
cortex/
├── src/cortex/          # pacote Python principal
│   ├── identity/        # Fase 1: camada de identidade (SOUL/AGENTS/USER)
│   ├── runtime/         # Fase 2: loop do agente, runtime state
│   ├── memory/          # Fase 3: memória episódica/entidade/semântica (Graphiti)
│   ├── governance/      # Fase 4: decision, ceticismo, justificação, learning queue
│   ├── knowledge/       # Fase 5: RAG sobre KB + systems of record (SAP/SQL)
│   └── controlplane/    # Fase 6: control/data plane, mTLS
├── personas/            # arquivos .md de FORMAÇÃO da persona (templates)
│   ├── SOUL.md          # identidade, valores e caráter da persona
│   ├── AGENTS.md        # índice das operações (cada operação vira um playbook)
│   ├── USER.md          # autoridade e relacionamento com o usuário/empresa
│   └── playbooks/       # manuais por operação (preenchidos na Fase 1)
├── tests/               # testes (pytest)
└── .github/workflows/   # CI: ruff + pytest em cada push/PR
```

## Mapa de fases

| Fase | Diretório                  | Escopo                                                |
|------|----------------------------|-------------------------------------------------------|
| 0    | (raiz)                     | Fundação do repositório — estrutura, config, CI       |
| 1    | `src/cortex/identity/`     | Camada de identidade (SOUL/AGENTS/USER) + playbooks   |
| 2    | `src/cortex/runtime/`      | Loop do agente, runtime state                         |
| 3    | `src/cortex/memory/`       | Memória episódica/entidade/semântica (Graphiti)       |
| 4    | `src/cortex/governance/`   | Decision, ceticismo, justificação, learning queue     |
| 5    | `src/cortex/knowledge/`    | RAG sobre KB + systems of record (SAP/SQL)            |
| 6    | `src/cortex/controlplane/` | Control/data plane, mTLS                              |

## Licença

Proprietary — Nexxian. Veja [LICENSE](LICENSE).
