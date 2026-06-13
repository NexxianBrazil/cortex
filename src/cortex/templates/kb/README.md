# Knowledge Base de $nome

1. Crie um `.md` por política/regra, com frontmatter curado: `titulo`,
   `autoridade`, `dominio`, `vigente_desde` (e `vigente_ate`/`substituido_por`
   ao revogar — o revogado nunca some, mas nunca se passa por vigente).
2. O corpo é o texto da política; não resuma — a KB é a verdade DECLARADA.
3. Indexe com `cortex kb indexar --deploy <este diretório>` (ato do curador).
4. Teste com `cortex kb buscar "..." --deploy <este diretório>`.
5. A persona consulta via tool `consultar_kb` — a KB prevalece sobre a memória.
