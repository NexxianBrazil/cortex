---
# Playbook de EXEMPLO — substitua pela primeira operação real de $nome.
operacao: exemplo_operacao
descricao: Operação de exemplo gerada pelo scaffold; mostra a estrutura mínima.
passos:
  - id: entender_pedido
    ordem: 1
    descricao: Entender o que foi pedido e identificar a operação aplicável.
  - id: consultar_politica
    ordem: 2
    descricao: Consultar a KB para a política/regra que rege o caso.
    tool: consultar_kb
    depende_de: [entender_pedido]
  - id: responder_ou_escalar
    ordem: 3
    descricao: Responder com base na política; na ausência dela, escalar ao gestor.
    depende_de: [consultar_politica]
escalonamento:
  - quando: o caso não tiver política aplicável na KB
    para: gestor
---

Este é um manual de exemplo. A operação real de $nome substitui este arquivo:
descreva os passos como $nome os executaria, citando as tools do catálogo e os
pontos em que a operação sai do caminho feliz e precisa escalar.

A regra da casa: na dúvida, consultar a verdade declarada (KB) antes de
responder; sem política aplicável, escalar em vez de improvisar.
