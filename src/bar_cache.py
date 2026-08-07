"""Cache de barras en SQLite con refresco incremental.

Es lo que hace viable analizar 500 activos: el primer arranque baja el historico
completo (unos minutos), y a partir de ahi cada ciclo solo pide a Yahoo las barras
nuevas. Sin esto, 500 simbolos x 275 barras en cada ciclo acabarian en un HTTP 429
y en un bloqueo temporal de IP.

Tres decisiones que conviene conocer:

  * **El refresco es idempotente.** `insert or replace` sobre la clave
    (simbolo, intervalo, ts) actualiza la barra en lugar de duplicarla. Importa
    porque la ultima barra cambia mientras el mercado esta abierto: cada ciclo la
    reescribe con el precio mas reciente.
  * **Se descarga por lotes.** Yahoo acepta varios tickers por peticion; se
    trocea en grupos y se descansa entre ellos. Con `threads=False`, que evita el
    bloqueo de la cache interna de yfinance en Windows.
  * **Los fallos se cuentan, no se ocultan.** Un simbolo que Yahoo deja de
    reconocer (fusion, exclusion del indice) acumula fallos en `bar_cache_state`,
    y a partir de un umbral se deja de pedir para no gastar peticiones en el vacio.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone

from .db import Database
from .indicators import Bar

log = logging.getLogger(__name__)

# Tickers por peticion a Yahoo. Mas alto va mas rapido pero sube el riesgo de 429
# y hace que un fallo tumbe mas simbolos a la vez.
BATCH_SIZE = 60
# Pausa entre lotes, en segundos.
BATCH_PAUSE = 1.0
# Fallos consecutivos tras los que se deja de pedir un simbolo.
MAX_FAILURES = 5

# Yahoo limita el historico intradia. Son limites suyos, no nuestros.
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

    # -- Lectura -----------------------------------------------------------

    def get_bars(self, symbol: str, *, limit: int = 400) -> list[Bar]:
        """Ultimas `limit` barras de un simbolo, de la mas antigua a la mas nueva."""
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
        """Cuantas barras hay por simbolo. Para diagnostico."""
        return {
            row["symbol"]: int(row["bars"])
            for row in self.db.query(
                "select symbol, bars from bar_cache_state where interval = ?",
                (self.interval,),
            )
        }

    def stats(self) -> dict[str, int]:
        row = self.db.query(
            "select count(1) as simbolos, coalesce(sum(bars), 0) as barras, "
            "       coalesce(sum(case when failures >= ? then 1 else 0 end), 0) as caidos "
            "from bar_cache_state where interval = ?",
            (MAX_FAILURES, self.interval),
        )[0]
        return {
            "simbolos": int(row["simbolos"]),
            "barras": int(row["barras"]),
            "caidos": int(row["caidos"]),
        }

    # -- Refresco ----------------------------------------------------------

    def refresh(
        self,
        symbols: list[str],
        *,
        lookback_days: int = 400,
        force_full: bool = False,
    ) -> dict[str, int]:
        """Descarga las barras que faltan. Devuelve un resumen del trabajo hecho.

        Para cada simbolo se pide desde su ultima barra conocida; los que no
        tienen nada se piden completos. Los que comparten fecha de inicio se
        agrupan en la misma peticion.
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

        # Agrupa por fecha de inicio para poder pedir lotes.
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
                # Se resta un margen para que la ultima barra (posiblemente
                # incompleta) se vuelva a pedir y se reescriba con datos frescos.
                margin = timedelta(days=4) if self.interval == "1d" else timedelta(days=2)
                start = (last - margin).date()
                if start < full_start:
                    start = full_start

            groups.setdefault(start, []).append(symbol)

        summary = {"peticiones": 0, "simbolos": 0, "barras": 0, "fallos": 0,
                   "omitidos": skipped}

        for start, group in sorted(groups.items()):
            for batch in _chunks(group, BATCH_SIZE):
                summary["peticiones"] += 1
                inserted, failed = self._fetch_batch(yf, batch, start)
                summary["barras"] += inserted
                summary["fallos"] += failed
                summary["simbolos"] += len(batch) - failed
                if BATCH_PAUSE:
                    _time.sleep(BATCH_PAUSE)

        log.info(
            "Cache %s: %d peticiones, %d barras nuevas, %d simbolos con fallo, "
            "%d omitidos por fallos repetidos.",
            self.interval, summary["peticiones"], summary["barras"],
            summary["fallos"], summary["omitidos"],
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
        except Exception as exc:  # noqa: BLE001 - yfinance lanza tipos variados
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
        """Saca las barras de un simbolo, sea plano o multi-indice el DataFrame.

        No se decide por el numero de tickers pedidos: yfinance devuelve un
        DataFrame plano o con multi-indice segun version y numero de simbolos, y
        acertar por adivinacion hacia que la extraccion devolviera silenciosamente
        cero barras. Se prueban las dos formas y se acepta la que tenga las
        columnas OHLCV.
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
            # `insert or replace`: la ultima barra se reescribe en cada ciclo
            # mientras el mercado esta abierto.
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
