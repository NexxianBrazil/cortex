# Cortex "$nome" — $papel

Deploy auto-contido de uma ACP (Artificial Cognitive Persona) da Nexxian,
gerado por `cortex novo`. Tudo deste Cortex vive neste diretório: formação,
KB, memória e auditoria. Gestor: **$gestor**.

## Colocar para operar

1. **Curar a formação** — revise `personas/SOUL.md` (seções `<!-- preencher -->`),
   `personas/USER.md` (teto, escalonamentos, colegas) e os `playbooks/`.
2. **Curar a KB** — veja `kb/README.md`; depois `cortex kb indexar --deploy .`
3. **Mapear os canais** — edite `canais.yaml` com os contatos do gestor e dos
   colegas (número de WhatsApp / e-mail → pessoa do USER.md). Nome órfão impede
   o servidor de subir, de propósito.
4. **Subir** — `cortex servir --deploy .` (sobe em `127.0.0.1:8420`).
5. **Plugar o bridge** — um bridge (n8n/Evolution/Cloud API) chama
   `POST /v1/mensagens` com o header `X-Cortex-Token` (veja `cortex.toml`).

## Segurança

- `cortex.toml` contém o `server_token` deste deploy — **não versione** este
  diretório num repositório público.
- O token autentica o transporte; a **identidade** do remetente segue o canal
  autenticado (`canais.yaml`), nunca o que a mensagem alega.
- O servidor sobe em loopback; exposição pública/TLS é tarefa de deploy (um
  proxy reverso na frente), fora do escopo do Cortex.
