"""Audit Engine — trilha append-only de decisões e custo (Fase 4c).

Audit-first: rastro estruturado e DURÁVEL de todas as decisões, incluindo
quanto cada request de LLM custou em tokens. Formato JSONL (uma linha JSON por
evento) — simples de anexar, fácil de ler linha a linha, sem schema rígido.

ONDE: arquivo local no Data Plane (on-prem). NUNCA sobe ao Control Plane — só
telemetria AGREGADA subiria, e isso é Fase 6. Aqui é rastro local e cru.

TRADE-OFF DECIDIDO: falha de escrita do audit NÃO derruba o turno (loga warning
e segue). No MVP, disponibilidade da operação > completude do rastro — um disco
cheio não pode travar a persona. Quando o audit virar requisito de compliance
duro, isto se inverte.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("cortex.governance")


class AuditTrail:
    """Trilha append-only em JSONL. Cada `registrar` acrescenta uma linha."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def registrar(self, tipo: str, **campos: Any) -> None:
        """Grava uma linha JSON com timestamp UTC + tipo + campos arbitrários.

        Tipos desta fase: 'decisao_tool', 'llm_request', 'turno'. Falha de
        escrita só gera warning — o turno segue (ver trade-off no módulo).
        """
        linha = {"ts": datetime.now(UTC).isoformat(), "tipo": tipo, **campos}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logger.warning("falha ao gravar audit (%s): %s", self._path, exc)

    def ultimos(self, n: int) -> list[dict]:
        """Lê as últimas `n` linhas do JSONL como dicts (para inspeção/CLI)."""
        if not self._path.is_file():
            return []
        linhas = self._path.read_text(encoding="utf-8").splitlines()
        out: list[dict] = []
        for linha in linhas[-n:]:
            try:
                out.append(json.loads(linha))
            except json.JSONDecodeError:
                continue
        return out
