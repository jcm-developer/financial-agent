#!/usr/bin/env python
"""Planificador de ciclos. Es el proceso principal del contenedor `scheduler`.

Duerme hasta la siguiente hora configurada y lanza un ciclo. Se implementa aqui
en lugar de instalar cron en la imagen porque asi los logs salen por stdout (que
es donde Docker los espera) y el contenedor no necesita un gestor de procesos.

Configuracion por entorno:

    CYCLE_TIMES    Horas de ejecucion, "HH:MM" separadas por comas. Def. "22:15"
    CYCLE_TZ       Zona horaria de esas horas. Def. "Europe/Madrid"
    RUN_ON_START   Si es true, ejecuta un ciclo al arrancar. Def. false

Cada ciclo se ejecuta como un subproceso aparte a proposito: si una llamada al
modelo se cuelga o el proceso muere, el planificador sobrevive y la siguiente
ejecucion sigue en pie.
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

# Trozos de espera cortos para que el contenedor responda a `docker stop` en
# segundos en lugar de esperar el timeout de SIGKILL.
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
    # Inalcanzable: con dos dias de margen siempre hay una hora posterior.
    raise RuntimeError("No se pudo calcular la siguiente ejecucion.")


def sleep_until(target: datetime, timezone: ZoneInfo) -> bool:
    """Espera hasta `target`. Devuelve False si llega una senal de parada."""
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
        # No se aborta: un ciclo fallido (red, cuota del modelo) no debe impedir
        # el intento de manana.
        log.error(
            "El ciclo termino con codigo %d tras %.0fs. Revisa el log anterior "
            "y `python run.py report`.", result.returncode, elapsed,
        )
    return result.returncode


def validate_config() -> bool:
    """Comprueba las credenciales una vez, antes de dormir horas en balde."""
    from src.config import ConfigError, Settings

    try:
        settings = Settings.load()
    except ConfigError as exc:
        log.error("Configuracion incompleta, el planificador no puede operar:\n%s", exc)
        log.error(
            "Si solo quieres el dashboard, para este servicio con:  "
            "docker compose stop scheduler"
        )
        return False

    log.info("Configuracion cargada: %s", settings.describe())
    if not settings.alpaca_paper:
        log.warning(
            "ALPACA_PAPER=false: los ciclos programados enviaran ordenes con DINERO "
            "REAL sin pedir confirmacion. La confirmacion interactiva solo existe "
            "al ejecutar `run.py cycle` a mano."
        )
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
        # Salida limpia: con `restart: on-failure` el contenedor no entra en
        # bucle de reinicios por un .env a medio rellenar.
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
