#!/usr/bin/env python
"""Ingestor de precios: un tick por minuto mientras haya alguna bolsa abierta.

Es el proceso principal del contenedor `ingestor`. La logica vive en
`src/ingest.py`; aqui solo esta el bucle, el reloj y el apagado limpio.

**Sigue varias bolsas a la vez.** Desde que el mercado es un parametro del perfil
(`agent_settings.market`), dos perfiles activos pueden operar en Madrid y en
Nueva York, cuyas sesiones se solapan solo tres horas y media. Cada tick pide
unicamente los simbolos de las bolsas que estan **dentro de su ventana
operativa** en ese instante: pedir un valor europeo a las 22:00 CET no da un
error, da la barra rancia del cierre, que es peor porque parece un dato.

La ventana no es la sesion. En la zona euro va de 09:15 a 17:45 frente a una
sesion de 09:00 a 17:30: se dejan pasar los 15 primeros minutos, que son la
resaca de la subasta de apertura, y se trabajan 15 despues del cierre, porque la
ultima barra no aparece cuando suena la campana. Ver `Market` en
`src/market_calendar.py`.

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

Con todas las bolsas cerradas no se pide nada: se duerme hasta la proxima
apertura -la mas temprana de las que se siguen- en tramos cortos, para que
`docker stop` responda en segundos en vez de esperar al SIGKILL.
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
    for mercado in market_calendar.MARKETS.values():
        log.info("Mercado %s: %s", mercado.code, market_calendar.describe(market=mercado))

    universos: dict[str, list[str]] = {}
    symbols_edad = 10**9        # fuerza relectura en el primer tick
    fallos_seguidos = 0
    ultima_poda: str | None = None

    with Database(path=db_path) as db:
        last_ts = load_last_timestamps(db)
        log.info("Historico previo: %d simbolos con barras de 1m.", len(last_ts))

        while not _stopping:
            if symbols_edad >= refresh_min:
                nuevos = db.active_universe_by_market()
                # Un codigo de mercado que no este en el registro se descarta con
                # un aviso en lugar de reventar: el CHECK del esquema deberia
                # impedirlo, pero esto es un demonio que corre semanas y morir
                # por una fila rara dejaria sin precios a los perfiles sanos.
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
                # Sin perfiles activos no hay nada que seguir. No es un error, y
                # no se duerme hasta ninguna apertura: sin universo tampoco se
                # sabe que bolsas mirar.
                if not sleep_until(next_tick(offset)):
                    break
                symbols_edad += 1
                continue

            # `is_operating`, no `is_session_open`: la ventana empieza despues de
            # la apertura y termina despues del cierre. Los ultimos minutos son
            # los que capturan la barra final, que no llega en el instante en que
            # suena la campana.
            abiertos = [
                code for code in universos
                if market_calendar.is_operating(market=code)
            ]

            if not abiertos:
                # Aprovecha que no hay nada que hacer para podar, una vez al dia.
                hoy = datetime.now(timezone.utc).date().isoformat()
                if ultima_poda != hoy:
                    podar(db, keep_days)
                    ultima_poda = hoy

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
                # Se relee el universo al despertar: entre medias han podido
                # activarse o pausarse perfiles.
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
