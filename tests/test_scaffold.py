"""Testes do scaffold `cortex novo` (Fase 7a).

O deploy gerado tem de NASCER VÁLIDO: a persona carrega, a formação universal
está presente e a config aponta para dentro do deploy.
"""

import pytest
import yaml

from cortex.config import carregar_config
from cortex.identity import carregar_persona
from cortex.runtime import autoridade_da_persona
from cortex.runtime.promotion import DOMINIO_PADRAO
from cortex.scaffold import ScaffoldError, gerar_deploy

COMPORTAMENTOS_UNIVERSAIS = {
    "conferir_antes_de_enviar",
    "escalar_quando_incerto",
    "nao_prometer_sem_lastro",
    "transparencia_sobre_incerteza",
}


def test_novo_gera_deploy_que_carrega(tmp_path):
    d = gerar_deploy(
        tmp_path / "rafael",
        nome="Rafael",
        funcao="suporte técnico",
        gestor="Denilson",
        token="TKN",
    )

    persona = carregar_persona(d / "personas")
    assert persona.soul.nome == "Rafael"
    assert persona.soul.papel == "suporte técnico"
    # A formação UNIVERSAL (camada 1) está toda no SOUL gerado.
    assert {c.id for c in persona.soul.comportamentos} == COMPORTAMENTOS_UNIVERSAIS

    cfg = carregar_config(d)
    # cortex.toml aponta para DENTRO do deploy (caminhos resolvidos).
    assert cfg.personas_dir == d.resolve() / "personas"
    assert cfg.kb_path == d.resolve() / "kb"
    assert cfg.server_token.get_secret_value() == "TKN"

    # canais.yaml de exemplo parseia (lista vazia, pronta para o cliente preencher).
    dados = yaml.safe_load((d / "canais.yaml").read_text(encoding="utf-8"))
    assert "identidades" in dados and dados["identidades"] == []


def test_novo_gestor_reflete_e_torna_autoritativo(tmp_path):
    d = gerar_deploy(
        tmp_path / "r", nome="Rafael", funcao="suporte", gestor="Denilson Medeiros", token="T"
    )
    persona = carregar_persona(d / "personas")
    assert persona.user.autoridade.gestor.nome == "Denilson Medeiros"
    # O gestor do USER.md vira autoritativo no domínio (mesma régua da 4b).
    amap = autoridade_da_persona(persona)
    assert amap.is_authoritative("Denilson Medeiros", DOMINIO_PADRAO)


def test_novo_recusa_destino_nao_vazio(tmp_path):
    alvo = tmp_path / "ocupado"
    alvo.mkdir()
    (alvo / "algo.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ScaffoldError, match="não está vazio"):
        gerar_deploy(alvo, nome="A", funcao="b", gestor="C")
