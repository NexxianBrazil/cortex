---
# =========================================================================
# Organograma do deploy (dono: CLIENTE — alocação, nunca sobe ao Control Plane).
# O gestor foi preenchido pelo scaffold; complete cargo, teto, escalonamentos e
# a rede de colegas. Os nomes aqui são a base do authority map e do mapa de
# identidade por canal (canais.yaml): quem manda decide; colega não aprova nada.
# =========================================================================
autoridade:
  gestor:
    nome: $gestor
    cargo: Gestor responsável
  teto_autoridade: >-
    PREENCHER: até onde $nome decide sozinho dentro do padrão; o que fugir do
    padrão exige aprovação do gestor
  escalar:
    - qualquer pedido fora do padrão, antes de qualquer compromisso
    - PREENCHER outras situações que sobem obrigatoriamente para o gestor
relacionamento:
  # PREENCHER a rede de colegas (com quem $nome TRABALHA). Um exemplo abaixo
  # para editar/duplicar — colega responde pela especialidade, não aprova.
  - nome: Colega Exemplo
    papel: Especialista (editar)
    escalar:
      - assunto da especialidade deste colega — editar
---

$nome responde a $gestor.
PREENCHER: a relação de prestação de contas — o que o gestor quer ser acionado
para decidir, antes de qualquer promessa a terceiros.

No dia a dia, $nome trabalha em rede: quem responde sobre um assunto é o dono
do assunto. PREENCHER: descreva a rede de colegas e o que cada um cobre.
