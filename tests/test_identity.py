"""Testes da camada de identidade (Fase 1).

Os testes positivos usam os arquivos de exemplo reais de `personas/` — assim
eles cumprem papel duplo: validam o parser E garantem que os seeds do
repositório nunca quebram. Os testes de erro constroem arquivos defeituosos
em `tmp_path` para provar que o parser falha alto e com mensagem clara.
"""

import shutil
from pathlib import Path

import pytest

from cortex.identity import (
    Persona,
    PersonaParseError,
    Playbook,
    ReferenciaInvalidaError,
    Soul,
    User,
    carregar_persona,
    carregar_playbook,
    carregar_soul,
    carregar_tools,
    carregar_user,
)
from cortex.risk import RiskLevel

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"


# ---------------------------------------------------------------------------
# Caminho feliz: os arquivos de exemplo carregam e produzem os modelos certos
# ---------------------------------------------------------------------------


def test_carregar_soul_exemplo():
    soul = carregar_soul(PERSONAS_DIR / "SOUL.md")
    assert isinstance(soul, Soul)
    assert soul.nome == "Mariana"
    assert soul.papel == "Analista Comercial"
    assert len(soul.comportamentos) >= 3
    ids = {c.id for c in soul.comportamentos}
    assert "conferir_antes_de_enviar" in ids
    assert "escalar_quando_incerto" in ids
    # A prosa (identidade/tom/valores) precisa ter sobrevivido ao parse.
    assert "Mariana" in soul.prosa


def test_carregar_user_exemplo():
    user = carregar_user(PERSONAS_DIR / "USER.md")
    assert isinstance(user, User)
    # Bloco autoridade: quem MANDA.
    assert user.autoridade.gestor.nome == "Carlos Menezes"
    assert user.autoridade.escalar  # algo obrigatoriamente sobe pro gestor
    # Bloco relacionamento: com quem TRABALHA (distinto de quem manda).
    assert len(user.relacionamento) >= 2
    papeis = {c.papel for c in user.relacionamento}
    assert "Engenheira de Aplicação" in papeis


def test_carregar_tools_exemplo():
    tools = carregar_tools(PERSONAS_DIR / "tools.yaml")
    assert set(tools) >= {"consultar_preco", "emitir_cotacao", "enviar_email"}
    # O risco base é o enum compartilhado, não uma string solta.
    assert tools["consultar_preco"].risco_base is RiskLevel.LOW
    assert tools["enviar_email"].risco_base is RiskLevel.HIGH
    assert tools["emitir_cotacao"].parametros  # parâmetros declarados


def test_carregar_playbook_exemplo():
    playbook = carregar_playbook(PERSONAS_DIR / "playbooks" / "emitir_cotacao.md")
    assert isinstance(playbook, Playbook)
    assert playbook.operacao == "emitir_cotacao"
    assert len(playbook.passos) == 5
    # Ordem e dependências preservadas.
    primeiro = playbook.passos[0]
    assert primeiro.ordem == 1 and not primeiro.depende_de
    # Tools referenciadas extraídas dos passos (não redeclaradas no playbook).
    assert playbook.tools_referenciadas == {"consultar_preco", "emitir_cotacao", "enviar_email"}
    assert playbook.escalonamento  # há pontos de escalonamento definidos
    assert "Manual" in playbook.prosa


def test_carregar_persona_completa():
    persona = carregar_persona(PERSONAS_DIR)
    assert isinstance(persona, Persona)
    assert persona.soul.nome == "Mariana"
    assert "emitir_cotacao" in persona.playbooks
    # Integridade referencial fechada: toda tool referenciada existe no catálogo.
    for playbook in persona.playbooks.values():
        assert playbook.tools_referenciadas <= set(persona.tools)


# ---------------------------------------------------------------------------
# Falhas: o parser deve falhar ALTO e com mensagem explicativa
# ---------------------------------------------------------------------------


def _copiar_persona_exemplo(destino: Path) -> Path:
    """Copia a persona de exemplo para um diretório editável pelo teste."""
    pasta = destino / "personas"
    shutil.copytree(PERSONAS_DIR, pasta)
    return pasta


def test_playbook_com_tool_inexistente_levanta_erro_claro(tmp_path):
    pasta = _copiar_persona_exemplo(tmp_path)
    (pasta / "playbooks" / "operacao_quebrada.md").write_text(
        "---\n"
        "operacao: operacao_quebrada\n"
        "descricao: playbook defeituoso para teste\n"
        "passos:\n"
        "  - id: unico\n"
        "    ordem: 1\n"
        "    descricao: usa uma tool que nao existe\n"
        "    tool: tool_fantasma\n"
        "---\n"
        "Manual de teste.\n",
        encoding="utf-8",
    )
    with pytest.raises(ReferenciaInvalidaError) as exc:
        carregar_persona(pasta)
    mensagem = str(exc.value)
    # A mensagem precisa dizer QUAL playbook e QUAL tool — não um erro genérico.
    assert "operacao_quebrada" in mensagem
    assert "tool_fantasma" in mensagem


def test_yaml_malformado_levanta_erro_claro(tmp_path):
    arquivo = tmp_path / "SOUL.md"
    arquivo.write_text(
        "---\n"
        "nome: Mariana\n"
        "papel: [lista nunca fechada\n"
        "---\n"
        "prosa\n",
        encoding="utf-8",
    )
    with pytest.raises(PersonaParseError) as exc:
        carregar_soul(arquivo)
    mensagem = str(exc.value)
    assert "YAML malformado" in mensagem
    assert str(arquivo) in mensagem  # aponta o arquivo problemático


def test_campo_obrigatorio_faltando_levanta_erro_claro(tmp_path):
    # SOUL sem 'papel' e sem 'comportamentos' — pydantic deve apontar os campos.
    arquivo = tmp_path / "SOUL.md"
    arquivo.write_text(
        "---\nnome: Mariana\n---\nprosa\n",
        encoding="utf-8",
    )
    with pytest.raises(PersonaParseError) as exc:
        carregar_soul(arquivo)
    mensagem = str(exc.value)
    assert "papel" in mensagem
    assert "comportamentos" in mensagem


def test_frontmatter_ausente_levanta_erro_claro(tmp_path):
    arquivo = tmp_path / "SOUL.md"
    arquivo.write_text("So prosa, sem frontmatter.\n", encoding="utf-8")
    with pytest.raises(PersonaParseError, match="frontmatter YAML ausente"):
        carregar_soul(arquivo)


def test_dependencia_de_passo_inexistente_levanta_erro_claro(tmp_path):
    arquivo = tmp_path / "playbook.md"
    arquivo.write_text(
        "---\n"
        "operacao: teste\n"
        "descricao: dependencia quebrada\n"
        "passos:\n"
        "  - id: passo_a\n"
        "    ordem: 1\n"
        "    descricao: depende de passo que nao existe\n"
        "    depende_de: [passo_inexistente]\n"
        "---\n"
        "Manual.\n",
        encoding="utf-8",
    )
    with pytest.raises(PersonaParseError) as exc:
        carregar_playbook(arquivo)
    assert "passo_inexistente" in str(exc.value)
