"""Minute-by-minute price ingestion.

The logic lives here; the loop that calls it every minute is in
`tools/ingestor.py`. They are separate so this can be tested without network and
without waiting a real minute per test case.

Three decisions worth understanding before touching anything:

  * **One endpoint per symbol.** yfinance does not download in batches: asking
    for 50 symbols is 50 HTTP requests to Yahoo. The spike measured it
    (`tools/spike_1m.py`): ~8s serial, ~1.5s in parallel. It fits comfortably
    inside the minute with 50 symbols, but it does not scale: at ~170 ms per
    symbol, some 200 would use the minute up. If the universe grows, it is time
    to parallelise or change source.

  * **The last bar is always rewritten.** The current minute's bar keeps changing
    while the market is open, so writing only what is new is not enough: the last
    one we already had has to be overwritten too. Hence the `>=` in
    `_bars_to_write` and the `insert or replace` in the database.

  * **A failure does not stop the ingestion.** A lost minute is a gap in the
    history, not a breakage: the next tick tries again. It only escalates to an
    error after several consecutive failures, which already suggests something
    systematic (sustained 429, network down, Yahoo changed).

  * **Which gaps heal by themselves and which one does not** (F2.10). Each tick
    asks for `period=1d`, that is, the whole session, and writes everything later
    than the last known bar. That means an outage *within* the session is filled
    in on the next tick by itself, however long it was. What does not heal is
    **the session that was lost whole**: if the process dies mid-afternoon and
    comes back the next day, `period=1d` no longer reaches yesterday's and the
    gap stays forever. Hence `backfill_gaps`, which runs once a day outside the
    window and asks for several days instead of one.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .db import Database
from .indicators import Bar

log = logging.getLogger(__name__)

# How many recent bars get rewritten even when we already had them. It covers the
# gap of one lost tick without rewriting the whole session every minute.
SOLAPE_BARRAS = 3

# Thresholds of the contention warning (F2.9).
#
# The first calibration was done on the Windows host —~1.1 ms/row with no
# competition— and the warning was set at 5 "to leave room for slower disks".
# That room never arrived: measured inside the container, which is where this
# really runs, a quiet write costs **~3.6-3.9 ms/row**, so any normal load
# brushed the threshold and with a busy machine went past it. A warning that
# fires during a healthy initial load is exactly the one that ends up ignored,
# which is what this measurement was written against.
#
# 15 leaves about 4 times the container's quiet cost. What it is meant to detect
# is a wait on `busy_timeout`, which is hundreds of ms per row, not a slow disk.
UMBRAL_ESCRITURA_MS = 1000
UMBRAL_MS_POR_FILA = 15.0

# Days the gap backfill asks for by default. It covers a whole long weekend,
# which is the realistic case: the computer switched off from Friday to Monday.
BACKFILL_DIAS = 5

# Hard cap per request. Yahoo serves at most 7 days of 1-minute bars in a single
# call (and only 30 days of history in total): asking for more does not error, it
# returns an empty frame, which is the worst way to fail. It is clamped here.
BACKFILL_DIAS_MAX = 7


class IngestError(RuntimeError):
    pass


@dataclass
class IngestResult:
    """What was measured in one tick. It is what ends up in `ingest_runs`."""

    pedidos: int = 0
    con_datos: int = 0
    empty: list[str] = field(default_factory=list)
    barras_escritas: int = 0
    latencia_descarga_ms: int = 0
    latencia_escritura_ms: int = 0
    rate_limited: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.con_datos > 0


@dataclass
class BackfillResult:
    """What was measured in one gap backfill. It also ends up in `ingest_runs`."""

    dias: int = 0
    pedidos: int = 0
    con_datos: int = 0
    barras_escritas: int = 0
    #: Symbols that were missing something, with how many bars. It is the datum
    #: worth reading: it says whether there was a gap and how big.
    gaps: dict[str, int] = field(default_factory=dict)
    #: Symbols already compared and written. With `interrupted`, it says how far
    #: it got; without it, it is the complete list.
    revisados: list[str] = field(default_factory=list)
    latencia_ms: int = 0
    rate_limited: bool = False
    #: It was abandoned between symbols by a stop signal. That is not a failure:
    #: what was done is written and tomorrow the same window is looked at again.
    interrumpido: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class QuoteProvider(Protocol):
    """Source of one-minute bars.

    It exists as an interface for one practical reason and one of design: the
    tests need a source without network, and changing provider if Yahoo starts
    rate-limiting by IP should touch nothing but this piece.

    `days` is the only thing separating a tick from a backfill: the tick asks for
    the current day and the backfill asks for several. It is a parameter and not a
    separate method so a new provider does not have two paths that can drift apart.
    """

    def fetch(self, symbols: list[str], *, days: int = 1) -> dict[str, list[Bar]]:
        ...


class YahooQuotes:
    """1-minute bars from Yahoo, via yfinance.

    `threads` defaults to False as in `src/market_data.py`: in parallel,
    yfinance's internal cache has given "database is locked" on Windows and
    returned empty symbols *without warning*. It is intermittent, so it is not
    switched on without having measured it in a real session (F2.1c).
    """

    def __init__(
        self, *, threads: bool = False, max_retries: int = 3,
        backoff_base: float = 2.0,
    ) -> None:
        try:
            import yfinance  # noqa: F401 - para fallar pronto y con mensaje claro
        except ImportError as exc:  # pragma: no cover
            raise IngestError(
                "Falta el paquete yfinance. Instalalo con: pip install yfinance"
            ) from exc
        self.threads = threads
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    def fetch(self, symbols: list[str], *, days: int = 1) -> dict[str, list[Bar]]:
        import yfinance as yf

        from .market_data import YahooMarketData

        ultimo_error: Exception | None = None
        period = f"{max(1, min(days, BACKFILL_DIAS_MAX))}d"

        for intento in range(1, self.max_retries + 1):
            try:
                frame = yf.download(
                    tickers=symbols,
                    period=period,
                    interval="1m",
                    auto_adjust=False,
                    progress=False,
                    group_by="ticker",
                    threads=self.threads,
                    actions=False,
                )
            except Exception as exc:  # noqa: BLE001 - yfinance lanza tipos variados
                ultimo_error = exc
                if not _es_rate_limit(exc) or intento == self.max_retries:
                    raise IngestError(f"Yahoo fallo: {exc}") from exc
                espera = self.backoff_base ** intento + random.uniform(0, 1)
                log.warning(
                    "Yahoo limito el ritmo (intento %d/%d). Reintento en %.1fs.",
                    intento, self.max_retries, espera,
                )
                time.sleep(espera)
                continue

            if frame is None or getattr(frame, "empty", True):
                return {}

            output: dict[str, list[Bar]] = {}
            for symbol in symbols:
                bars = YahooMarketData._extract_bars(
                    frame, symbol, single=len(symbols) == 1
                )
                if bars:
                    output[symbol] = bars
            return output

        raise IngestError(f"Yahoo fallo tras {self.max_retries} intentos: {ultimo_error}")


def _es_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


def _utc(moment: datetime) -> datetime:
    """Everything is stored in UTC. yfinance returns these marks in New York
    time, and mixing zones in the database would be a silent source of gaps."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _bars_to_write(bars: list[Bar], last_ts: str | None) -> list[Bar]:
    """Bars that have to be written for this symbol.

    The last one we already had is included, not just the later ones: while the
    minute is in progress its bar keeps changing, so keeping the first version
    would freeze a close that was not the close yet.
    """
    if last_ts is None:
        return bars

    recientes = [b for b in bars if _utc(b.timestamp).isoformat() >= last_ts]
    if recientes:
        return recientes
    # Nothing new: a short overlap is refreshed in case an earlier tick wrote a
    # half-formed bar.
    return bars[-SOLAPE_BARRAS:]


def load_last_timestamps(db: Database) -> dict[str, str]:
    """Last known bar of each symbol, so as not to rewrite the whole session.

    It is queried once at startup and kept in memory: doing it on every tick
    would be a `group by` over hundreds of thousands of rows every minute.
    """
    return {
        row["symbol"]: row["ultimo"]
        for row in db.query("select symbol, max(ts) as ultimo from bars_1m group by symbol")
    }


def _prev_close(db: Database, symbol: str) -> float | None:
    """Previous session's close, if `bar_cache` has it.

    It comes from there and not from a separate request because one request per
    symbol and minute just for this would double the load against Yahoo. If the
    agent has not run yet, `bar_cache` will be empty and this returns None: the
    day's percentage then falls back to the open as its reference, which always
    exists.
    """
    rows = db.query(
        "select close from bar_cache where symbol = ? and interval = '1d' "
        "order by ts desc limit 1",
        (symbol,),
    )
    return float(rows[0]["close"]) if rows else None


def ingest_once(
    db: Database,
    provider: QuoteProvider,
    symbols: list[str],
    *,
    last_ts: dict[str, str] | None = None,
) -> IngestResult:
    """A complete tick: download, write and record. It never raises.

    Never raising is deliberate: it is invoked by a loop that must stay alive. A
    failed tick is noted in `ingest_runs` and the next minute retries it.
    """
    result = IngestResult(pedidos=len(symbols))
    if not symbols:
        return result

    last_ts = last_ts if last_ts is not None else {}
    run_id = db.start_ingest_run(symbols_requested=len(symbols))

    t0 = time.monotonic()
    try:
        por_simbolo = provider.fetch(symbols)
    except Exception as exc:  # noqa: BLE001 - the loop cannot die from this
        result.error = str(exc)
        result.rate_limited = _es_rate_limit(exc)
        result.latencia_descarga_ms = int((time.monotonic() - t0) * 1000)
        db.finish_ingest_run(
            run_id, symbols_ok=0, symbols_failed=len(symbols),
            latency_ms=result.latencia_descarga_ms,
            rate_limited=result.rate_limited, error=result.error[:500],
        )
        log.error("Tick fallido: %s", exc)
        return result

    result.latencia_descarga_ms = int((time.monotonic() - t0) * 1000)
    result.empty = sorted(set(symbols) - set(por_simbolo))
    result.con_datos = len(por_simbolo)

    filas_barras: list[dict] = []
    filas_quotes: list[dict] = []

    for symbol, bars in por_simbolo.items():
        pendientes = _bars_to_write(bars, last_ts.get(symbol))
        for bar in pendientes:
            filas_barras.append({
                "symbol": symbol,
                "ts": _utc(bar.timestamp).isoformat(),
                "open": bar.open, "high": bar.high, "low": bar.low,
                "close": bar.close, "volume": bar.volume,
            })

        ultima = bars[-1]
        referencia = _prev_close(db, symbol) or bars[0].open
        filas_quotes.append({
            "symbol": symbol,
            "price": ultima.close,
            "prev_close": referencia,
            "change_pct": (
                round((ultima.close / referencia - 1) * 100, 4) if referencia else None
            ),
            "volume": ultima.volume,
            "as_of": _utc(ultima.timestamp).isoformat(),
        })
        last_ts[symbol] = _utc(ultima.timestamp).isoformat()

    t1 = time.monotonic()
    try:
        db.upsert_bars_1m(filas_barras)
        db.upsert_quotes(filas_quotes)
    except Exception as exc:  # noqa: BLE001
        result.error = f"Fallo al escribir: {exc}"
        log.error("%s", result.error)
    result.latencia_escritura_ms = int((time.monotonic() - t1) * 1000)
    result.barras_escritas = len(filas_barras)

    # Contention with the agent's cycle (F2.9). It is measured by cost *per row*,
    # not by total time: the first tick writes the whole session (~1,950 rows with
    # 5 symbols) and takes seconds without anyone blocking it. What gives away a
    # wait on `busy_timeout` is taking a long time for few rows.
    ms_por_fila = (
        result.latencia_escritura_ms / result.barras_escritas
        if result.barras_escritas else result.latencia_escritura_ms
    )
    if result.latencia_escritura_ms > UMBRAL_ESCRITURA_MS and ms_por_fila > UMBRAL_MS_POR_FILA:
        log.warning(
            "Escritura lenta: %d ms para %d barras (%.1f ms/fila). Probable "
            "contencion con un ciclo en marcha.",
            result.latencia_escritura_ms, result.barras_escritas, ms_por_fila,
        )

    db.finish_ingest_run(
        run_id,
        symbols_ok=result.con_datos,
        symbols_failed=len(result.empty),
        latency_ms=result.latencia_descarga_ms + result.latencia_escritura_ms,
        rate_limited=result.rate_limited,
        error=result.error[:500] if result.error else None,
    )
    return result


def backfill_gaps(
    db: Database,
    provider: QuoteProvider,
    symbols: list[str],
    *,
    days: int = BACKFILL_DIAS,
    should_stop: Callable[[], bool] | None = None,
) -> BackfillResult:
    """Fills in the bars missing from the last `days` days. It never raises.

    It is the half F2.10 was missing. The other -- the pruning -- lives in the loop.

    **What it fixes that the tick does not.** A tick asks for the current day, so
    an outage within the session recovers by itself the next minute. What does not
    recover is a whole lost session: with the process stopped from Friday to
    Monday, on Monday `period=1d` only brings Monday and Friday stays empty
    forever, because nothing will ever look back. This looks back.

    **It writes only what is missing, not what it fetched.** Rewriting five whole
    days every afternoon would be ~225,000 rows with the European universe, and
    with `insert or replace` it would not fail: it would show up only as a task
    taking longer and longer. Comparing against what is already there costs one
    query per symbol over an index, and along the way gives the only figure worth
    reading -- how many bars were missing.

    **And it writes symbol by symbol, not everything at the end.** Measured on
    2026-08-08 against Yahoo: 3 symbols over 5 days are 7,635 bars and 9 s, that
    is about 3 s per symbol, ~4-5 minutes with the 89 European ones. That is too
    much for a single transaction: it coincides with the agent's cycle time (18:00
    in Madrid is "outside the window" for both) and a long write there is exactly
    the contention R3 watches. In batches, besides, a stop halfway leaves what it
    had done.

    `should_stop` allows abandoning between symbols. Without it, a `docker stop`
    at maintenance time would wait out the minutes the download takes and end in
    SIGKILL.
    """
    result = BackfillResult(dias=max(1, min(days, BACKFILL_DIAS_MAX)))
    if not symbols or days < 1:
        return result

    result.pedidos = len(symbols)
    run_id = db.start_ingest_run(symbols_requested=len(symbols), kind="backfill")
    # One day of margin over the requested window: the comparison is made against
    # what is in the database from this cut-off, and falling short would make the
    # batch's oldest bars look new every afternoon.
    desde = (
        datetime.now(timezone.utc) - timedelta(days=result.dias + 1)
    ).isoformat()

    t0 = time.monotonic()
    try:
        por_simbolo = provider.fetch(symbols, days=result.dias)
    except Exception as exc:  # noqa: BLE001 - the loop cannot die from this
        result.error = str(exc)
        result.rate_limited = _es_rate_limit(exc)
        result.latencia_ms = int((time.monotonic() - t0) * 1000)
        db.finish_ingest_run(
            run_id, symbols_ok=0, symbols_failed=len(symbols),
            latency_ms=result.latencia_ms, rate_limited=result.rate_limited,
            error=result.error[:500],
        )
        log.error("Relleno de huecos fallido: %s", exc)
        return result

    result.con_datos = len(por_simbolo)

    for symbol, bars in sorted(por_simbolo.items()):
        if should_stop is not None and should_stop():
            result.interrumpido = True
            # It stays written in `ingest_runs` even though it is not a breakage:
            # a half pass recorded as complete would suggest the window had
            # already been reviewed in full.
            result.error = (
                f"interrumpido por senal de parada tras "
                f"{len(result.revisados)}/{result.con_datos} simbolos"
            )
            break

        conocidas = db.bars_1m_timestamps(symbol, since=desde)
        rows: list[dict] = []
        faltan = 0
        for indice, bar in enumerate(bars):
            ts = _utc(bar.timestamp).isoformat()
            if ts < desde:
                continue
            nueva = ts not in conocidas
            # The last bar is rewritten even when we already had it, for the same
            # reason the tick rewrites its own: the version we stored may have
            # been captured with the minute half done. In the United States, with
            # the operating window flush against the close (drain=0), that
            # half-done version is precisely the session close, which is the bar
            # that gets looked at most.
            ultima = indice == len(bars) - 1
            if not nueva and not ultima:
                continue
            # Only what was genuinely missing counts as a gap: if refreshing the
            # last bar entered the tally, every symbol would have "1 gap" every
            # afternoon and the figure would stop meaning anything.
            faltan += int(nueva)
            rows.append({
                "symbol": symbol,
                "ts": ts,
                "open": bar.open, "high": bar.high, "low": bar.low,
                "close": bar.close, "volume": bar.volume,
            })

        try:
            db.upsert_bars_1m(rows)
        except Exception as exc:  # noqa: BLE001 - un simbolo no tumba el resto
            result.error = f"Fallo al escribir {symbol}: {exc}"
            log.error("%s", result.error)
            continue

        result.revisados.append(symbol)
        result.barras_escritas += len(rows)
        if faltan:
            result.gaps[symbol] = faltan

    result.latencia_ms = int((time.monotonic() - t0) * 1000)
    # `symbols_ok` are the ones reviewed, not the ones the provider returned: if
    # it was abandoned halfway, counting what arrived would pass a partial run off
    # as a complete one.
    db.finish_ingest_run(
        run_id,
        symbols_ok=len(result.revisados),
        symbols_failed=len(symbols) - len(result.revisados),
        latency_ms=result.latencia_ms,
        rate_limited=result.rate_limited,
        error=result.error[:500] if result.error else None,
    )
    return result
