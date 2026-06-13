"""Resolução de identidade por canal (Fase 7a) — o canais.yaml do deploy.

O canais.yaml (dono: CLIENTE) mapeia (canal, canal_id) → pessoa do USER.md. A
autoridade segue o CANAL: mapeado vira INTERNA com o nome real; QUALQUER outro
remetente vira EXTERNA "desconhecido(...)" — alegar identidade no texto não
compra nada. Nome de pessoa que não exista no USER.md é mapa ÓRFÃO: erro alto no
startup (mesmo padrão do invariante sem linhagem da 4c).
"""

from pathlib import Path

import yaml

from cortex.identity.models import Persona
from cortex.runtime.identidade import (
    Identidade,
    identidade_externa,
    identidade_interna,
    papel_no_user_md,
)

ChaveCanal = tuple[str, str]


class CanaisError(Exception):
    """canais.yaml malformado ou com pessoa órfã (inexistente no USER.md)."""


def carregar_mapa_identidades(
    caminho: Path | str, persona: Persona
) -> dict[ChaveCanal, str]:
    """Lê o canais.yaml e devolve {(canal, canal_id): pessoa}, validando órfãos.

    Ausência do arquivo = mapa vazio (deploy ainda sem canais mapeados: todos os
    remetentes são EXTERNOS até serem configurados). Pessoa que não exista no
    USER.md aborta o carregamento — é erro de configuração do deploy.
    """
    caminho = Path(caminho)
    if not caminho.is_file():
        return {}
    try:
        dados = yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise CanaisError(f"{caminho}: YAML malformado. Detalhe: {exc}") from exc

    entradas = dados.get("identidades") or []
    if not isinstance(entradas, list):
        raise CanaisError(f"{caminho}: 'identidades' deve ser uma lista.")

    mapa: dict[ChaveCanal, str] = {}
    for i, item in enumerate(entradas):
        if not isinstance(item, dict) or not {"canal", "canal_id", "pessoa"} <= item.keys():
            raise CanaisError(
                f"{caminho}: identidade #{i} precisa de 'canal', 'canal_id' e 'pessoa'."
            )
        pessoa = str(item["pessoa"])
        if papel_no_user_md(persona, pessoa) is None:
            raise CanaisError(
                f"{caminho}: pessoa '{pessoa}' não existe no USER.md (nem gestor nem "
                "colega) — mapa de canal órfão. Corrija o nome ou o USER.md."
            )
        mapa[(str(item["canal"]), str(item["canal_id"]))] = pessoa
    return mapa


def resolver_identidade(
    canal: str, canal_id: str, mapa: dict[ChaveCanal, str], persona: Persona
) -> Identidade:
    """Identidade do remetente: mapeada → INTERNA; não mapeada → EXTERNA."""
    pessoa = mapa.get((canal, canal_id))
    if pessoa is None:
        return identidade_externa(canal, canal_id)
    return identidade_interna(persona, pessoa, canal=canal, canal_id=canal_id)
