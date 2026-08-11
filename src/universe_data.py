"""The funnel: large universe -> cache -> screener -> candidates for the LLM.

It lives in its own module so `market_data` depends on neither the database nor
the screener: `YahooMarketData` stays a stateless object that only talks to the
network.

**The two intervals are kept apart, and that is the key to this being viable.**
The screener always sifts with DAILY bars; the price and the execution use the
configured interval. With `BAR_INTERVAL=1h`, downloading hourly bars for 503
assets would be some 2.5 million rows and several hundred MB, when all that is
needed are those of the twenty that will reach the model.

**Since F9.14 the daily cache does double duty, and that is why this file got
shorter rather than longer.** The indicators the analyst reads are always daily
(`market_data.INDICATOR_INTERVAL`), and the daily bars of the whole universe were
already here for the screener — so the change costs no download at all. What the
intraday cache is for now is narrower and better named: the price to decide on and
the bar to execute against.

Cost measured with 503 assets and daily bars:

  * First run: 9 requests to Yahoo (batches of 60), ~4.5 minutes, 103,000 bars
    cached, 18 MB.
  * Later cycles: the same 9 requests but only for the new bars, ~90 seconds.
    Negligible next to the 15 minutes the model takes to analyse twenty
    candidates.
  * Screener: arithmetic over the cache, no network.
  * Hourly bars, if asked for: only for the candidates, ~1 extra request.

Open positions are always included, even when the screener does not select them:
a live position needs reviewing precisely when it stops being attractive.
"""

from __future__ import annotations

import logging

from .bar_cache import BarCache
from .config import ScreenerSettings
from .db import Database
from .market_data import INDICATOR_INTERVAL, MarketDataError, build_snapshot
from .models import MarketSnapshot
from .screener import ScreenerLimits, ScreenerReport, load_universe, screen

log = logging.getLogger(__name__)


class UniverseMarketData:
    def __init__(
        self,
        *,
        database: Database,
        screener: ScreenerSettings,
        interval: str = "1d",
        lookback_days: int = 400,
    ) -> None:
        self.db = database
        self.settings = screener
        self.interval = interval
        self.lookback_days = lookback_days
        # Sifting always runs on daily bars: they are the only thing downloaded
        # for the whole universe. See the note in the header.
        #
        # And since F9.14 the same cache is what the indicators are computed on,
        # so the two names are two roles of one object rather than a duplicate:
        # `screen_cache` is what the screener scores, `indicator_cache` is what the
        # analyst reads. They are the same bars by construction —both daily— and
        # keeping the alias says which of the two a line is talking about.
        self.screen_cache = BarCache(database, interval=INDICATOR_INTERVAL)
        self.indicator_cache = self.screen_cache
        # The intraday cache, downloaded only for the selected symbols, is now for
        # the price to decide on and the bar to execute against — nothing else.
        self.price_cache = (
            self.screen_cache if interval == INDICATOR_INTERVAL
            else BarCache(database, interval=interval)
        )
        self.limits = ScreenerLimits(
            top_n=screener.top_n,
            min_turnover=screener.min_turnover,
            min_price=screener.min_price,
            max_volatility_pct=screener.max_volatility_pct,
        )
        # The last report stays available so the cycle can record it.
        self.last_report: ScreenerReport | None = None

    def fetch_snapshots(
        self, must_include: tuple[str, ...] | list[str] = ()
    ) -> dict[str, MarketSnapshot]:
        universe = load_universe(self.settings.universe_file)
        required = sorted(set(must_include))
        all_symbols = sorted(set(universe) | set(required))

        log.info(
            "Universo: %d simbolos (%d del fichero, %d posiciones abiertas).",
            len(all_symbols), len(universe), len(required),
        )

        # 1. Incremental refresh of the daily cache, for the whole universe.
        before = self.screen_cache.stats()
        self.screen_cache.refresh(all_symbols, lookback_days=self.lookback_days)
        after = self.screen_cache.stats()
        log.info(
            "Cache diaria: %d simbolos, %d barras (%+d en este ciclo).",
            after["symbols"], after["bars"], after["bars"] - before["bars"],
        )

        # 2. Read from the cache. The network is no longer touched.
        bars_needed = max(self.lookback_days, 260)
        screen_bars = {
            symbol: bars
            for symbol in all_symbols
            if (bars := self.screen_cache.get_bars(symbol, limit=bars_needed))
        }

        if not screen_bars:
            raise MarketDataError(
                "La cache esta vacia tras el refresco. Comprueba la conexion y que "
                f"el fichero de universo {self.settings.universe_file} tenga simbolos "
                "que Yahoo reconozca."
            )

        # 3. Screener. Open positions are not scored: they get in regardless, and
        #    mixing them would falsify the report.
        universe_bars = {
            symbol: bars for symbol, bars in screen_bars.items()
            if symbol not in set(required)
        }
        report = screen(universe_bars, self.limits, mode=self.settings.mode)
        self.last_report = report

        selected = [c.symbol for c in report.candidates]
        to_analyze = sorted(set(selected) | set(required))

        # 4. If the price runs on another interval, only now is it downloaded
        #    —and only for the selected ones. That is the difference between 20
        #    symbols and 503.
        price_bars = screen_bars
        if self.price_cache is not self.screen_cache:
            log.info(
                "Bajando barras de %s para los %d activos seleccionados.",
                self.interval, len(to_analyze),
            )
            self.price_cache.refresh(to_analyze, lookback_days=self.lookback_days)
            price_bars = {
                symbol: bars
                for symbol in to_analyze
                if (bars := self.price_cache.get_bars(symbol, limit=bars_needed))
            }

        # 5. Snapshots only for what was selected: the price in the profile's
        #    interval, the indicators always daily (F9.14). `screen_bars` is
        #    already the daily series, so the second one costs no download.
        snapshots: dict[str, MarketSnapshot] = {}
        for symbol in to_analyze:
            bars = price_bars.get(symbol)
            if not bars:
                log.warning(
                    "%s no tiene barras de %s en la cache; se omite.",
                    symbol, self.interval,
                )
                continue
            # Explicit and not `screen_bars.get(symbol)` inline: `None` means "use
            # the price series" to `build_snapshot`, so a symbol with intraday bars
            # and no daily ones would come back with hourly indicators and no
            # warning — the one silent case this whole change exists to remove. A
            # skip here lands in the cycle's SIN PRECIO path, which already leaves
            # a `no_price` risk event.
            context = screen_bars.get(symbol)
            if not context:
                log.warning(
                    "%s no tiene barras diarias en la cache; se omite, porque los "
                    "indicadores se calculan siempre en diario.", symbol,
                )
                continue
            snapshot = build_snapshot(symbol, bars, indicator_bars=context)
            if snapshot is not None:
                snapshots[symbol] = snapshot

        log.info(
            "Embudo: %d del universo -> %d candidatos + %d posiciones = %d analisis "
            "(precio en %s, indicadores en %s).",
            len(universe_bars), len(selected), len(required), len(snapshots),
            self.interval, INDICATOR_INTERVAL,
        )
        return snapshots

    def describe_selection(self) -> str:
        """Summary of the last sift, for the cycle's log."""
        if self.last_report is None:
            return "sin cribar todavia"
        top = ", ".join(
            f"{c.symbol}({c.score:.2f})" for c in self.last_report.candidates[:10]
        )
        return f"{self.last_report.summary()}. Top: {top}"
