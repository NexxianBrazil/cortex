---
# =========================================================================
# EXEMPLO / SEED — playbook fictício para desenvolvimento e testes.
# NÃO é conteúdo curado de produção. Substitua antes de qualquer deploy.
# =========================================================================
# Playbook: o procedimento canônico ("a regra da casa") de UMA operação.
# Frontmatter: passos com ordem/dependências, tools do catálogo e pontos
# de escalonamento. Prosa: o manual em linguagem natural.
# =========================================================================
operacao: emitir_cotacao
descricao: >-
  Emitir uma cotação formal para um cliente a partir de um pedido de
  orçamento, da identificação dos itens até o envio do documento.
passos:
  - id: levantar_itens
    ordem: 1
    descricao: >-
      Identificar os produtos e quantidades solicitados pelo cliente,
      confirmando códigos no catálogo interno.
  - id: consultar_precos
    ordem: 2
    descricao: Consultar preço de tabela e disponibilidade de cada item.
    tool: consultar_preco
    depende_de: [levantar_itens]
  - id: montar_cotacao
    ordem: 3
    descricao: >-
      Gerar o documento de cotação com itens, preços e condições de
      pagamento da tabela padrão.
    tool: emitir_cotacao
    depende_de: [consultar_precos]
  - id: conferir_documento
    ordem: 4
    descricao: >-
      Revisar valores, condições, dados do cliente e destinatário antes de
      qualquer envio (comportamento conferir_antes_de_enviar do SOUL).
    depende_de: [montar_cotacao]
  - id: enviar_ao_cliente
    ordem: 5
    descricao: Enviar a cotação ao cliente por e-mail, com o documento anexado.
    tool: enviar_email
    depende_de: [conferir_documento]
escalonamento:
  - quando: cliente solicitar desconto acima de 5% ou condição de pagamento fora da tabela
    para: Carlos Menezes (gestor)
  - quando: valor total da cotação ultrapassar R$ 50.000
    para: Carlos Menezes (gestor)
  - quando: houver dúvida técnica sobre especificação ou compatibilidade de produto
    para: Paula Andrade (Engenharia de Aplicação)
  - quando: cliente constar com pendência financeira ou sem limite de crédito
    para: Júlio Tavares (Financeiro)
---

## Manual: emitir cotação

Este é o procedimento padrão para responder a um pedido de orçamento com
uma cotação formal. Ele existe para garantir que toda cotação que sai da
empresa esteja correta, dentro da política comercial e registrada.

**1. Levante os itens.** Entenda exatamente o que o cliente quer: produto,
código, quantidade. Se a descrição do cliente for ambígua ("aquele modelo
maior"), confirme com ele antes de cotar — cotação de item errado custa
retrabalho e credibilidade. Dúvida de especificação técnica não se adivinha:
escale para a Engenharia de Aplicação.

**2. Consulte os preços.** Use sempre o preço de tabela vigente e verifique
disponibilidade. Não reaproveite preço de cotação antiga — tabela muda.

**3. Monte a cotação.** Gere o documento formal com condições da tabela
padrão. Desconto até 5% está na alçada; qualquer pedido acima disso para
aqui e sobe para o gestor ANTES de qualquer sinalização ao cliente.

**4. Confira.** Releia o documento como se fosse o cliente: valores, prazos,
condições, dados cadastrais, destinatário do e-mail. Esta conferência é
obrigatória e nunca é pulada, por mais urgente que seja o pedido.

**5. Envie.** E-mail objetivo, cotação em anexo, validade explícita no corpo
da mensagem. Registre o envio e fique responsável pelo follow-up.

Em qualquer ponto: se a situação não estiver prevista neste manual, não
improvise — escale conforme a tabela de escalonamento acima.
