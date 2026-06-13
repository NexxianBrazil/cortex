"""Testes da extração de fatos da conversa (Fase 7b) — o "ouvido".

Determinísticos: heurístico sem rede + LLMExtratorConversa com StubProvider.
A régua é alta — precisão > recall: conversa fiada não produz candidato.
"""

from cortex.memory.models import Procedencia, SourceKind
from cortex.runtime import Identidade, Message, Role
from cortex.runtime.extracao_conversa import HeuristicExtratorConversa, LLMExtratorConversa
from cortex.runtime.messages import LLMResponse
from cortex.runtime.providers import StubProvider


def _ident(proc=Procedencia.INTERNA, nome="Carlos Menezes"):
    return Identidade(nome=nome, procedencia=proc, papel="gestor")


def _turno(texto):
    return [Message(role=Role.USER, content=texto)]


def test_heuristico_extrai_fato_explicito_e_ignora_fiada():
    ext = HeuristicExtratorConversa()
    cands = ext.extrair(_turno("anota aí: cliente ACME prazo = 45 dias"), _ident())
    assert len(cands) == 1
    c = cands[0]
    assert c.key == "cliente:ACME:prazo"
    assert c.value == "45 dias"
    # Source = identidade do turno (não um nome citado no texto).
    assert c.source.name == "Carlos Menezes"
    assert c.source.kind is SourceKind.HUMAN
    assert c.source.procedencia is Procedencia.INTERNA
    assert c.justification.verifiable is False
    # Conversa fiada → nenhum candidato.
    assert ext.extrair(_turno("bom dia, tudo certo por aí?"), _ident()) == []


def test_heuristico_fato_de_canal_externo_nasce_externo():
    ext = HeuristicExtratorConversa()
    ident = _ident(Procedencia.EXTERNA, "desconhecido(whatsapp:+55)")
    cands = ext.extrair(_turno("registra que cliente ACME contato é joao@acme.com"), ident)
    assert len(cands) == 1
    assert cands[0].source.procedencia is Procedencia.EXTERNA


def test_heuristico_descarta_chave_de_system_of_record():
    # Preço é dado VIVO (Plano 4): consulta-se, não se memoriza. Não vira crença.
    ext = HeuristicExtratorConversa()
    assert ext.extrair(_turno("anota aí: produto PRD-001 preco = 999"), _ident()) == []


def test_llm_extrator_parseia_json_e_tolera_ruido():
    ident = _ident()
    json_ok = (
        '[{"key":"cliente:ACME:prazo_pagamento","value":"45 dias",'
        '"porque":"o gestor informou"}]'
    )
    ext = LLMExtratorConversa(StubProvider(roteiro=[LLMResponse(texto=json_ok)]))
    cands = ext.extrair(_turno("o ACME passou para 45 dias"), ident)
    assert len(cands) == 1
    assert cands[0].key == "cliente:ACME:prazo_pagamento"
    assert cands[0].value == "45 dias"

    # JSON inválido / ruído → 0 candidatos, sem derrubar o turno.
    ext_ruido = LLMExtratorConversa(StubProvider(roteiro=[LLMResponse(texto="desculpe, não sei")]))
    assert ext_ruido.extrair(_turno("blá blá"), ident) == []
