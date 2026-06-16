"""Heartbeat (Fase 8) — a saúde LOCAL do Cortex (telemetria do §7).

Coleta métricas locais por limiares simples e devolve um status OK/WARNING/ERROR.
Stdlib only (disco via shutil; CPU via os.getloadavg; RAM via /proc ou psutil
OPCIONAL com fallback — sem dep nova). Aqui fica LOCAL; subir telemetria
sanitizada ao Control Plane é a Fase 6.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger("cortex.ops")

# Limiares de disco livre (%) — abaixo disso, alerta.
_DISCO_WARNING = 10.0
_DISCO_ERROR = 3.0


class StatusSaude(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


def _memoria_pct() -> float | None:
    """% de RAM em uso — psutil se houver, senão /proc/meminfo (Linux); senão None."""
    try:
        import psutil  # opcional: nunca exigido

        return float(psutil.virtual_memory().percent)
    except ImportError:
        pass
    try:
        info: dict[str, int] = {}
        for linha in Path("/proc/meminfo").read_text().splitlines():
            chave, _, resto = linha.partition(":")
            info[chave] = int(resto.strip().split()[0])  # kB
        total, disp = info.get("MemTotal", 0), info.get("MemAvailable", 0)
        return round((total - disp) / total * 100, 1) if total else None
    except (OSError, ValueError, IndexError):
        return None


def _cpu_load() -> float | None:
    """Load average de 1 min (Unix); None onde não há getloadavg."""
    try:
        return round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        return None


class Heartbeat:
    """Snapshot de saúde local — disco, CPU, RAM, banco, sessões, pendentes."""

    def __init__(
        self,
        *,
        caminho_disco: str | Path = ".",
        db_path: Path | None = None,
        sessoes_ativas: Callable[[], int] = lambda: 0,
        pendentes: Callable[[], int] = lambda: 0,
        ultimo_turno: Callable[[], datetime | None] = lambda: None,
    ) -> None:
        self._caminho_disco = str(caminho_disco)
        self._db_path = db_path
        self._sessoes = sessoes_ativas
        self._pendentes = pendentes
        self._ultimo_turno = ultimo_turno

    def coletar(self) -> dict:
        uso = shutil.disk_usage(self._caminho_disco)
        disco_livre_pct = round(uso.free / uso.total * 100, 1)
        status = self._status(disco_livre_pct)
        ultimo = self._ultimo_turno()
        return {
            "status": status.value,
            "ts": datetime.now(UTC).isoformat(),
            "disco_livre_pct": disco_livre_pct,
            "cpu_load_1min": _cpu_load(),
            "memoria_uso_pct": _memoria_pct(),
            "banco_bytes": self._banco_bytes(),
            "sessoes_ativas": self._sessoes(),
            "propostas_pendentes": self._pendentes(),
            "ultimo_turno": ultimo.isoformat() if ultimo else None,
        }

    def _banco_bytes(self) -> int | None:
        if self._db_path is None or not Path(self._db_path).exists():
            return None
        return Path(self._db_path).stat().st_size

    @staticmethod
    def _status(disco_livre_pct: float) -> StatusSaude:
        if disco_livre_pct < _DISCO_ERROR:
            return StatusSaude.ERROR
        if disco_livre_pct < _DISCO_WARNING:
            return StatusSaude.WARNING
        return StatusSaude.OK
