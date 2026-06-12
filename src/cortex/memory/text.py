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

    Usado para detectar magnitude suspeita numa contradição (ex.: um valor 10x
    maior que o vigente) e para comparar valores contra a fonte de verdade
    (ver `valores_equivalentes`). Trata o ponto como separador de milhar e a
    vírgula como decimal (convenção pt-BR — NÃO é en-US, e isso é deliberado:
    os valores do domínio são escritos em reais). Devolve None sem número.
    """
    bruto = str(s).replace(".", "").replace(",", ".")
    m = re.search(r"[\d.]+", bruto)
    if not m:
        return None
    try:
        return float(re.sub(r"[^\d.]", "", bruto))
    except ValueError:
        return None


def valores_equivalentes(a: str, b: str) -> bool:
    """Dois valores representam o MESMO fato?

    Se AMBOS parseiam como número (via `como_numero`), compara numericamente —
    assim '1250', 'R$ 1.250,00' e '1.250,00' são equivalentes apesar de textos
    diferentes. Caso contrário, cai para a igualdade textual normalizada
    (comportamento histórico, bom para valores não-numéricos como '28 DDL').

    Existe para a verificação contra a fonte de verdade não acusar o humano de
    estar errado por mera diferença de formatação ('1250' vs 'R$ 1.250,00').
    """
    na, nb = como_numero(a), como_numero(b)
    if na is not None and nb is not None:
        return na == nb
    return normalizar(a) == normalizar(b)
