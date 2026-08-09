"""Fetching bars and indicators. Two interchangeable providers.

  * `UniverseMarketData` (recommended for 500 assets) refreshes the bar cache,
    runs the universe through the screener and returns only the best candidates.
    It is the funnel that makes a large universe viable.
  * `YahooMarketData` downloads the watchlist directly. No account, no key.

**Shared contract:** `fetch_snapshots(must_include)` returns the snapshots of
everything that has to be analysed. `must_include` are the mandatory symbols —the
open positions, which need reviewing even when the screener does not select
them— and each provider adds its own.

The separation of prices (decision / execution / valuation) is explained in
`MarketSnapshot`. The rule, here and in the cache: **the last bar is never used to
decide**, because it may be half-formed if the market is still open; its open is
used as the execution price.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from .indicators import Bar, compute_snapshot
from .models import MarketSnapshot

log = logging.getLogger(__name__)

# Minimum bars for the long indicators (SMA200) to mean anything. Below this we
# still analyse, but the snapshot will carry null keys.
MIN_BARS = 60


class MarketDataError(RuntimeError):
    pass


class MarketDataProvider(Protocol):
    def fetch_snapshots(
        self, must_include: tuple[str, ...] | list[str] = ()
    ) -> dict[str, MarketSnapshot]:
        ...


# ----------------------------------------------------------------------
# Construccion del snapshot, comun a los dos proveedores
# ----------------------------------------------------------------------

def build_snapshot(symbol: str, bars: list[Bar]) -> MarketSnapshot | None:
    """Assembles the snapshot, separating the decision bar from the execution one.

    `bars` must arrive ordered oldest to newest. The last bar is reserved for the
    execution price; the indicators are computed over everything before it.
    """
    if len(bars) < 2:
        log.warning("%s: hacen falta al menos 2 barras; se omite.", symbol)
        return None

    decision_bars = bars[:-1]
    execution_bar = bars[-1]

    if len(decision_bars) < MIN_BARS:
        log.warning(
            "%s tiene %d barras utilizables (minimo %d); se omite del analisis.",
            symbol, len(decision_bars), MIN_BARS,
        )
        return None

    indicators = compute_snapshot(decision_bars)
    decision_bar = decision_bars[-1]

    return MarketSnapshot(
        symbol=symbol,
        as_of=decision_bar.timestamp,
        price=decision_bar.close,
        indicators=indicators,
        recent_bars=[_bar_to_dict(b) for b in decision_bars[-10:]],
        fill_price=execution_bar.open,
        mark_price=execution_bar.close,
        fill_basis="next_open",
        session=execution_bar.timestamp.strftime("%Y-%m-%d"),
    )


def _bar_to_dict(bar: Bar) -> dict[str, object]:
    # With hourly bars the date alone does not tell one bar from another, so the
    # time is included. With daily bars it would be noise in the prompt.
    intraday = (bar.timestamp.hour, bar.timestamp.minute) != (0, 0)
    return {
        "date": bar.timestamp.strftime("%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"),
        "open": round(bar.open, 4),
        "high": round(bar.high, 4),
        "low": round(bar.low, 4),
        "close": round(bar.close, 4),
        "volume": bar.volume,
    }


# ----------------------------------------------------------------------
# Yahoo Finance (yfinance)
# ----------------------------------------------------------------------

class YahooMarketData:
    """Daily bars from Yahoo, with no account and no key.

    Honest warnings about this source:

      * yfinance is an unofficial client. Yahoo changes its endpoints every so
        often and then the package has to be updated.
      * It downloads with `threads=False` on purpose: in parallel, yfinance's
        internal cache (a SQLite of its own) gives "database is locked" on
        Windows and returns empty symbols without warning.
      * Prices do not come adjusted for splits or dividends
        (`auto_adjust=False`), which is the right thing here: we want the price
        trading would have happened at, not the series adjusted after the fact.
    """

    def __init__(
        self,
        *,
        watchlist: tuple[str, ...] | list[str],
        lookback_days: int = 200,
        interval: str = "1d",
    ) -> None:
        try:
            import yfinance  # noqa: F401 - solo para fallar pronto y claro
        except ImportError as exc:  # pragma: no cover
            raise MarketDataError(
                "Falta el paquete yfinance. Instalalo con: pip install yfinance"
            ) from exc
        self.watchlist = tuple(watchlist)
        self.lookback_days = lookback_days
        self.interval = interval

    def fetch_snapshots(
        self, must_include: tuple[str, ...] | list[str] = ()
    ) -> dict[str, MarketSnapshot]:
        symbols = sorted(set(self.watchlist) | set(must_include))
        if not symbols:
            return {}

        import yfinance as yf

        lookback_days = self.lookback_days
        # Margen amplio: `lookback_days` son sesiones, no dias naturales.
        period_days = int(lookback_days * 1.8) + 40

        try:
            frame = yf.download(
                tickers=symbols,
                start=(datetime.now(timezone.utc) - timedelta(days=period_days)).date(),
                interval=self.interval,
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=False,
                actions=False,
            )
        except Exception as exc:  # noqa: BLE001 - yfinance lanza tipos variados
            raise MarketDataError(f"No se pudieron descargar las barras de Yahoo: {exc}") from exc

        if frame is None or frame.empty:
            raise MarketDataError(
                "Yahoo no devolvio ninguna barra. Comprueba la conexion y que los "
                f"simbolos existan: {', '.join(symbols)}"
            )

        snapshots: dict[str, MarketSnapshot] = {}
        for symbol in symbols:
            bars = self._extract_bars(frame, symbol, single=len(symbols) == 1)
            if not bars:
                log.warning("%s: Yahoo no devolvio datos utilizables; se omite.", symbol)
                continue
            snapshot = build_snapshot(symbol, bars)
            if snapshot is not None:
                snapshots[symbol] = snapshot

        log.info(
            "Datos de Yahoo listos para %d/%d simbolos.", len(snapshots), len(symbols)
        )
        return snapshots

    @staticmethod
    def _extract_bars(frame: Any, symbol: str, *, single: bool = False) -> list[Bar]:
        """Pulls a symbol's bars out, whether the DataFrame is flat or multi-index.

        It is not decided by the number of tickers requested: yfinance returns one
        shape or the other depending on version and symbol count, and guessing
        made the extraction return zero bars without warning. Both are tried.
        """
        required = ("Open", "High", "Low", "Close", "Volume")

        options = []
        try:
            options.append(frame[symbol])
        except Exception:  # noqa: BLE001 - KeyError, TypeError o lo que traiga pandas
            pass
        options.append(frame)

        sub = None
        for option in options:
            columns = getattr(option, "columns", None)
            if columns is not None and all(column in columns for column in required):
                sub = option
                break

        if sub is None or getattr(sub, "empty", True):
            return []

        # A row with a null Close is a session with no data (a holiday, or the
        # symbol was not listed yet): it is discarded instead of dragging gaps along.
        sub = sub.dropna(subset=["Open", "Close"])

        bars: list[Bar] = []
        for timestamp, row in sub.iterrows():
            try:
                close = float(row["Close"])
                open_price = float(row["Open"])
            except (TypeError, ValueError):
                continue
            if close <= 0 or open_price <= 0:
                continue

            moment = timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)

            volume = row["Volume"]
            bars.append(
                Bar(
                    timestamp=moment,
                    open=open_price,
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=close,
                    volume=float(volume) if volume == volume else 0.0,
                )
            )

        bars.sort(key=lambda b: b.timestamp)
        return bars


# ----------------------------------------------------------------------

def build_market_data(settings, database=None) -> MarketDataProvider:
    """Picks the provider according to the configuration.

    With a universe set, the funnel is used, which needs the database for the bar
    cache. Without a universe, the watchlist is downloaded as-is.
    """
    if settings.screener.enabled:
        if database is None:
            raise MarketDataError(
                "El embudo por universo necesita la base de datos para la cache "
                "de barras."
            )
        from .universe_data import UniverseMarketData

        return UniverseMarketData(
            database=database,
            screener=settings.screener,
            interval=settings.bar_interval,
            lookback_days=settings.lookback_days,
        )

    return YahooMarketData(
        watchlist=settings.watchlist,
        lookback_days=settings.lookback_days,
        interval=settings.bar_interval,
    )
