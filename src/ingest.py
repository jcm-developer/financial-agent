"""Ingesta de precios minuto a minuto.

Aqui vive la logica; el bucle que la invoca cada minuto esta en
`tools/ingestor.py`. Estan separados para que esto se pueda probar sin red y sin
esperar un minuto real por caso de prueba.

Tres decisiones que conviene entender antes de tocar nada:

  * **Un endpoint por simbolo.** yfinance no descarga en lote: pedir 50 simbolos
    son 50 peticiones HTTP a Yahoo. Lo midio el spike (`tools/spike_1m.py`): en
    serie ~8s, en paralelo ~1.5s. Cabe holgado dentro del minuto con 50
    simbolos, pero no escala: a ~170 ms por simbolo, unos 200 agotarian el
    minuto. Si el universo crece, toca paralelizar o cambiar de fuente.

  * **Se reescribe la ultima barra siempre.** La barra del minuto en curso sigue
    cambiando mientras el mercado esta abierto, asi que no basta con escribir lo
    que es nuevo: hay que pisar tambien la ultima que ya teniamos. De ahi el
    `>=` en `_bars_to_write` y el `insert or replace` en la base.

  * **Un fallo no detiene la ingesta.** Un minuto perdido es un hueco en el
    historico, no una averia: el siguiente tick lo intenta otra vez. Solo se
    escala a error tras varios fallos seguidos, que ya sugiere algo sistematico
    (429 sostenido, red caida, Yahoo cambiado).
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from .db import Database
from .indicators import Bar

log = logging.getLogger(__name__)

# Cuantas barras recientes se reescriben aunque ya se tuvieran. Cubre el hueco de
# un tick perdido sin tener que reescribir la sesion entera cada minuto.
SOLAPE_BARRAS = 3

# Umbrales del aviso de contencion. Medido en esta maquina, una escritura sin
# competencia cuesta ~1.1 ms/fila; se avisa a partir de 5 para dejar margen a
# discos mas lentos sin que el aviso pierda sentido.
UMBRAL_ESCRITURA_MS = 1000
UMBRAL_MS_POR_FILA = 5.0


class IngestError(RuntimeError):
    pass


@dataclass
class IngestResult:
    """Lo medido en un tick. Es lo que acaba en `ingest_runs`."""

    pedidos: int = 0
    con_datos: int = 0
    vacios: list[str] = field(default_factory=list)
    barras_escritas: int = 0
    latencia_descarga_ms: int = 0
    latencia_escritura_ms: int = 0
    rate_limited: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.con_datos > 0


class QuoteProvider(Protocol):
    """Fuente de barras de un minuto.

    Existe como interfaz por una razon practica y otra de diseno: los tests
    necesitan una fuente sin red, y cambiar a Alpaca IEX si Yahoo empieza a
    limitar por IP no deberia tocar nada mas que esta pieza.
    """

    def fetch(self, symbols: list[str]) -> dict[str, list[Bar]]:
        ...


class YahooQuotes:
    """Barras de 1 minuto desde Yahoo, via yfinance.

    `threads` por defecto en False como hace `src/market_data.py`: en paralelo la
    cache interna de yfinance ha dado "database is locked" en Windows y ha
    devuelto simbolos vacios *sin avisar*. Es intermitente, asi que no se activa
    sin haberlo medido en sesion (F2.1c).
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

    def fetch(self, symbols: list[str]) -> dict[str, list[Bar]]:
        import yfinance as yf

        from .market_data import YahooMarketData

        ultimo_error: Exception | None = None

        for intento in range(1, self.max_retries + 1):
            try:
                frame = yf.download(
                    tickers=symbols,
                    period="1d",
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

            salida: dict[str, list[Bar]] = {}
            for symbol in symbols:
                bars = YahooMarketData._extract_bars(
                    frame, symbol, single=len(symbols) == 1
                )
                if bars:
                    salida[symbol] = bars
            return salida

        raise IngestError(f"Yahoo fallo tras {self.max_retries} intentos: {ultimo_error}")


def _es_rate_limit(exc: Exception) -> bool:
    texto = f"{type(exc).__name__}: {exc}".lower()
    return "429" in texto or "too many requests" in texto or "rate limit" in texto


def _utc(moment: datetime) -> datetime:
    """Todo se guarda en UTC. yfinance devuelve estas marcas en hora de Nueva
    York, y mezclar husos en la base seria una fuente silenciosa de huecos."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _bars_to_write(bars: list[Bar], last_ts: str | None) -> list[Bar]:
    """Barras que hay que escribir para este simbolo.

    Se incluye la ultima que ya teniamos, no solo las posteriores: mientras el
    minuto esta en curso su barra sigue cambiando, asi que quedarse con la
    primera version congelaria un cierre que aun no era el cierre.
    """
    if last_ts is None:
        return bars

    recientes = [b for b in bars if _utc(b.timestamp).isoformat() >= last_ts]
    if recientes:
        return recientes
    # Nada nuevo: se refresca un solape corto por si un tick anterior escribio
    # una barra a medias.
    return bars[-SOLAPE_BARRAS:]


def load_last_timestamps(db: Database) -> dict[str, str]:
    """Ultima barra conocida de cada simbolo, para no reescribir la sesion entera.

    Se consulta una vez al arrancar y se mantiene en memoria: hacerlo en cada
    tick seria un `group by` sobre cientos de miles de filas cada minuto.
    """
    return {
        row["symbol"]: row["ultimo"]
        for row in db.query("select symbol, max(ts) as ultimo from bars_1m group by symbol")
    }


def _prev_close(db: Database, symbol: str) -> float | None:
    """Cierre de la sesion anterior, si `bar_cache` lo tiene.

    Sale de ahi y no de una peticion aparte porque una peticion por simbolo y
    minuto solo para esto duplicaria la carga contra Yahoo. Si el agente todavia
    no ha corrido, `bar_cache` estara vacio y esto devuelve None: el porcentaje
    del dia cae entonces a la apertura como referencia, que siempre existe.
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
    """Un tick completo: descarga, escritura y registro. No lanza nunca.

    Que no lance es deliberado: lo invoca un bucle que debe seguir vivo. Un tick
    fallido se anota en `ingest_runs` y el siguiente minuto lo reintenta.
    """
    result = IngestResult(pedidos=len(symbols))
    if not symbols:
        return result

    last_ts = last_ts if last_ts is not None else {}
    run_id = db.start_ingest_run(symbols_requested=len(symbols))

    t0 = time.monotonic()
    try:
        por_simbolo = provider.fetch(symbols)
    except Exception as exc:  # noqa: BLE001 - el bucle no puede morir por esto
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
    result.vacios = sorted(set(symbols) - set(por_simbolo))
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

    # Contencion con el ciclo del agente (F2.9). Se mide por coste *por fila*, no
    # por tiempo total: el primer tick escribe la sesion entera (~1.950 filas con
    # 5 simbolos) y tarda segundos sin que nadie lo este bloqueando. Lo que
    # delata una espera por `busy_timeout` es tardar mucho para pocas filas.
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
        symbols_failed=len(result.vacios),
        latency_ms=result.latencia_descarga_ms + result.latencia_escritura_ms,
        rate_limited=result.rate_limited,
        error=result.error[:500] if result.error else None,
    )
    return result
