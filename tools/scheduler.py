#!/usr/bin/env python
"""Cycle scheduler. It is the main process of the `scheduler` container.

It sleeps until the next configured time and launches a cycle. It is implemented
here instead of installing cron in the image because that way the logs come out
on stdout (which is where Docker expects them) and the container needs no process
manager.

Configuration by environment:

    CYCLE_TIMES    Run times, "HH:MM" separated by commas. Default "22:15"
    CYCLE_TZ       Time zone of those times. Default "Europe/Madrid"
    RUN_ON_START   If true, runs a cycle at startup. Default false

Each cycle runs as a separate subprocess on purpose: if a call to the model hangs
or the process dies, the scheduler survives and the next run is still standing.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

log = logging.getLogger("scheduler")

# Short waiting slices so the container answers `docker stop` in seconds instead
# of waiting out the SIGKILL timeout.
SLEEP_CHUNK_SECONDS = 30

_stopping = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _stopping
    _stopping = True
    log.info("Recibida senal %s; se detendra tras el ciclo en curso.", signum)


def parse_times(raw: str) -> list[tuple[int, int]]:
    """Convierte "09:30, 22:15" en [(9, 30), (22, 15)], ordenado."""
    times: list[tuple[int, int]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            hour_text, minute_text = chunk.split(":")
            hour, minute = int(hour_text), int(minute_text)
        except ValueError as exc:
            raise SystemExit(
                f"CYCLE_TIMES invalido: {chunk!r}. Formato esperado HH:MM, "
                f"por ejemplo '22:15' o '15:35,22:15'."
            ) from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise SystemExit(f"CYCLE_TIMES fuera de rango: {chunk!r}.")
        times.append((hour, minute))

    if not times:
        raise SystemExit("CYCLE_TIMES esta vacio: no hay ninguna hora que planificar.")
    return sorted(set(times))


def next_run(now: datetime, times: list[tuple[int, int]]) -> datetime:
    """Primera hora programada posterior a `now`, hoy o manana."""
    for day_offset in (0, 1):
        day = now + timedelta(days=day_offset)
        for hour, minute in times:
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > now:
                return candidate
    # Unreachable: with two days of margin there is always a later time.
    raise RuntimeError("No se pudo calcular la siguiente ejecucion.")


def sleep_until(target: datetime, timezone: ZoneInfo) -> bool:
    """Waits until `target`. Returns False if a stop signal arrives."""
    while not _stopping:
        remaining = (target - datetime.now(timezone)).total_seconds()
        if remaining <= 0:
            return True
        time.sleep(min(SLEEP_CHUNK_SECONDS, remaining))
    return False


def run_cycle() -> int:
    log.info("Lanzando ciclo…")
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "run.py", "cycle"], cwd=str(APP_DIR), check=False
    )
    elapsed = time.monotonic() - started

    if result.returncode == 0:
        log.info("Ciclo completado en %.0fs.", elapsed)
    else:
        # It is not aborted: a failed cycle (network, model quota) must not stop
        # tomorrow's attempt.
        log.error(
            "El ciclo termino con codigo %d tras %.0fs. Revisa el log anterior "
            "y `python run.py report`.", result.returncode, elapsed,
        )
    return result.returncode


def validate_config() -> bool:
    """Checks profile and credentials once, before sleeping for hours in vain.

    The profile is resolved here even though the cycle resolves it again in its
    subprocess: finding out there is no active profile at 22:15, after eight
    hours asleep, is the worst possible time to learn it.
    """
    from src.config import ConfigError, Infra

    try:
        from src.profile_settings import load_for_cycle

        _, settings = load_for_cycle(Infra.load())
    except ConfigError as exc:
        log.error("Configuracion incompleta, el planificador no puede operar:\n%s", exc)
        log.error(
            "Si solo quieres la interfaz, para este servicio con:  "
            "docker compose stop scheduler"
        )
        return False

    log.info("Configuracion cargada: %s", settings.describe())
    log.info("Riesgo: %s", settings.risk_summary)
    return True


def main() -> int:
    logging.basicConfig(
        level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
        format="%(asctime)s  %(levelname)-7s %(name)-12s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if not validate_config():
        # Clean exit: with `restart: on-failure` the container does not enter a
        # restart loop over a half-filled .env.
        return 0

    tz_name = (os.getenv("CYCLE_TZ") or "Europe/Madrid").strip()
    try:
        timezone = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        log.error("CYCLE_TZ desconocida: %r. Usando UTC.", tz_name)
        timezone = ZoneInfo("UTC")

    times = parse_times(os.getenv("CYCLE_TIMES") or "22:15")
    schedule = ", ".join(f"{h:02d}:{m:02d}" for h, m in times)
    log.info("Planificacion: %s (%s), %d ciclo(s) al dia.", schedule, tz_name, len(times))

    run_on_start = (os.getenv("RUN_ON_START") or "").strip().lower() in {
        "1", "true", "yes", "y", "on",
    }
    if run_on_start and not _stopping:
        log.info("RUN_ON_START activo: ciclo inmediato.")
        run_cycle()

    while not _stopping:
        target = next_run(datetime.now(timezone), times)
        log.info("Siguiente ciclo: %s", target.strftime("%Y-%m-%d %H:%M %Z"))
        if not sleep_until(target, timezone):
            break
        run_cycle()

    log.info("Planificador detenido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
