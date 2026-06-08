"""Utilitários de texto da memória (portados do protótipo).

Pequenos, puros e determinísticos — usados pela heurística do classificador
e pela avaliação de magnitude no motor. Ficam isolados aqui para serem
reutilizados sem arrastar dependência de modelos.
"""

import re


def normalizar(s: str) -> str:
    """Normaliza para comparação de igualdade: minúsculas, espaços colapsados.

    É assim que decidimos se dois valores são 'o mesmo fato' (reforço) ou
    'fatos diferentes' (contradição) sem depender de capitalização ou espaços.
    """
    return re.sub(r"\s+", " ", str(s).strip().lower())


def como_numero(s: str) -> float | None:
    """Extrai um número de uma string tipo 'R$ 50.000' → 50000.0.

    Usado só para detectar magnitude suspeita numa contradição (ex.: um valor
    10x maior que o vigente). Trata o ponto como separador de milhar e a
    vírgula como decimal (convenção pt-BR). Devolve None quando não há número.
    """
    bruto = str(s).replace(".", "").replace(",", ".")
    m = re.search(r"[\d.]+", bruto)
    if not m:
        return None
    try:
        return float(re.sub(r"[^\d.]", "", bruto))
    except ValueError:
        return None
