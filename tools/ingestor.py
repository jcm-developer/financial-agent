#!/usr/bin/env python
"""Price ingestor: one tick per minute while any exchange is open.

It is the main process of the `ingestor` container. The logic lives in
`src/ingest.py`; here there is only the loop, the clock and the clean shutdown.

**It follows several exchanges at once.** Now that the market is a parameter of
the profile (`agent_settings.market`), two active profiles can trade in Madrid
and in New York, whose sessions overlap for only three and a half hours. Each
tick asks only for the symbols of the exchanges that are **inside their operating
window** at that instant: asking for a European stock at 22:00 CET does not give
an error, it gives the stale bar from the close, which is worse because it looks
like a datum.

The window is not the session. In the euro zone it runs 09:15 to 17:45 against a
session of 09:00 to 17:30: the first 15 minutes are let go, being the hangover of
the opening auction, and 15 minutes past the close are worked, because the last
bar does not appear when the bell rings. See `Market` in
`src/market_calendar.py`.

Configuration by environment:

    INGEST_ENABLED        false to switch it off without touching compose. Def. true
    INGEST_REFRESH_MIN    how often the universe is re-read, in minutes. Def. 5
    INGEST_KEEP_DAYS      days of 1m bars kept. Def. 90
    INGEST_BACKFILL_DAYS  days the daily backfill reviews. 0 switches it off. Def. 5
    INGEST_MAX_FAILURES   consecutive failures before shouting. Def. 5
    INGEST_THREADS        download in parallel. Def. false (see src/ingest.py)
    INGEST_OFFSET_SECONDS seconds after the minute rolls over. Def. 5
    DB_PATH               path to the database

About the clock: it wakes up a few seconds *after* the minute rolls over, not
right on it, because a one-minute bar is not available until that minute has
ended. Asking for it at second 0 returns the previous minute's, half-formed.

With every exchange closed nothing is requested: it sleeps until the next open
-the earliest of the ones being followed- in short slices, so `docker stop`
answers in seconds instead of waiting for the SIGKILL.

Before that nap it runs the daily maintenance, once a day: **gap backfill** and
**pruning**. In that order, and there and not at startup, because it is the only
moment when the session is already complete at Yahoo and asking for several days
at once does not compete with the minute's ticks.
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
from src.ingest import (  # noqa: E402
    BACKFILL_DIAS,
    YahooQuotes,
    backfill_gaps,
    ingest_once,
    load_last_timestamps,
)

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
    """Waits until `target`. Returns False if a stop signal arrives."""
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


def _describe_universe(universos: dict[str, list[str]]) -> str:
    """`eu: 89 (ABI.BR, ACS.MC...)  us: 3 (AAPL, MSFT, NVDA)`, para el log."""
    if not universos:
        return "ningun perfil activo con universo"
    partes = []
    for code, symbols in sorted(universos.items()):
        muestra = ", ".join(symbols[:5]) + ("..." if len(symbols) > 5 else "")
        partes.append(f"{code}: {len(symbols)} ({muestra})")
    return "  ".join(partes)


def podar(db: Database, keep_days: int) -> None:
    """Daily pruning of 1-minute bars.

    Without it the file grows forever: ~19,500 rows a day with 50 symbols. The
    daily bars are not lost -- they live in `bar_cache`, which is where the agent
    computes indicators from.
    """
    try:
        borradas = db.prune_bars_1m(keep_days=keep_days)
        if borradas:
            log.info("Poda: %d barras de mas de %d dias eliminadas.", borradas, keep_days)
    except DatabaseError as exc:
        log.warning("La poda fallo, se reintentara manana: %s", exc)


def rellenar(db: Database, provider: YahooQuotes, symbols: list[str], days: int) -> None:
    """Daily gap backfill (F2.10). It runs at the close, with the pruning.

    At the close and not at startup: that is when the session is already complete
    at Yahoo and when asking for 89 symbols over several days does not compete
    with the minute's ticks.

    What it fixes is the **whole lost session**. A gap within the session already
    heals by itself -- each tick asks for the complete day -- but if the process
    died on Friday afternoon, on Monday no tick ever looks back at Friday.
    """
    if days < 1 or not symbols:
        return
    # With 89 symbols that is ~4-5 minutes of downloading: without being able to
    # abandon, a `docker stop` at this hour would wait it all out and end in SIGKILL.
    resultado = backfill_gaps(
        db, provider, symbols, days=days, should_stop=lambda: _stopping
    )
    if resultado.interrumpido:
        log.info(
            "Relleno interrumpido al parar: %d simbolos revisados, %d barras "
            "recuperadas. Lo escrito se queda.",
            len(resultado.revisados), resultado.barras_escritas,
        )
        return
    if not resultado.ok:
        # A warning and not an error: tomorrow it tries again, and Yahoo's
        # 1-minute window is 30 days, so there is plenty of room to recover it.
        log.warning("Relleno de huecos fallido, se reintentara manana: %s",
                    resultado.error)
        return
    if not resultado.gaps:
        log.info(
            "Relleno: sin huecos en los ultimos %d dias (%d simbolos, %d ms).",
            resultado.dias, resultado.con_datos, resultado.latencia_ms,
        )
        return
    peores = sorted(resultado.gaps.items(), key=lambda kv: -kv[1])[:5]
    log.info(
        "Relleno: %d barras recuperadas en %d simbolos de los ultimos %d dias. "
        "Mayores huecos: %s",
        sum(resultado.gaps.values()), len(resultado.gaps), resultado.dias,
        ", ".join(f"{s} ({n})" for s, n in peores),
    )


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
    backfill_days = _get_int("INGEST_BACKFILL_DAYS", BACKFILL_DIAS)

    provider = YahooQuotes(threads=threads)

    log.info(
        "Ingestor en marcha. base=%s universo cada %d min, retencion %d dias, "
        "relleno %d dias, paralelo=%s",
        db_path, refresh_min, keep_days, backfill_days, threads,
    )
    for market in market_calendar.MARKETS.values():
        log.info("Mercado %s: %s", market.code, market_calendar.describe(market=market))

    universos: dict[str, list[str]] = {}
    symbols_edad = 10**9        # fuerza relectura en el primer tick
    fallos_seguidos = 0
    ultimo_mantenimiento: str | None = None

    with Database(path=db_path) as db:
        last_ts = load_last_timestamps(db)
        log.info("Historico previo: %d simbolos con barras de 1m.", len(last_ts))

        while not _stopping:
            if symbols_edad >= refresh_min:
                nuevos = db.active_universe_by_market()
                # A market code that is not in the registry is discarded with a
                # warning instead of blowing up: the schema's CHECK should prevent
                # it, but this is a daemon that runs for weeks and dying over one
                # odd row would leave the healthy profiles with no prices.
                desconocidos = set(nuevos) - set(market_calendar.MARKETS)
                for code in sorted(desconocidos):
                    log.error(
                        "Mercado %r desconocido: sus %d simbolos no se seguiran. "
                        "Revisa agent_settings.market.", code, len(nuevos[code]),
                    )
                    nuevos.pop(code)
                if nuevos != universos:
                    log.info("Universo: %s", _describe_universe(nuevos))
                universos = nuevos
                symbols_edad = 0

            if not universos:
                # With no active profiles there is nothing to follow. It is not an
                # error, and it does not sleep until any open: with no universe
                # there is no knowing which exchanges to watch either.
                if not sleep_until(next_tick(offset)):
                    break
                symbols_edad += 1
                continue

            # `is_operating`, not `is_session_open`: the window starts after the
            # open and ends after the close. The last minutes are the ones that
            # capture the final bar, which does not arrive at the instant the bell
            # rings.
            abiertos = [
                code for code in universos
                if market_calendar.is_operating(market=code)
            ]

            if not abiertos:
                # It takes advantage of having nothing to do for the daily
                # maintenance: first recover what is missing, then throw out the old.
                hoy = datetime.now(timezone.utc).date().isoformat()
                if ultimo_mantenimiento != hoy:
                    todos = sorted({s for ss in universos.values() for s in ss})
                    rellenar(db, provider, todos, backfill_days)
                    podar(db, keep_days)
                    ultimo_mantenimiento = hoy
                    # The backfill may have written bars later than what was
                    # there: without re-reading, tomorrow's first tick would take
                    # them for new and rewrite them whole.
                    last_ts = load_last_timestamps(db)

                apertura = min(
                    market_calendar.next_operating_open(market=code)
                    for code in universos
                ).astimezone(timezone.utc)
                log.info(
                    "Fuera de ventana en todas las bolsas (%s). Proximo arranque: "
                    "%s (en %.1f h).",
                    ", ".join(sorted(universos)),
                    apertura.isoformat(timespec="minutes"),
                    (apertura - datetime.now(timezone.utc)).total_seconds() / 3600,
                )
                if not sleep_until(apertura):
                    break
                # The universe is re-read on waking: profiles may have been
                # activated or paused in the meantime.
                symbols_edad = 10**9
                continue

            symbols = sorted({s for code in abiertos for s in universos[code]})

            resultado = ingest_once(db, provider, symbols, last_ts=last_ts)

            if resultado.ok:
                fallos_seguidos = 0
                log.info(
                    "[%s] %d/%d simbolos  %d barras  %d ms descarga  %d ms escritura",
                    "+".join(sorted(abiertos)),
                    resultado.con_datos, resultado.pedidos, resultado.barras_escritas,
                    resultado.latencia_descarga_ms, resultado.latencia_escritura_ms,
                )
                if resultado.empty:
                    log.warning(
                        "Sin datos: %s", ", ".join(resultado.empty[:10])
                        + ("..." if len(resultado.empty) > 10 else ""),
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
                        "revisa la red y si Yahoo esta limitando por IP. Palancas: "
                        "menos simbolos en el universo, o espaciar las peticiones "
                        "dentro del minuto.", max_failures,
                    )

            if not sleep_until(next_tick(offset)):
                break
            symbols_edad += 1

    log.info("Ingestor detenido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
