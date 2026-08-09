#!/usr/bin/env python
"""Spike F2.1: measures whether Yahoo is good enough for minute-by-minute ingestion.

It is a measurement experiment, not production code: it does not write to the
database nor touch anything of the cycle. Its only job is to answer three
questions before the real ingestor is built (F2.2):

  1. LAG. The most important one. "Every minute" only holds if the datum is a
     minute old. Yahoo guarantees no real time, so it has to be measured: if it
     comes 15 minutes behind, the ingestor's design does not change but what can
     be concluded from the experiment does.
  2. RELIABILITY. How many 429s appear while keeping one request per minute for a
     whole session, and how many symbols come back empty.
  3. COST. How long the batch download takes, to know whether it fits comfortably
     inside the minute.

Usage:

    python tools/spike_1m.py --once --force      # one pass, even if closed
    python tools/spike_1m.py --minutes 390       # a whole session
    python tools/spike_1m.py --symbols AAPL,MSFT --once --force

The results are written to JSONL (--out) line by line, so a cut halfway does not
take down what has been measured so far.

About the lag: yfinance returns the **start** timestamp of each bar. A 15:30 bar
covers 15:30-15:31, so before 15:31 it cannot be complete. That is why
`lag_cierre` (= now - end of the bar) is measured, which is the honest number:
how long a minute that has already closed takes to become available. A
`lag_cierre` of seconds is excellent; one of ~15 minutes means Yahoo is serving
the feed delayed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from src import market_calendar  # noqa: E402
from src.market_data import YahooMarketData  # noqa: E402

BAR_SECONDS = 60


def load_symbols(spec: str, count: int, universe_file: Path) -> list[str]:
    """Simbolos a pedir: lista explicita, o los `count` primeros del universo."""
    if spec:
        return sorted({s.strip().upper() for s in spec.split(",") if s.strip()})

    if not universe_file.exists():
        raise SystemExit(
            f"No se encontro {universe_file}. Pasa los simbolos a mano:\n"
            "    python tools/spike_1m.py --symbols SAN.MC,SAP.DE,ASML.AS --once --force"
        )
    symbols = [
        line.strip().upper()
        for line in universe_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return symbols[:count]


def one_pass(
    symbols: list[str],
    threads: bool = False,
    market: str | market_calendar.Market | None = None,
) -> dict:
    """One download of the symbol batch. Returns the metrics, never raises.

    `threads` is no minor detail: yfinance asks for **one endpoint per symbol**,
    not one for the batch, so with 50 symbols that is 50 requests to Yahoo.
    Serially it takes ~8s and in parallel ~1.5s, but in parallel it also
    concentrates the 50 requests into an instant, which is worse for the rate
    limit. Measure both.
    """
    import yfinance as yf

    now = datetime.now(timezone.utc)
    started = time.monotonic()
    frame = None
    error = None

    try:
        frame = yf.download(
            tickers=symbols,
            period="1d",
            interval="1m",
            auto_adjust=False,
            progress=False,
            group_by="ticker",
            # False by default, as in src/market_data.py: in parallel, yfinance's
            # internal cache has given "database is locked" on Windows, returning
            # empty symbols *without warning*. It is an intermittent fault, so a
            # few green passes do not rule it out: hence the spike counting empty
            # symbols on every pass.
            threads=threads,
            actions=False,
        )
    except Exception as exc:  # noqa: BLE001 - yfinance lanza tipos variados
        error = f"{type(exc).__name__}: {exc}"

    latency = time.monotonic() - started

    con_datos: list[str] = []
    empty: list[str] = []
    lags: list[float] = []
    ultima_barra: datetime | None = None

    if frame is not None and not getattr(frame, "empty", True):
        for symbol in symbols:
            bars = YahooMarketData._extract_bars(
                frame, symbol, single=len(symbols) == 1
            )
            if not bars:
                empty.append(symbol)
                continue
            con_datos.append(symbol)
            inicio = bars[-1].timestamp
            if inicio.tzinfo is None:
                inicio = inicio.replace(tzinfo=timezone.utc)
            # End of the bar: that is when the minute is closed and the datum
            # could be available.
            fin = inicio + timedelta(seconds=BAR_SECONDS)
            lags.append((now - fin).total_seconds())
            if ultima_barra is None or inicio > ultima_barra:
                # Always to UTC: yfinance returns these marks in New York time,
                # and the rest of the project stores everything in UTC (schema.sql).
                ultima_barra = inicio.astimezone(timezone.utc)
    else:
        empty = list(symbols)

    return {
        "ts": now.isoformat(),
        # Of the market being measured, not the American one: whether the measured
        # lag is declared valid at the end depends on this field (see main).
        "mercado_abierto": market_calendar.is_session_open(market=market),
        "threads": threads,
        "pedidos": len(symbols),
        "con_datos": len(con_datos),
        "vacios": len(empty),
        "simbolos_vacios": empty[:10],
        "latencia_s": round(latency, 2),
        "ultima_barra": ultima_barra.isoformat() if ultima_barra else None,
        "lag_cierre_min_s": round(min(lags), 1) if lags else None,
        "lag_cierre_mediana_s": round(statistics.median(lags), 1) if lags else None,
        "lag_cierre_max_s": round(max(lags), 1) if lags else None,
        "error": error,
        # Marked separately because it is the failure that would force a source change.
        "rate_limited": bool(error and ("429" in error or "Too Many Requests" in error)),
    }


def format_row(n: int, r: dict) -> str:
    if r["error"]:
        marca = "429" if r["rate_limited"] else "ERR"
        return f"  {n:>4}  {marca}  {r['error'][:80]}"
    lag = r["lag_cierre_mediana_s"]
    lag_txt = f"{lag / 60:>6.1f} min" if lag is not None else "     s/d"
    return (
        f"  {n:>4}  {r['con_datos']:>3}/{r['pedidos']:<3} simbolos"
        f"  {r['latencia_s']:>6.2f}s descarga"
        f"  retraso {lag_txt}"
        f"  ultima barra {r['ultima_barra'][11:16] if r['ultima_barra'] else '--:--'}Z"
    )


def sleep_to_next_minute() -> None:
    """Waits for the start of the next minute, which is when the bar closes."""
    ahora = datetime.now(timezone.utc)
    objetivo = (ahora + timedelta(minutes=1)).replace(second=2, microsecond=0)
    espera = (objetivo - ahora).total_seconds()
    if espera > 0:
        time.sleep(espera)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--symbols", default="", help="Lista separada por comas.")
    parser.add_argument("--market", default="us",
                        help="Bolsa a medir: eu o us. Decide el calendario que "
                             "se consulta y, si no se pasa --universe, el "
                             "fichero de simbolos. Def. us.")
    parser.add_argument("--universe", default="",
                        help="Fichero de universo. Por defecto, el del mercado.")
    parser.add_argument("--count", type=int, default=50,
                        help="Cuantos simbolos del universo. Def. 50.")
    parser.add_argument("--minutes", type=int, default=0,
                        help="Minutos a medir. 0 = la sesion entera del mercado "
                             "elegido (390 en us, 510 en eu).")
    parser.add_argument("--once", action="store_true", help="Una sola pasada.")
    parser.add_argument("--force", action="store_true",
                        help="Medir aunque el mercado este cerrado.")
    parser.add_argument("--threads", action="store_true",
                        help="Descargar en paralelo: ~5x mas rapido, pero concentra "
                             "una peticion por simbolo en un instante. Ver one_pass().")
    parser.add_argument("--out", default="spike_1m.jsonl")
    args = parser.parse_args()

    try:
        market = market_calendar.get_market(args.market)
    except market_calendar.UnknownMarket as exc:
        raise SystemExit(f"  {exc}") from exc

    universe_file = (
        Path(args.universe) if args.universe else APP_DIR / market.universe_file
    )
    symbols = load_symbols(args.symbols, args.count, universe_file)
    # The operating window and not the session: measuring the 15 minutes after the
    # close is precisely where it will show whether the last bar arrives late,
    # which is the question of F2.1c.
    abierto = market_calendar.is_operating(market=market)

    print()
    print(f"  Spike de ingesta 1m — {len(symbols)} simbolos de {market.label}")
    print(f"  Mercado: {market_calendar.describe(market=market)}")
    print(f"  Resultados: {args.out}")

    if not abierto and not args.force:
        print()
        print("  El mercado esta cerrado. La medicion que importa (el retraso real")
        print("  del dato en vivo) solo tiene sentido en sesion.")
        print(f"  Proximo arranque de la ventana: "
              f"{market_calendar.next_operating_open(market=market)}")
        print()
        print("  Para validar solo el mecanismo contra la ultima sesion:")
        print(f"      python tools/spike_1m.py --market {market.code} --once --force")
        print()
        return 0

    if not abierto:
        print()
        print("  AVISO: mercado cerrado. Los datos son de la ultima sesion, asi que")
        print("  el retraso medido NO es representativo. Sirve para comprobar que la")
        print("  descarga funciona, cuanto tarda y cuantos simbolos vuelven vacios.")

    pasadas = 1 if args.once else (args.minutes or market.operating_minutes)
    out = Path(args.out)
    resultados: list[dict] = []

    print()
    print("  ---- n  simbolos      descarga     retraso mediano   ultima barra ----")

    try:
        for n in range(1, pasadas + 1):
            r = one_pass(symbols, threads=args.threads, market=market)
            resultados.append(r)
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(format_row(n, r), flush=True)
            if n < pasadas:
                sleep_to_next_minute()
    except KeyboardInterrupt:
        print("\n  Interrumpido.")

    summary(resultados)
    return 0


def summary(resultados: list[dict]) -> None:
    if not resultados:
        return

    ok = [r for r in resultados if not r["error"]]
    con_lag = [r for r in ok if r["lag_cierre_mediana_s"] is not None]
    rate_limited = sum(1 for r in resultados if r["rate_limited"])
    errores = sum(1 for r in resultados if r["error"])

    print()
    print("  ================ RESUMEN ================")
    print(f"  Pasadas:            {len(resultados)}")
    print(f"  Con error:          {errores}  (de ellas 429: {rate_limited})")

    if ok:
        lat = [r["latencia_s"] for r in ok]
        print(f"  Descarga mediana:   {statistics.median(lat):.2f}s"
              f"   (max {max(lat):.2f}s)")
        cobertura = [r["con_datos"] / r["pedidos"] for r in ok if r["pedidos"]]
        print(f"  Cobertura mediana:  {statistics.median(cobertura) * 100:.0f}% de simbolos")
        vacios_total = sum(r["vacios"] for r in ok)
        if vacios_total:
            print(f"  Simbolos vacios:    {vacios_total} en total"
                  f"  (ej. {', '.join(ok[-1]['simbolos_vacios'][:5]) or '-'})")

    if con_lag:
        lags = [r["lag_cierre_mediana_s"] for r in con_lag]
        mediana = statistics.median(lags)
        print(f"  RETRASO mediano:    {mediana / 60:.1f} min"
              f"   (min {min(lags) / 60:.1f} / max {max(lags) / 60:.1f})")
        print()
        en_sesion = any(r["mercado_abierto"] for r in con_lag)
        if not en_sesion:
            print("  El retraso de arriba NO es valido: se midio con el mercado")
            print("  cerrado, asi que solo dice cuanto hace que termino la sesion.")
        elif mediana < 180:
            print("  VEREDICTO: el dato llega con menos de 3 minutos. Yahoo sirve")
            print("  para la ingesta cada minuto (F2.2 adelante).")
        elif mediana < 20 * 60:
            print("  VEREDICTO: hay retraso apreciable. El ingestor funciona igual,")
            print("  pero conviene anotarlo: las decisiones se toman sobre datos de")
            print("  hace ~%.0f min, no del momento." % (mediana / 60))
        else:
            print("  VEREDICTO: demasiado retraso para llamarlo tiempo real.")
            print("  Habria que buscar otra fuente de datos de 1 minuto.")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
