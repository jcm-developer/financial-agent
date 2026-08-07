#!/usr/bin/env python
"""Ingestor de precios: un tick por minuto mientras la bolsa US esta abierta.

Es el proceso principal del contenedor `ingestor`. La logica vive en
`src/ingest.py`; aqui solo esta el bucle, el reloj y el apagado limpio.

Configuracion por entorno:

    INGEST_ENABLED        false para apagarlo sin tocar el compose. Def. true
    INGEST_REFRESH_MIN    cada cuantos minutos se relee el universo. Def. 5
    INGEST_KEEP_DAYS      dias de barras de 1m que se conservan. Def. 90
    INGEST_MAX_FAILURES   fallos seguidos antes de gritar. Def. 5
    INGEST_THREADS        descargar en paralelo. Def. false (ver src/ingest.py)
    INGEST_OFFSET_SECONDS segundos tras el cambio de minuto. Def. 5
    DB_PATH               ruta de la base

Sobre el reloj: se despierta unos segundos *despues* del cambio de minuto, no
justo en el, porque la barra de un minuto no esta disponible hasta que ese minuto
ha terminado. Pedirla en el segundo 0 devuelve la del minuto anterior a medias.

Con el mercado cerrado no se pide nada: se duerme hasta la proxima apertura en
tramos cortos, para que `docker stop` responda en segundos en vez de esperar al
SIGKILL.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from src import market_calendar  # noqa: E402
from src.db import Database, DatabaseError  # noqa: E402
from src.ingest import YahooQuotes, ingest_once, load_last_timestamps  # noqa: E402

log = logging.getLogger("ingestor")

SLEEP_CHUNK_SECONDS = 20

_stopping = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _stopping
    _stopping = True
    log.info("Recibida senal %s; se detendra tras el tick en curso.", signum)


def _get_int(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s=%r no es un entero; se usa %d.", key, raw, default)
        return default


def _get_bool(key: str, default: bool) -> bool:
    raw = (os.getenv(key) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def sleep_until(target: datetime) -> bool:
    """Espera hasta `target`. Devuelve False si llega una senal de parada."""
    while not _stopping:
        restante = (target - datetime.now(timezone.utc)).total_seconds()
        if restante <= 0:
            return True
        time.sleep(min(SLEEP_CHUNK_SECONDS, restante))
    return False


def next_tick(offset_seconds: int) -> datetime:
    ahora = datetime.now(timezone.utc)
    objetivo = ahora.replace(second=0, microsecond=0) + timedelta(
        minutes=1, seconds=offset_seconds
    )
    if objetivo <= ahora:
        objetivo += timedelta(minutes=1)
    return objetivo


def podar(db: Database, keep_days: int) -> None:
    """Poda diaria de barras de 1 minuto.

    Sin esto el fichero crece para siempre: ~19.500 filas al dia con 50 simbolos.
    Las barras diarias no se pierden -- viven en `bar_cache`, que es de donde el
    agente calcula indicadores.
    """
    try:
        borradas = db.prune_bars_1m(keep_days=keep_days)
        if borradas:
            log.info("Poda: %d barras de mas de %d dias eliminadas.", borradas, keep_days)
    except DatabaseError as exc:
        log.warning("La poda fallo, se reintentara manana: %s", exc)


def main() -> int:
    logging.basicConfig(
        level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
        format="%(asctime)s  %(levelname)-7s %(name)-10s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if not _get_bool("INGEST_ENABLED", True):
        log.info("INGEST_ENABLED=false: el ingestor no arranca.")
        return 0

    db_path = (os.getenv("DB_PATH") or "data/trading.db").strip()
    refresh_min = _get_int("INGEST_REFRESH_MIN", 5)
    keep_days = _get_int("INGEST_KEEP_DAYS", 90)
    max_failures = _get_int("INGEST_MAX_FAILURES", 5)
    offset = _get_int("INGEST_OFFSET_SECONDS", 5)
    threads = _get_bool("INGEST_THREADS", False)

    provider = YahooQuotes(threads=threads)

    log.info(
        "Ingestor en marcha. base=%s universo cada %d min, retencion %d dias, "
        "paralelo=%s", db_path, refresh_min, keep_days, threads,
    )
    log.info("Mercado: %s", market_calendar.describe())

    symbols: list[str] = []
    symbols_edad = 10**9        # fuerza relectura en el primer tick
    fallos_seguidos = 0
    ultima_poda: str | None = None

    with Database(path=db_path) as db:
        last_ts = load_last_timestamps(db)
        log.info("Historico previo: %d simbolos con barras de 1m.", len(last_ts))

        while not _stopping:
            if not market_calendar.is_session_open():
                # Aprovecha que no hay nada que hacer para podar, una vez al dia.
                hoy = datetime.now(timezone.utc).date().isoformat()
                if ultima_poda != hoy:
                    podar(db, keep_days)
                    ultima_poda = hoy

                apertura = market_calendar.next_session_open().astimezone(timezone.utc)
                log.info(
                    "Mercado cerrado. Proxima apertura: %s (en %.1f h).",
                    apertura.isoformat(timespec="minutes"),
                    (apertura - datetime.now(timezone.utc)).total_seconds() / 3600,
                )
                if not sleep_until(apertura):
                    break
                symbols_edad = 10**9
                continue

            if symbols_edad >= refresh_min:
                nuevos = db.active_universe()
                if nuevos != symbols:
                    log.info(
                        "Universo: %d simbolos%s", len(nuevos),
                        f" ({', '.join(nuevos[:8])}{'...' if len(nuevos) > 8 else ''})"
                        if nuevos else " -- ningun perfil activo con universo",
                    )
                symbols = nuevos
                symbols_edad = 0

            if not symbols:
                # Sin perfiles activos no hay nada que seguir. No es un error.
                if not sleep_until(next_tick(offset)):
                    break
                symbols_edad += 1
                continue

            resultado = ingest_once(db, provider, symbols, last_ts=last_ts)

            if resultado.ok:
                fallos_seguidos = 0
                log.info(
                    "%d/%d simbolos  %d barras  %d ms descarga  %d ms escritura",
                    resultado.con_datos, resultado.pedidos, resultado.barras_escritas,
                    resultado.latencia_descarga_ms, resultado.latencia_escritura_ms,
                )
                if resultado.vacios:
                    log.warning(
                        "Sin datos: %s", ", ".join(resultado.vacios[:10])
                        + ("..." if len(resultado.vacios) > 10 else ""),
                    )
            else:
                fallos_seguidos += 1
                nivel = log.error if fallos_seguidos >= max_failures else log.warning
                nivel(
                    "Tick sin datos (%d seguidos): %s",
                    fallos_seguidos, resultado.error or "ningun simbolo devolvio barras",
                )
                if fallos_seguidos == max_failures:
                    log.error(
                        "%d fallos seguidos. Esto ya no parece un tropiezo puntual: "
                        "revisa la red, si Yahoo esta limitando por IP, o pasa al "
                        "plan B (DATA_PROVIDER=alpaca).", max_failures,
                    )

            if not sleep_until(next_tick(offset)):
                break
            symbols_edad += 1

    log.info("Ingestor detenido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
