"""Extração de fatos da CONVERSA — o "ouvido" do Cortex (Fase 7b).

Até aqui só virava memória o que uma tool retornava (promotion.py). Esta fase
adiciona uma SEGUNDA fonte de candidatos: fatos duráveis ditos na conversa
("o cliente ACME mudou o prazo para 45 dias"). Mas a régua não afrouxa —
precisão > recall, e TODO candidato ainda passa pelo observe() governado
(ceticismo, procedência, supersessão). A conversa genérica NÃO vira belief.

Doutrina:
- O Source do fato é a IDENTIDADE autenticada do turno (4b/7a), nunca um nome
  citado no texto. Fato de canal EXTERNO nasce EXTERNO (teto 0.4) e tende a
  escalar. Fato de conversa é NÃO-VERIFICÁVEL por padrão.
- Chave que casa um padrão de system-of-record (preço, limite, condição) é
  DESCARTADA — dado vivo se consulta, não se memoriza (Plano 4).
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence

from cortex.memory.models import Justification, Source, SourceKind
from cortex.runtime.identidade import Identidade
from cortex.runtime.messages import Message, Role
from cortex.runtime.promotion import PromotionCandidate
from cortex.runtime.providers.base import LLMProvider

logger = logging.getLogger("cortex.runtime")

# Padrões de chave que pertencem ao system of record (espelham sor/truth.py).
# Fato de conversa com uma dessas chaves é dado VIVO — não vira crença.
_CHAVES_SOR = (
    re.compile(r"^produto:.+:preco$"),
    re.compile(r"^cliente:.+:limite_credito$"),
    re.compile(r"^cliente:.+:condicao_pagamento$"),
)


def _eh_chave_de_sor(key: str) -> bool:
    return any(p.match(key) for p in _CHAVES_SOR)


def _textos_do_usuario(turno: Sequence[Message]) -> list[str]:
    """Só o que o REMETENTE disse — fatos se atribuem a quem fala, não à persona."""
    return [m.content for m in turno if m.role is Role.USER and m.content.strip()]


def _candidato(
    key: str, value: str, porque: str, identidade: Identidade
) -> PromotionCandidate | None:
    """Monta um candidato com Source = identidade do turno; descarta chave de SOR."""
    key = key.strip()
    value = value.strip()
    if not key or not value:
        return None
    if _eh_chave_de_sor(key):
        logger.info("extração de conversa descartou chave de system-of-record: %s", key)
        return None
    source = Source(
        name=identidade.nome, kind=SourceKind.HUMAN, procedencia=identidade.procedencia
    )
    return PromotionCandidate(
        key=key,
        value=value,
        source=source,
        justification=Justification(why=porque or None, verifiable=False),
    )


class ExtratorConversa(ABC):
    """Contrato: do turno + identidade, zero ou mais candidatos a crença."""

    @abstractmethod
    def extrair(
        self, turno: Sequence[Message], identidade: Identidade
    ) -> list[PromotionCandidate]:
        """Fatos duráveis ditos no turno; lista vazia é o caso comum (e ok)."""


# ----------------------------- heurístico (CI) ----------------------------- #

# Formas EXPLICITÍSSIMAS de "registre isto": tipo entidade atributo = valor.
# Minimalista de propósito — a maioria das conversas não casa, e tudo bem.
_RE_ANOTA = re.compile(
    r"anota a[íi]:?\s+(\w+)\s+(\S+)\s+(\w+)\s*=\s*([^\n]+)", re.IGNORECASE
)
_RE_REGISTRA = re.compile(
    r"registra que\s+(\w+)\s+(\S+)\s+(\w+)\s+[ée]\s+([^\n]+)", re.IGNORECASE
)


class HeuristicExtratorConversa(ExtratorConversa):
    """Extrator determinístico, sem LLM — só padrões explícitos. Default de CI."""

    def extrair(
        self, turno: Sequence[Message], identidade: Identidade
    ) -> list[PromotionCandidate]:
        candidatos: list[PromotionCandidate] = []
        for texto in _textos_do_usuario(turno):
            for regex in (_RE_ANOTA, _RE_REGISTRA):
                for tipo, ent_id, atributo, valor in regex.findall(texto):
                    key = f"{tipo.lower()}:{ent_id}:{atributo.lower()}"
                    porque = f"dito na conversa: '{texto.strip()}'"
                    cand = _candidato(key, valor, porque, identidade)
                    if cand is not None:
                        candidatos.append(cand)
        return candidatos


# ------------------------------- via LLM ----------------------------------- #

_SYSTEM_EXTRACAO = (
    "Você extrai FATOS DURÁVEIS sobre entidades (cliente, produto, processo) ditos "
    "numa conversa, para a memória de longo prazo de um profissional digital.\n\n"
    "Extraia SÓ o que vale ALÉM desta conversa: mudanças de cadastro, decisões, "
    "preferências, condições combinadas. IGNORE pedidos, perguntas, conversa fiada "
    "e qualquer coisa efêmera. Na dúvida, NÃO extraia (precisão acima de recall).\n\n"
    "Responda APENAS com um array JSON (sem texto fora dele). Cada item: "
    '{"key": "<tipo>:<id>:<atributo>", "value": "<valor>", "porque": "<de onde no '
    'diálogo saiu>"}. Use chaves canônicas minúsculas, ex.: '
    '"cliente:ACME:prazo_pagamento". Nada relevante → array vazio [].'
)


def _parse_json_tolerante(texto: str | None) -> list[dict]:
    """Extrai o array JSON da resposta do LLM; ruído/erro → lista vazia (nunca levanta)."""
    if not texto:
        return []
    bruto = texto.strip()
    if bruto.startswith("```"):  # tira cercas de código ```json ... ```
        bruto = bruto.strip("`")
        bruto = bruto[4:] if bruto[:4].lower() == "json" else bruto
    inicio, fim = bruto.find("["), bruto.rfind("]")
    if inicio == -1 or fim == -1 or fim < inicio:
        return []
    try:
        dados = json.loads(bruto[inicio : fim + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in dados if isinstance(d, dict)] if isinstance(dados, list) else []


class LLMExtratorConversa(ExtratorConversa):
    """Extrai fatos via LLM (JSON estrito). Não acopla a memória — recebe o provider."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def extrair(
        self, turno: Sequence[Message], identidade: Identidade
    ) -> list[PromotionCandidate]:
        textos = _textos_do_usuario(turno)
        if not textos:
            return []
        pergunta = Message(role=Role.USER, content="\n".join(textos))
        try:
            resposta = self._provider.gerar(_SYSTEM_EXTRACAO, [pergunta], [])
        except Exception as exc:  # noqa: BLE001 — extração nunca derruba o turno
            logger.warning("extração de conversa via LLM falhou: %s", exc)
            return []

        candidatos: list[PromotionCandidate] = []
        for item in _parse_json_tolerante(resposta.texto):
            cand = _candidato(
                str(item.get("key", "")),
                str(item.get("value", "")),
                str(item.get("porque", "")),
                identidade,
            )
            if cand is not None:
                candidatos.append(cand)
        return candidatos


def criar_extrator_conversa(config, provider: LLMProvider) -> ExtratorConversa:
    """Extrator conforme a config: 'heuristico' (CI, sem rede) ou 'llm' (via provider)."""
    if config.extrator_conversa == "llm":
        return LLMExtratorConversa(provider)
    return HeuristicExtratorConversa()
