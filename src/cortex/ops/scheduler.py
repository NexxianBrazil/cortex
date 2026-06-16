"""Scheduler mínimo (Fase 8) — agenda jobs no single-node, sem dep nova.

Uma `threading.Thread` daemon que dispara callbacks em horários/intervalos. Sem
APScheduler/cron/celery — o MVP é single-node on-prem. O relógio é INJETÁVEL e
a lógica de disparo (`tick`) é separada do loop da thread, para os testes
exercitarem o agendamento avançando o relógio na mão, sem dormir.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("cortex.ops")


@dataclass
class _Job:
    proximo: datetime
    callback: Callable[[], None]
    nome: str
    intervalo: timedelta | None = None  # 'a cada N' (re-arma por intervalo)
    hora_diaria: tuple[int, int] | None = None  # 'todo dia HH:MM'


class Scheduler:
    """Agenda 'a cada N minutos' e 'todo dia HH:MM'. Relógio injetável (testes)."""

    def __init__(self, agora: Callable[[], datetime] | None = None) -> None:
        self._agora = agora or (lambda: datetime.now(UTC))
        self._jobs: list[_Job] = []
        self._thread: threading.Thread | None = None
        self._parar = threading.Event()

    def agendar_intervalo(self, minutos: int, callback, nome: str = "intervalo") -> None:
        intervalo = timedelta(minutes=minutos)
        self._jobs.append(
            _Job(
                proximo=self._agora() + intervalo,
                callback=callback,
                nome=nome,
                intervalo=intervalo,
            )
        )

    def agendar_diario(self, hh_mm: str, callback, nome: str = "diario") -> None:
        h, m = _parse_hh_mm(hh_mm)
        self._jobs.append(
            _Job(
                proximo=self._proximo_diario(h, m),
                callback=callback,
                nome=nome,
                hora_diaria=(h, m),
            )
        )

    def _proximo_diario(self, h: int, m: int) -> datetime:
        agora = self._agora()
        alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
        return alvo if alvo > agora else alvo + timedelta(days=1)

    def tick(self) -> int:
        """Dispara os jobs vencidos e os re-arma. Devolve quantos dispararam."""
        agora = self._agora()
        disparados = 0
        for job in self._jobs:
            if agora >= job.proximo:
                disparados += 1
                try:
                    job.callback()
                except Exception:  # noqa: BLE001 — um job não pode derrubar o loop
                    logger.exception("job '%s' falhou", job.nome)
                if job.intervalo is not None:
                    job.proximo = agora + job.intervalo
                else:  # diário
                    job.proximo = self._proximo_diario(*job.hora_diaria)
        return disparados

    # ---- loop em thread (produção) --------------------------------------- #

    def iniciar(self, tick_segundos: float = 30.0) -> None:
        if self._thread is not None:
            return
        self._parar.clear()

        def _loop() -> None:
            while not self._parar.wait(tick_segundos):
                self.tick()

        self._thread = threading.Thread(target=_loop, name="cortex-scheduler", daemon=True)
        self._thread.start()
        logger.info("scheduler iniciado (%d job(s))", len(self._jobs))

    def parar(self, timeout: float = 2.0) -> None:
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None


def _parse_hh_mm(hh_mm: str) -> tuple[int, int]:
    try:
        h_str, m_str = hh_mm.strip().split(":")
        h, m = int(h_str), int(m_str)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"horário inválido '{hh_mm}': use HH:MM") from exc
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"horário fora do intervalo: {hh_mm}")
    return h, m
