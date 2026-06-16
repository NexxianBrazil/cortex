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

## Ligando o WhatsApp (Evolution API)

O Cortex não embute o protocolo do WhatsApp — uma **Evolution API** (Baileys,
on-prem) faz a plumbing e fala com a porta HTTP do Cortex.

1. **Suba a Evolution API** (Docker, na mesma rede do Cortex) e **crie uma
   instância**; pareie o número lendo o **QR Code**.
2. **Aponte o webhook** da instância para
   `http://<host-do-cortex>:8420/v1/webhook/evolution` (evento
   `messages.upsert`), enviando o header `apikey` com o `server_token` do
   `cortex.toml`.
3. **Configure o `cortex.toml`** deste deploy:
   ```toml
   canal_saida = "evolution"
   evolution_base_url = "http://localhost:8080"
   evolution_instancia = "<nome-da-instancia>"
   ```
   A `evolution_api_key` vem do ambiente/.env (`CORTEX_EVOLUTION_API_KEY`), nunca
   versionada.
4. **Mapeie o número** no `canais.yaml` (telefone com DDI, só dígitos → pessoa
   do USER.md). O gestor mapeado recebe as notificações da fila.
5. **Teste**: `cortex whatsapp testar --deploy . --para 5511999990000 --texto "oi"`.

> ⚠ **Aviso:** número de WhatsApp não-oficial (Baileys) **pode ser banido** —
> use apenas em TESTE. Em produção, o cliente usa a **Cloud API oficial da
> Meta**, que entra pela MESMA interface de canal de saída (sem tocar o Cortex).

## Segurança

- `cortex.toml` contém o `server_token` deste deploy — **não versione** este
  diretório num repositório público.
- O token autentica o transporte; a **identidade** do remetente segue o canal
  autenticado (`canais.yaml`), nunca o que a mensagem alega.
- O servidor sobe em loopback; exposição pública/TLS é tarefa de deploy (um
  proxy reverso na frente), fora do escopo do Cortex.
