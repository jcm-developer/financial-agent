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

⚠️ **There is a second separation since F9.14, and it is the one that is easy to
miss: the price clock is not the indicator clock.** The profile's `bar_interval`
decides how often there is a new price to decide and execute on; the indicators
the analyst reads are **always daily** (`INDICATOR_INTERVAL`). They used to be the
same series, and with `bar_interval=1h` that meant `atr_14` measured 14 *hours* —
four times smaller than the 14 days the risk table of F6.5 was calibrated on, so
the stop landed at −0,6 % for a horizon the analyst itself declared as 7 to 14
days. Measured in F9.15: ratio 4,08x median, and `volatility_20d_pct` was out by
another 4,19x for the same reason.
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

#: Interval the analyst's indicators are always computed on (F9.14).
#:
#: **It is a constant and not a profile column on purpose.** The indicators carry
#: their unit in the name —`return_60d_pct`, `high_52w`, `volatility_20d_pct`— and
#: the whole risk table of F6.5 is calibrated in days, so an indicator interval
#: that could be anything else is an invitation to recalibrate five things at once
#: and forget one. Making it a parameter was option (b) of F9.14 and was
#: discarded: it fixes the stop and leaves the volatility lying by 4,19x.
#:
#: What stays configurable is `bar_interval`, which is now the **price and
#: execution** clock: eight hourly cycles a day still check every stop and target
#: eight times, which is what they were for.
INDICATOR_INTERVAL = "1d"


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

def build_snapshot(
    symbol: str,
    bars: list[Bar],
    *,
    indicator_bars: list[Bar] | None = None,
) -> MarketSnapshot | None:
    """Assembles the snapshot from its two series, both oldest to newest.

    @param bars: The **price** series, in the profile's `bar_interval`. Its last
        bar is reserved for the execution price and the one before it is what the
        decision is taken at.
    @param indicator_bars: The **daily** series the indicators are computed on
        (`INDICATOR_INTERVAL`). Its last bar is reserved too —the session in
        progress is half-formed— and the ten shown to the model come from here.
        `None` means "the same series as the price", which is what the daily
        profiles and the no-universe provider get.

    ⚠️ **`indicators["price"]` is then the last daily close and `snapshot.price` is
    the current one, and they are deliberately both kept.** The bundle is
    internally consistent —every band, mean and distance in it refers to that
    daily close— so overwriting its price with the intraday one would make the
    numbers disagree with each other instead of with the reference. What the model
    gets is a daily chart plus where the price stands against it right now, and the
    prompt spells the gap out (`analyst.py`) rather than leaving two figures called
    "price" for it to reconcile.

    The 60-bar floor applies to the indicator series, which is what it was always
    about: the long indicators are what stop meaning anything. The price series
    only needs the two bars that make a decision and an execution.
    """
    if len(bars) < 2:
        log.warning("%s: hacen falta al menos 2 barras de precio; se omite.", symbol)
        return None

    decision_bar = bars[-2]
    execution_bar = bars[-1]

    context = bars if indicator_bars is None else indicator_bars
    if len(context) < 2:
        log.warning(
            "%s: hacen falta al menos 2 barras de indicadores; se omite.", symbol
        )
        return None

    context_bars = context[:-1]
    if len(context_bars) < MIN_BARS:
        log.warning(
            "%s tiene %d barras utilizables (minimo %d); se omite del analisis.",
            symbol, len(context_bars), MIN_BARS,
        )
        return None

    return MarketSnapshot(
        symbol=symbol,
        as_of=decision_bar.timestamp,
        price=decision_bar.close,
        indicators=compute_snapshot(context_bars),
        recent_bars=[_bar_to_dict(b) for b in context_bars[-10:]],
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
    """Bars from Yahoo for a plain watchlist, with no account and no key.

    Used only by a profile with **no universe file**, so it has no bar cache and
    no screener: it downloads the watchlist as it is on every cycle.

    With an intraday `interval` it makes **two** downloads, and that is F9.14's
    rule showing up here too: one for the price series and one for the daily series
    the indicators are computed on. It could have been left reading a single
    interval —no profile in flight uses this path— but a provider that quietly
    reasons on hourly indicators while the other one reasons on daily is exactly
    the kind of second behaviour that gets found months later, in a result that
    cannot be compared with anything.

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

        price_frame = self._download(symbols, self.interval)
        indicator_frame = (
            price_frame if self.interval == INDICATOR_INTERVAL
            else self._download(symbols, INDICATOR_INTERVAL)
        )

        snapshots: dict[str, MarketSnapshot] = {}
        single = len(symbols) == 1
        for symbol in symbols:
            bars = self._extract_bars(price_frame, symbol, single=single)
            if not bars:
                log.warning("%s: Yahoo no devolvio datos utilizables; se omite.", symbol)
                continue
            context = (
                bars if indicator_frame is price_frame
                else self._extract_bars(indicator_frame, symbol, single=single)
            )
            if not context:
                log.warning(
                    "%s: Yahoo no devolvio barras diarias; se omite (los indicadores "
                    "se calculan siempre en diario).", symbol,
                )
                continue
            snapshot = build_snapshot(symbol, bars, indicator_bars=context)
            if snapshot is not None:
                snapshots[symbol] = snapshot

        log.info(
            "Datos de Yahoo listos para %d/%d simbolos (precio en %s, indicadores en %s).",
            len(snapshots), len(symbols), self.interval, INDICATOR_INTERVAL,
        )
        return snapshots

    def _download(self, symbols: list[str], interval: str) -> Any:
        # Margen amplio: `lookback_days` son sesiones, no dias naturales.
        period_days = int(self.lookback_days * 1.8) + 40

        import yfinance as yf

        try:
            frame = yf.download(
                tickers=symbols,
                start=(datetime.now(timezone.utc) - timedelta(days=period_days)).date(),
                interval=interval,
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=False,
                actions=False,
            )
        except Exception as exc:  # noqa: BLE001 - yfinance lanza tipos variados
            raise MarketDataError(
                f"No se pudieron descargar las barras de {interval} de Yahoo: {exc}"
            ) from exc

        if frame is None or frame.empty:
            raise MarketDataError(
                f"Yahoo no devolvio ninguna barra de {interval}. Comprueba la conexion "
                f"y que los simbolos existan: {', '.join(symbols)}"
            )
        return frame

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
