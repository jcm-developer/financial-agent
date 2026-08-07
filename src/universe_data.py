"""El embudo: universo grande -> cache -> screener -> candidatos para el LLM.

Vive en su propio modulo para que `market_data` no dependa de la base de datos ni
del screener: los proveedores simples (Yahoo, Alpaca) siguen siendo objetos sin
estado que solo hablan con la red.

**Los dos intervalos van por separado, y es la clave de que esto sea viable.** El
screener criba siempre con barras DIARIAS; el analisis usa el intervalo
configurado. Con `BAR_INTERVAL=1h`, bajar barras horarias de 503 activos serian
unos 2,5 millones de filas y varios cientos de MB, cuando solo hacen falta las de
los veinte que van a llegar al modelo.

Coste medido con 503 activos y barras diarias:

  * Primer arranque: 9 peticiones a Yahoo (lotes de 60), ~4,5 minutos, 103.000
    barras en cache, 18 MB.
  * Ciclos siguientes: las mismas 9 peticiones pero solo con las barras nuevas,
    ~90 segundos. Insignificante al lado de los 15 minutos que tarda el modelo en
    analizar veinte candidatos.
  * Screener: aritmetica sobre la cache, sin red.
  * Barras horarias, si se piden: solo de los candidatos, ~1 peticion mas.

Las posiciones abiertas se incluyen siempre, aunque el screener no las seleccione:
una posicion viva necesita revision precisamente cuando deja de estar atractiva.
"""

from __future__ import annotations

import logging

from .bar_cache import BarCache
from .config import ScreenerSettings
from .db import Database
from .market_data import MarketDataError, build_snapshot
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
        # El cribado siempre va con barras diarias: es lo unico que se descarga
        # para todo el universo. Ver la nota del encabezado.
        self.screen_cache = BarCache(database, interval="1d")
        self.analysis_cache = (
            self.screen_cache if interval == "1d" else BarCache(database, interval=interval)
        )
        self.limits = ScreenerLimits(
            top_n=screener.top_n,
            min_dollar_volume=screener.min_dollar_volume,
            min_price=screener.min_price,
            max_volatility_pct=screener.max_volatility_pct,
        )
        # El ultimo informe queda disponible para que el ciclo lo registre.
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

        # 1. Refresco incremental de la cache diaria, para todo el universo.
        before = self.screen_cache.stats()
        self.screen_cache.refresh(all_symbols, lookback_days=self.lookback_days)
        after = self.screen_cache.stats()
        log.info(
            "Cache diaria: %d simbolos, %d barras (%+d en este ciclo).",
            after["simbolos"], after["barras"], after["barras"] - before["barras"],
        )

        # 2. Lectura desde la cache. Ya no se toca la red.
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

        # 3. Screener. Las posiciones abiertas no se puntuan: entran de todos
        #    modos, y mezclarlas falsearia el informe.
        universe_bars = {
            symbol: bars for symbol, bars in screen_bars.items()
            if symbol not in set(required)
        }
        report = screen(universe_bars, self.limits, mode=self.settings.mode)
        self.last_report = report

        selected = [c.symbol for c in report.candidates]
        to_analyze = sorted(set(selected) | set(required))

        # 4. Si el analisis va en otro intervalo, ahora si se baja —solo de los
        #    seleccionados. Es la diferencia entre 20 simbolos y 503.
        analysis_bars = screen_bars
        if self.analysis_cache is not self.screen_cache:
            log.info(
                "Bajando barras de %s para los %d activos seleccionados.",
                self.interval, len(to_analyze),
            )
            self.analysis_cache.refresh(to_analyze, lookback_days=self.lookback_days)
            analysis_bars = {
                symbol: bars
                for symbol in to_analyze
                if (bars := self.analysis_cache.get_bars(symbol, limit=bars_needed))
            }

        # 5. Snapshots solo de lo seleccionado.
        snapshots: dict[str, MarketSnapshot] = {}
        for symbol in to_analyze:
            bars = analysis_bars.get(symbol)
            if not bars:
                log.warning(
                    "%s no tiene barras de %s en la cache; se omite.",
                    symbol, self.interval,
                )
                continue
            snapshot = build_snapshot(symbol, bars)
            if snapshot is not None:
                snapshots[symbol] = snapshot

        log.info(
            "Embudo: %d del universo -> %d candidatos + %d posiciones = %d analisis "
            "en barras de %s.",
            len(universe_bars), len(selected), len(required), len(snapshots),
            self.interval,
        )
        return snapshots

    def describe_selection(self) -> str:
        """Resumen del ultimo cribado, para el log del ciclo."""
        if self.last_report is None:
            return "sin cribar todavia"
        top = ", ".join(
            f"{c.symbol}({c.score:.2f})" for c in self.last_report.candidates[:10]
        )
        return f"{self.last_report.summary()}. Top: {top}"
