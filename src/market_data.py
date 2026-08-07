"""Obtencion de barras e indicadores. Tres proveedores intercambiables.

  * `UniverseMarketData` (recomendado para 500 activos) refresca la cache de
    barras, pasa el universo por el screener y devuelve solo los mejores
    candidatos. Es el embudo que hace viable un universo grande.
  * `YahooMarketData` descarga la watchlist directamente. Sin cuenta ni clave.
  * `AlpacaMarketData` para quien ya tenga credenciales o vaya a operar en real.

**Contrato comun:** `fetch_snapshots(must_include)` devuelve los snapshots de todo
lo que hay que analizar. `must_include` son los simbolos obligatorios —las
posiciones abiertas, que necesitan revision aunque el screener no las seleccione—
y cada proveedor anade los suyos.

La separacion de precios (decision / ejecucion / valoracion) esta explicada en
`MarketSnapshot`. La regla, aqui y en la cache: **la ultima barra nunca se usa para
decidir**, porque puede estar a medias si el mercado sigue abierto; se usa su
apertura como precio de ejecucion.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from .indicators import Bar, compute_snapshot
from .models import MarketSnapshot

log = logging.getLogger(__name__)

# Barras minimas para que los indicadores largos (SMA200) tengan sentido. Por
# debajo de esto seguimos analizando, pero el snapshot llevara claves en null.
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
    """Ensambla el snapshot separando la barra de decision de la de ejecucion.

    `bars` debe venir ordenado del mas antiguo al mas reciente. La ultima barra
    se reserva para el precio de ejecucion; los indicadores se calculan sobre
    todo lo anterior.
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
    # Con barras horarias la fecha sola no distingue una barra de otra, asi que se
    # incluye la hora. Con barras diarias sobraria ruido en el prompt.
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
    """Barras diarias desde Yahoo, sin cuenta ni clave.

    Advertencias honestas sobre esta fuente:

      * yfinance es un cliente no oficial. Yahoo cambia sus endpoints cada
        cierto tiempo y entonces hay que actualizar el paquete.
      * Se descarga con `threads=False` a proposito: en paralelo, la cache
        interna de yfinance (un SQLite propio) da "database is locked" en
        Windows y devuelve simbolos vacios sin avisar.
      * Los precios no vienen ajustados por splits ni dividendos
        (`auto_adjust=False`), que es lo correcto aqui: queremos el precio al
        que se habria operado, no la serie ajustada a posteriori.
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
        """Saca las barras de un simbolo, sea plano o multi-indice el DataFrame.

        No se decide por el numero de tickers pedidos: yfinance devuelve una forma
        u otra segun version y numero de simbolos, y adivinar hacia que la
        extraccion devolviera cero barras sin avisar. Se prueban las dos.
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

        # Una fila con Close nulo es una sesion sin datos (festivo, o el simbolo
        # todavia no cotizaba): se descarta en lugar de arrastrar huecos.
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
# Alpaca
# ----------------------------------------------------------------------

class AlpacaMarketData:
    """Barras diarias desde Alpaca. Requiere credenciales.

    El feed `iex` es gratuito y en tiempo real, pero solo cubre el volumen de
    IEX (en torno al 2% del mercado): los cierres diarios son fiables, el volumen
    es una fraccion del real. Tenlo en cuenta al leer `volume_ratio`.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        feed: str = "iex",
        watchlist: tuple[str, ...] | list[str] = (),
        lookback_days: int = 200,
    ) -> None:
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient

        self._client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
        self._feed = DataFeed.SIP if feed == "sip" else DataFeed.IEX
        self.watchlist = tuple(watchlist)
        self.lookback_days = lookback_days

    def fetch_snapshots(
        self, must_include: tuple[str, ...] | list[str] = ()
    ) -> dict[str, MarketSnapshot]:
        symbols = sorted(set(self.watchlist) | set(must_include))
        if not symbols:
            return {}

        lookback_days = self.lookback_days

        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        start = datetime.now(timezone.utc) - timedelta(days=int(lookback_days * 1.8) + 40)
        request = StockBarsRequest(
            symbol_or_symbols=list(symbols),
            timeframe=TimeFrame.Day,
            start=start,
            feed=self._feed,
        )
        try:
            barset = self._client.get_stock_bars(request)
        except Exception as exc:  # noqa: BLE001 - el SDK lanza tipos variados
            raise MarketDataError(f"No se pudieron descargar las barras: {exc}") from exc

        raw = getattr(barset, "data", None) or {}
        snapshots: dict[str, MarketSnapshot] = {}

        for symbol in symbols:
            raw_bars = raw.get(symbol) or []
            bars = [
                Bar(
                    timestamp=b.timestamp,
                    open=float(b.open),
                    high=float(b.high),
                    low=float(b.low),
                    close=float(b.close),
                    volume=float(b.volume),
                )
                for b in raw_bars
            ]
            bars.sort(key=lambda b: b.timestamp)
            snapshot = build_snapshot(symbol, bars)
            if snapshot is not None:
                snapshots[symbol] = snapshot

        log.info(
            "Datos de Alpaca listos para %d/%d simbolos.", len(snapshots), len(symbols)
        )
        return snapshots


# ----------------------------------------------------------------------

def build_market_data(settings, database=None) -> MarketDataProvider:
    """Elige el proveedor segun la configuracion.

    Con `UNIVERSE_FILE` puesto se usa el embudo, que necesita la base de datos
    para la cache de barras.
    """
    if settings.screener.enabled:
        if database is None:
            raise MarketDataError(
                "El embudo por universo necesita la base de datos para la cache "
                "de barras."
            )
        if settings.data_provider != "yahoo":
            raise MarketDataError(
                "El embudo por universo solo esta implementado sobre Yahoo. "
                "Quita UNIVERSE_FILE o pon DATA_PROVIDER=yahoo."
            )
        from .universe_data import UniverseMarketData

        return UniverseMarketData(
            database=database,
            screener=settings.screener,
            interval=settings.bar_interval,
            lookback_days=settings.lookback_days,
        )

    if settings.data_provider == "alpaca":
        return AlpacaMarketData(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            feed=settings.alpaca_data_feed,
            watchlist=settings.watchlist,
            lookback_days=settings.lookback_days,
        )

    return YahooMarketData(
        watchlist=settings.watchlist,
        lookback_days=settings.lookback_days,
        interval=settings.bar_interval,
    )
