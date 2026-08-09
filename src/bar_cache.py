"""Bar cache in SQLite, with incremental refresh.

This is what makes analysing 500 assets viable: the first run downloads the whole
history (a few minutes), and from then on each cycle only asks Yahoo for the new
bars. Without it, 500 symbols x 275 bars on every cycle would end in an HTTP 429
and a temporary IP ban.

Three decisions worth knowing about:

  * **The refresh is idempotent.** `insert or replace` on the key
    (symbol, interval, ts) updates the bar instead of duplicating it. It matters
    because the last bar changes while the market is open: every cycle rewrites
    it with the most recent price.
  * **Downloads are batched.** Yahoo accepts several tickers per request; they
    are chunked into groups with a rest in between. With `threads=False`, which
    avoids yfinance's internal cache locking up on Windows.
  * **Failures are counted, not hidden.** A symbol Yahoo stops recognising
    (merger, index removal) accumulates failures in `bar_cache_state`, and past a
    threshold it stops being requested so as not to spend requests on nothing.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone

from .db import Database
from .indicators import Bar

log = logging.getLogger(__name__)

# Tickers per request to Yahoo. Higher is faster but raises the risk of a 429 and
# makes a single failure take down more symbols at once.
BATCH_SIZE = 60
# Pause between batches, in seconds.
BATCH_PAUSE = 1.0
# Consecutive failures after which a symbol stops being requested.
MAX_FAILURES = 5

# Yahoo caps intraday history. These are its limits, not ours.
MAX_DAYS_BY_INTERVAL = {"1d": 3650, "1h": 700}


class BarCacheError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BarCache:
    def __init__(self, db: Database, *, interval: str = "1d") -> None:
        if interval not in MAX_DAYS_BY_INTERVAL:
            raise BarCacheError(
                f"Intervalo no soportado: {interval!r}. Usa 1d o 1h."
            )
        self.db = db
        self.interval = interval

    # -- Reading -----------------------------------------------------------

    def get_bars(self, symbol: str, *, limit: int = 400) -> list[Bar]:
        """A symbol's last `limit` bars, oldest to newest."""
        rows = self.db.query(
            "select ts, open, high, low, close, volume from bar_cache "
            "where symbol = ? and interval = ? order by ts desc limit ?",
            (symbol, self.interval, limit),
        )
        bars = [
            Bar(
                timestamp=datetime.fromisoformat(row["ts"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for row in reversed(rows)
        ]
        return bars

    def coverage(self) -> dict[str, int]:
        """How many bars there are per symbol. For diagnostics."""
        return {
            row["symbol"]: int(row["bars"])
            for row in self.db.query(
                "select symbol, bars from bar_cache_state where interval = ?",
                (self.interval,),
            )
        }

    def stats(self) -> dict[str, int]:
        row = self.db.query(
            "select count(1) as symbols, coalesce(sum(bars), 0) as bars, "
            "       coalesce(sum(case when failures >= ? then 1 else 0 end), 0) as stale "
            "from bar_cache_state where interval = ?",
            (MAX_FAILURES, self.interval),
        )[0]
        return {
            "symbols": int(row["symbols"]),
            "bars": int(row["bars"]),
            "stale": int(row["stale"]),
        }

    # -- Refresh -----------------------------------------------------------

    def refresh(
        self,
        symbols: list[str],
        *,
        lookback_days: int = 400,
        force_full: bool = False,
    ) -> dict[str, int]:
        """Downloads the missing bars. Returns a summary of the work done.

        Each symbol is requested from its last known bar; the ones holding
        nothing are requested in full. Those sharing a start date are grouped
        into the same request.
        """
        import yfinance as yf

        state = {
            (row["symbol"]): row
            for row in self.db.query(
                "select symbol, last_ts, failures from bar_cache_state where interval = ?",
                (self.interval,),
            )
        }

        max_days = MAX_DAYS_BY_INTERVAL[self.interval]
        full_start = (
            datetime.now(timezone.utc) - timedelta(days=min(lookback_days, max_days))
        ).date()

        # Grouped by start date so batches can be requested.
        groups: dict[object, list[str]] = {}
        skipped = 0
        for symbol in symbols:
            row = state.get(symbol)
            if row and int(row["failures"] or 0) >= MAX_FAILURES and not force_full:
                skipped += 1
                continue

            if force_full or not row or not row["last_ts"]:
                start = full_start
            else:
                last = datetime.fromisoformat(row["last_ts"])
                # A margin is subtracted so the last bar (possibly incomplete) is
                # requested again and rewritten with fresh data.
                margin = timedelta(days=4) if self.interval == "1d" else timedelta(days=2)
                start = (last - margin).date()
                if start < full_start:
                    start = full_start

            groups.setdefault(start, []).append(symbol)

        summary = {"requests": 0, "symbols": 0, "bars": 0, "failures": 0,
                   "skipped": skipped}

        for start, group in sorted(groups.items()):
            for batch in _chunks(group, BATCH_SIZE):
                summary["requests"] += 1
                inserted, failed = self._fetch_batch(yf, batch, start)
                summary["bars"] += inserted
                summary["failures"] += failed
                summary["symbols"] += len(batch) - failed
                if BATCH_PAUSE:
                    _time.sleep(BATCH_PAUSE)

        log.info(
            "Cache %s: %d peticiones, %d barras nuevas, %d simbolos con fallo, "
            "%d omitidos por fallos repetidos.",
            self.interval, summary["requests"], summary["bars"],
            summary["failures"], summary["skipped"],
        )
        return summary

    def _fetch_batch(self, yf, symbols: list[str], start) -> tuple[int, int]:
        try:
            frame = yf.download(
                tickers=symbols,
                start=start,
                interval=self.interval,
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=False,
                actions=False,
            )
        except Exception as exc:  # noqa: BLE001 - yfinance raises assorted types
            log.warning("Lote de %d simbolos fallo: %s", len(symbols), exc)
            for symbol in symbols:
                self._record_failure(symbol, str(exc))
            return 0, len(symbols)

        inserted = 0
        failed = 0
        single = len(symbols) == 1

        for symbol in symbols:
            rows = self._extract(frame, symbol, single=single)
            if not rows:
                failed += 1
                self._record_failure(symbol, "sin barras utilizables")
                continue
            inserted += self._store(symbol, rows)
            self._record_success(symbol)

        return inserted, failed

    @staticmethod
    def _extract(frame, symbol: str, *, single: bool = False) -> list[tuple]:
        """Pulls a symbol's bars out, whether the DataFrame is flat or multi-index.

        It is not decided by the number of tickers requested: yfinance returns a
        flat or a multi-index DataFrame depending on version and symbol count, and
        guessing right made the extraction silently return zero bars. Both shapes
        are tried and the one carrying the OHLCV columns is accepted.
        """
        required = ("Open", "High", "Low", "Close", "Volume")

        options = []
        try:
            options.append(frame[symbol])
        except Exception:  # noqa: BLE001 - KeyError, TypeError or whatever pandas brings
            pass
        options.append(frame)

        sub = None
        for option in options:
            columns = getattr(option, "columns", None)
            if columns is None:
                continue
            if all(column in columns for column in required):
                sub = option
                break

        if sub is None or getattr(sub, "empty", True):
            return []

        sub = sub.dropna(subset=["Open", "Close"])
        rows: list[tuple] = []
        for timestamp, row in sub.iterrows():
            try:
                open_price = float(row["Open"])
                close = float(row["Close"])
                high = float(row["High"])
                low = float(row["Low"])
            except (TypeError, ValueError):
                continue
            if open_price <= 0 or close <= 0:
                continue

            moment = (
                timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime")
                else timestamp
            )
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            else:
                moment = moment.astimezone(timezone.utc)

            volume = row["Volume"]
            rows.append((
                moment.isoformat(), open_price, high, low, close,
                float(volume) if volume == volume else 0.0,
            ))
        return rows

    def _store(self, symbol: str, rows: list[tuple]) -> int:
        for ts, open_price, high, low, close, volume in rows:
            # `insert or replace`: the last bar is rewritten on every cycle while
            # the market is open.
            self.db.execute(
                "insert or replace into bar_cache "
                "(symbol, interval, ts, open, high, low, close, volume) "
                "values (?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, self.interval, ts, open_price, high, low, close, volume),
            )
        return len(rows)

    def _record_success(self, symbol: str) -> None:
        row = self.db.query(
            "select max(ts) as last_ts, count(1) as bars from bar_cache "
            "where symbol = ? and interval = ?",
            (symbol, self.interval),
        )[0]
        self.db.execute(
            "insert or replace into bar_cache_state "
            "(symbol, interval, last_ts, last_refresh, bars, failures, last_error) "
            "values (?, ?, ?, ?, ?, 0, null)",
            (symbol, self.interval, row["last_ts"], _now(), int(row["bars"] or 0)),
        )

    def _record_failure(self, symbol: str, error: str) -> None:
        previous = self.db.query(
            "select failures, last_ts, bars from bar_cache_state "
            "where symbol = ? and interval = ?",
            (symbol, self.interval),
        )
        failures = int(previous[0]["failures"] or 0) + 1 if previous else 1
        last_ts = previous[0]["last_ts"] if previous else None
        bars = int(previous[0]["bars"] or 0) if previous else 0

        self.db.execute(
            "insert or replace into bar_cache_state "
            "(symbol, interval, last_ts, last_refresh, bars, failures, last_error) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (symbol, self.interval, last_ts, _now(), bars, failures, error[:500]),
        )
        if failures == MAX_FAILURES:
            log.warning(
                "%s acumula %d fallos seguidos; se deja de pedir. Puede que Yahoo ya "
                "no lo reconozca (fusion, exclusion del indice o cambio de ticker).",
                symbol, failures,
            )


def _chunks(items: list[str], size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]
