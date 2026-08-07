#!/usr/bin/env python
"""Punto de entrada del agente.

    python run.py check     Verifica configuracion y conectividad. Empieza aqui.
    python run.py status    Muestra el estado de la cuenta y las posiciones.
    python run.py cycle     Ejecuta un ciclo completo de analisis y operativa.
    python run.py report    Analitica del historico: P&L, calibracion, rechazos.
    python run.py serve     Dashboard web en http://127.0.0.1:8000

`cycle` esta pensado para lanzarse desde el Programador de tareas de Windows o
cron una o dos veces al dia, no en bucle continuo: los modelos gratuitos de NIM
tienen limites de peticiones y el horizonte del agente es de dias.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from src.config import ConfigError, DashboardSettings, Settings
from src.cycle import TradingCycle
from src.llm import LLMClient, LLMError


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s  %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # El SDK de Alpaca y httpx son muy verbosos en DEBUG.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)


def _print_header(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


# ----------------------------------------------------------------------
# check
# ----------------------------------------------------------------------

def command_check(settings: Settings) -> int:
    """Comprueba las tres integraciones por separado para que un fallo diga
    exactamente que pieza esta mal configurada."""
    failures: list[str] = []

    _print_header("Configuracion")
    print(f"  {settings.describe()}")
    if settings.screener.enabled:
        # Se dice explicitamente: con embudo la watchlist no se usa, y verla
        # impresa hacia pensar lo contrario.
        print("  WATCHLIST ignorada: manda UNIVERSE_FILE.")
    else:
        print(f"  Watchlist: {', '.join(settings.watchlist)}")
    risk = settings.risk
    print(
        f"  Riesgo: {risk.risk_per_trade_pct}% por operacion, "
        f"max {risk.max_position_pct}% por posicion, "
        f"max {risk.max_open_positions} posiciones, "
        f"kill switch a -{risk.max_daily_loss_pct}%"
    )

    _print_header("Calendario de mercado")
    from src import market_calendar

    print(f"  {market_calendar.describe()}")
    allowed, reason = market_calendar.should_run(settings.bar_interval)
    if allowed:
        print(f"  Un ciclo ahora SI se ejecutaria: {reason}")
    elif settings.skip_when_market_closed:
        print(f"  Un ciclo ahora se OMITIRIA: {reason}")
        print("  Para forzarlo de todos modos: SKIP_WHEN_MARKET_CLOSED=false")
    else:
        print(f"  {reason}, pero SKIP_WHEN_MARKET_CLOSED=false: se ejecutaria.")

    _print_header("Datos de mercado")
    try:
        from src.market_data import YahooMarketData, build_market_data

        if settings.screener.enabled:
            # Con embudo no se prueba el universo entero: seria descargar 500
            # simbolos solo para diagnosticar. Se sondea con tres.
            from src.screener import load_universe

            universe = load_universe(settings.screener.universe_file)
            print(f"  Universo: {len(universe)} simbolos en "
                  f"{settings.screener.universe_file}")
            print(f"  Embudo: top {settings.screener.top_n} por "
                  f"'{settings.screener.mode}' -> al modelo")
            probe = tuple(universe[:3])
            market_data = YahooMarketData(
                watchlist=probe, lookback_days=settings.lookback_days,
                interval=settings.bar_interval,
            )
        else:
            probe = settings.watchlist[:3]
            market_data = build_market_data(settings)

        snapshots = market_data.fetch_snapshots()
        if not snapshots:
            raise RuntimeError(
                f"No se obtuvieron barras para {', '.join(probe)}. "
                "Comprueba la conexion y que los simbolos existan."
            )
        source = ("Yahoo Finance (yfinance)" if settings.data_provider == "yahoo"
                  else f"Alpaca, feed {settings.alpaca_data_feed}")
        print(f"  OK  fuente={source}  intervalo={settings.bar_interval}")
        print(f"      {'ACTIVO':<8}{'DECISION':>10}{'EJECUCION':>11}"
              f"{'RSI':>7}{'ATR':>8}{'BARRAS':>8}  SESION")
        for symbol, snapshot in snapshots.items():
            indicators = snapshot.indicators
            print(
                f"      {symbol:<8}{snapshot.price:>10.2f}"
                f"{snapshot.execution_price:>11.2f}"
                f"{_show(indicators.get('rsi_14')):>7}"
                f"{_show(indicators.get('atr_14')):>8}"
                f"{indicators.get('bars_available'):>8}  {snapshot.session or 'n/d'}"
            )
        unidad = "sesion" if settings.bar_interval == "1d" else "hora"
        print(f"      DECISION = cierre de la ultima {unidad} completa (lo que ve el")
        print(f"      analista). EJECUCION = apertura de la {unidad} siguiente,")
        print("      donde se opera. Que sean distintos es lo que evita operar")
        print("      con informacion del futuro.")
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO  {exc}")
        if settings.data_provider == "yahoo":
            print("      Si el error viene de yfinance, prueba: pip install -U yfinance")
        failures.append("Datos de mercado")

    if settings.broker == "sim":
        _print_header("Broker simulado")
        try:
            from src.db import Database
            from src.sim_broker import SimBroker

            with Database(path=settings.db_path) as database:
                portfolio_id = database.ensure_portfolio(
                    name=settings.portfolio_name,
                    mode=settings.mode,
                    initial_budget=settings.initial_budget,
                )
                broker = SimBroker(
                    database=database,
                    portfolio_id=portfolio_id,
                    initial_cash=settings.initial_budget,
                    slippage_bps=settings.sim_slippage_bps,
                    commission_per_order=settings.sim_commission,
                )
                account = broker.get_account_state()
                fills = database.query(
                    "select count(*) as n from sim_fills where account_id = ?",
                    (portfolio_id,),
                )[0]["n"]

            print(f"  OK  sin cuenta de broker: la contabilidad es local")
            print(f"      efectivo=${account.cash:,.2f}  equity=${account.equity:,.2f}")
            print(f"      posiciones={len(account.positions)}  ejecuciones registradas={fills}")
            print(f"      deslizamiento={settings.sim_slippage_bps:.0f} pb  "
                  f"comision=${settings.sim_commission:,.2f} por orden")
            for position in account.positions:
                print(
                    f"        {position.symbol:<6} {position.qty:>8g} @ "
                    f"{position.avg_entry_price:>8.2f}"
                )
        except Exception as exc:  # noqa: BLE001
            print(f"  FALLO  {exc}")
            failures.append("Broker simulado")
    else:
        _print_header("Alpaca")
        try:
            from src.broker import Broker

            broker = Broker(
                api_key=settings.alpaca_api_key,
                secret_key=settings.alpaca_secret_key,
                paper=settings.alpaca_paper,
            )
            account = broker.get_account_state()
            is_open = broker.is_market_open()
            print(f"  OK  modo={settings.mode}  "
                  f"mercado={'abierto' if is_open else 'cerrado'}")
            print(f"      equity=${account.equity:,.2f}  cash=${account.cash:,.2f}")
            print(f"      posiciones abiertas={len(account.positions)}")
            for position in account.positions:
                print(
                    f"        {position.symbol:<6} {position.qty:>8g} @ "
                    f"{position.avg_entry_price:>8.2f}  "
                    f"P&L {position.unrealized_pl:+.2f} "
                    f"({position.unrealized_pl_pct:+.2f}%)"
                )
        except Exception as exc:  # noqa: BLE001
            print(f"  FALLO  {exc}")
            failures.append("Alpaca")

    _print_header("NVIDIA NIM")
    try:
        with LLMClient(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
            model=settings.llm_model,
            temperature=0.0,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        ) as llm:
            response = llm.complete_json(
                system='Responde solo con JSON valido.',
                user='Devuelve exactamente {"ok": true, "modelo": "<tu nombre de modelo>"}.',
                max_tokens=200,
            )
        print(f"  OK  modelo={response.model}  latencia={response.latency_ms}ms")
        print(f"      respuesta={response.parsed}")
        print(f"      tokens: {response.prompt_tokens} entrada / "
              f"{response.completion_tokens} salida")
    except LLMError as exc:
        print(f"  FALLO  {exc}")
        print("      Revisa NVIDIA_API_KEY y que LLM_MODEL exista en build.nvidia.com.")
        failures.append("NVIDIA NIM")
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO  {exc}")
        failures.append("NVIDIA NIM")

    _print_header("Base de datos (SQLite)")
    try:
        from src.db import Database

        with Database(path=settings.db_path) as database:
            portfolio_id = database.ensure_portfolio(
                name=settings.portfolio_name,
                mode=settings.mode,
                initial_budget=settings.initial_budget,
            )
            open_positions = database.get_open_positions(portfolio_id)
            tables = database.query(
                "select count(*) as n from sqlite_master where type = 'table'"
            )
            cycles = database.query(
                "select count(*) as n from cycles where portfolio_id = ?", (portfolio_id,)
            )
        print(f"  OK  fichero={database.path}")
        print(f"      tablas={tables[0]['n']}  cartera={settings.portfolio_name}")
        print(f"      ciclos registrados={cycles[0]['n']}  "
              f"posiciones abiertas={len(open_positions)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO  {exc}")
        print("      Comprueba que DB_PATH apunta a una ruta escribible.")
        failures.append("Base de datos")

    print()
    if failures:
        print(f"Fallaron {len(failures)} comprobaciones: {', '.join(failures)}")
        return 1
    print("Todas las comprobaciones han pasado. Ya puedes ejecutar: python run.py cycle")
    return 0


def _show(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.2f}"
    return "n/d"


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------

def command_status(settings: Settings) -> int:
    """Estado de la cuenta.

    Con el broker simulado hace falta descargar precios para poder valorar la
    cartera: el simulador no tiene fuente de datos propia, usa los mismos precios
    que ve el analista.
    """
    from src.db import Database
    from src.market_data import build_market_data

    with Database(path=settings.db_path) as database:
        portfolio_id = database.ensure_portfolio(
            name=settings.portfolio_name, mode=settings.mode,
            initial_budget=settings.initial_budget,
        )
        tracked = database.get_open_positions(portfolio_id)

        if settings.broker == "sim":
            from src.sim_broker import Quote, SimBroker

            broker = SimBroker(
                database=database,
                portfolio_id=portfolio_id,
                initial_cash=settings.initial_budget,
                slippage_bps=settings.sim_slippage_bps,
                commission_per_order=settings.sim_commission,
            )
            held = broker.held_symbols()
            if held:
                # Se piden solo las posiciones abiertas: `status` no necesita
                # cribar el universo ni gastar peticiones en candidatos nuevos.
                from src.market_data import YahooMarketData

                provider = (
                    YahooMarketData(
                        watchlist=sorted(held),
                        lookback_days=settings.lookback_days,
                        interval=settings.bar_interval,
                    )
                    if settings.data_provider == "yahoo"
                    else build_market_data(settings, database)
                )
                snapshots = provider.fetch_snapshots(sorted(held))
                broker.set_quotes({
                    symbol: Quote(
                        fill_price=snapshot.execution_price,
                        mark_price=snapshot.price,
                        basis=snapshot.fill_basis,
                    )
                    for symbol, snapshot in snapshots.items()
                })
            account = broker.get_account_state()
        else:
            from src.broker import Broker

            account = Broker(
                api_key=settings.alpaca_api_key,
                secret_key=settings.alpaca_secret_key,
                paper=settings.alpaca_paper,
            ).get_account_state()

    _print_header(f"Cuenta ({settings.broker}, {settings.mode})")
    print(f"  Equity          ${account.equity:>14,.2f}")
    print(f"  Cash            ${account.cash:>14,.2f}")
    print(f"  En posiciones   ${account.positions_value:>14,.2f}")
    print(f"  P&L del dia     ${account.day_pnl:>+14,.2f}  ({account.day_pnl_pct:+.2f}%)")

    _print_header(f"Posiciones abiertas ({len(account.positions)})")
    if not account.positions:
        print("  (ninguna)")
    else:
        print(f"  {'SIMBOLO':<8}{'CANT':>7}{'ENTRADA':>10}{'ACTUAL':>10}"
              f"{'STOP':>10}{'OBJETIVO':>10}{'P&L':>12}")
        for position in account.positions:
            row = tracked.get(position.symbol, {})
            print(
                f"  {position.symbol:<8}{position.qty:>7g}"
                f"{position.avg_entry_price:>10.2f}{position.current_price:>10.2f}"
                f"{_show(row.get('stop_price')):>10}{_show(row.get('target_price')):>10}"
                f"{position.unrealized_pl:>+12.2f}"
            )
        untracked = account.open_symbols - set(tracked)
        if untracked:
            print(f"\n  Sin registro en la base de datos (se adoptaran en el proximo "
                  f"ciclo): {', '.join(sorted(untracked))}")
    return 0


# ----------------------------------------------------------------------
# report
# ----------------------------------------------------------------------

def command_report(dash: DashboardSettings) -> int:
    """Version en consola del dashboard.

    Reutiliza el mismo ensamblado de datos que la web (`build_dashboard`), asi
    que las dos vistas no pueden divergir. Abre la base en solo lectura y no
    necesita credenciales de broker ni de LLM.
    """
    from src.dashboard import build_dashboard
    from src.db import Database, DatabaseError

    try:
        with Database(path=dash.db_path, read_only=True) as database:
            data = build_dashboard(database, portfolio_name=dash.portfolio_name)
    except DatabaseError as exc:
        print(f"  {exc}")
        return 1

    if not data.get("portfolio"):
        print(f"  {data.get('message', 'Sin datos.')}")
        available = data.get("portfolios") or []
        if available:
            print("  Carteras disponibles: "
                  + ", ".join(p["name"] for p in available))
        return 0

    portfolio, summary = data["portfolio"], data["summary"]

    _print_header(f"Cartera: {portfolio['name']} ({portfolio['mode']})")
    print(f"  Presupuesto asignado       ${portfolio['initial_budget']:>13,.2f}")
    print(f"  Equity del primer ciclo    ${_or_zero(summary['equity_start']):>13,.2f}")
    print(f"  Equity actual              ${_or_zero(summary['equity']):>13,.2f}"
          f"   ({_signed_pct(summary['total_return_pct'])})")
    print(f"  Efectivo                   ${_or_zero(summary['cash']):>13,.2f}")
    print(f"  Ultimo ciclo               {str(summary['last_update'])[:19]}")

    _print_header("Resultados")
    print(f"  P&L realizado              ${summary['realized_pnl']:>+13,.2f}"
          f"   ({summary['closed_trades']} operaciones cerradas)")
    print(f"  P&L abierto                ${summary['unrealized_pnl']:>+13,.2f}"
          f"   ({summary['open_positions']} posiciones)")
    print(f"  Acierto                    {_pct(summary['win_rate_pct']):>14}"
          f"   ({summary['wins']} ganadoras / {summary['losses']} perdedoras)")
    print(f"  Profit factor              {_show(summary['profit_factor']):>14}"
          f"   (<1 = pierde dinero)")
    print(f"  Caida maxima               {_pct(summary['max_drawdown_pct']):>14}")

    _print_header("Actividad del modelo")
    print(f"  Ciclos ejecutados          {summary['cycles']:>14}")
    print(f"  Decisiones                 {summary['decisions']:>14}"
          f"   ({_pct(summary['buy_rate_pct'])} fueron compras)")
    print(f"  Conviccion media           {_show(summary['avg_conviction']):>14}")
    print(f"  Rechazos de riesgo         {summary['rejections']:>14}")
    print(f"  Ordenes                    {summary['orders']:>14}")
    print(f"  Tokens consumidos          {summary['tokens']:>14,}")

    _print_header("Ultimos ciclos")
    print(f"  {'INICIO':<20}{'ESTADO':<11}{'EQUITY':>12}{'CAMBIO':>10}"
          f"{'DEC':>5}{'APR':>5}{'REC':>5}  MERCADO")
    for row in data["cycles"][:12]:
        print(
            f"  {str(row['started_at'])[:19]:<20}{row['status']:<11}"
            f"{_or_zero(row['equity_end']):>12,.2f}"
            f"{_or_zero(row['equity_delta']):>+10.2f}"
            f"{row['decisions']:>5}{row['approved']:>5}{row['rejected']:>5}"
            f"  {'abierto' if row['market_open'] else 'cerrado'}"
        )

    _print_header("Posiciones abiertas")
    if not data["open_positions"]:
        print("  (ninguna)")
    else:
        print(f"  {'SIMBOLO':<9}{'CANT':>7}{'ENTRADA':>10}{'ULTIMO':>10}"
              f"{'STOP':>10}{'OBJETIVO':>10}{'P&L':>12}")
        for row in data["open_positions"]:
            print(
                f"  {row['symbol']:<9}{row['qty']:>7g}{row['entry_price']:>10.2f}"
                f"{_show(row['last_price']):>10}{_show(row['stop_price']):>10}"
                f"{_show(row['target_price']):>10}{_or_zero(row['unrealized_pnl']):>+12,.2f}"
            )

    _print_header("Rendimiento por simbolo (posiciones cerradas)")
    performance = data["performance_by_symbol"]
    if not performance:
        print("  (todavia no hay posiciones cerradas)")
    else:
        print(f"  {'SIMBOLO':<9}{'OPS':>5}{'ACIERTO':>9}{'P&L TOTAL':>12}"
              f"{'P&L MEDIO':>12}{'DIAS':>7}")
        for row in performance:
            print(
                f"  {row['symbol']:<9}{row['trades']:>5}"
                f"{_pct(row['win_rate_pct']):>9}{row['total_pnl']:>+12,.2f}"
                f"{row['avg_pnl']:>+12,.2f}{_show(row['avg_holding_days']):>7}"
            )
        total = sum(row["total_pnl"] for row in performance)
        trades = sum(row["trades"] for row in performance)
        print(f"  {'TOTAL':<9}{trades:>5}{'':>9}{total:>+12,.2f}")

    _print_header("Calibracion de la conviccion del modelo")
    print("  Si el acierto no sube con la conviccion, el modelo no aporta senal.")
    calibration = data["calibration"]
    if not calibration:
        print("  (hacen falta posiciones cerradas para medirlo)")
    else:
        print(f"  {'CONVICCION':<12}{'OPS':>5}{'ACIERTO':>9}{'P&L MEDIO':>12}")
        for row in calibration:
            bucket = f"{row['conviction_bucket']}-{row['conviction_bucket'] + 9}"
            print(
                f"  {bucket:<12}{row['trades']:>5}{_pct(row['win_rate_pct']):>9}"
                f"{_or_zero(row['avg_pnl']):>+12,.2f}"
            )

    _print_header("Rechazos del Risk Manager")
    if not data["rejections"]:
        print("  (ninguno)")
    else:
        for row in data["rejections"]:
            print(f"  {row['rule']:<28}{row['rejections']:>5}")

    print()
    print(f"  Base de datos: {dash.db_path}")
    print("  Dashboard web: python run.py serve")
    print(f"  Consultas libres: sqlite3 {dash.db_path}")
    return 0


def _or_zero(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _signed_pct(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:+.2f}%"
    return "n/d"


def _pct(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.1f}%"
    return "n/d"


# ----------------------------------------------------------------------
# cycle
# ----------------------------------------------------------------------

def command_cycle(settings: Settings) -> int:
    with LLMClient(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    ) as llm:
        cycle = TradingCycle.build(settings, llm)
        report = cycle.run()

    _print_header("Resumen del ciclo")
    print(report.summary())
    return 0 if report.status in {"completed", "halted"} else 1


# ----------------------------------------------------------------------

def command_serve(dash: DashboardSettings, *, host: str, port: int) -> int:
    from web.server import serve

    return serve(
        db_path=dash.db_path,
        portfolio_name=dash.portfolio_name,
        host=host,
        port=port,
    )


# ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="financial-bot",
        description="Agente de trading con analisis por LLM y control de riesgo determinista.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="check",
        choices=["check", "status", "cycle", "report", "serve"],
        help="check: diagnostico (por defecto). status: estado de la cuenta. "
             "cycle: ejecutar un ciclo. report: analitica en consola. "
             "serve: dashboard web.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analiza y registra en la base de datos pero no envia ordenes al broker.",
    )
    parser.add_argument("--env-file", default=None, help="Ruta a un .env alternativo.")
    parser.add_argument(
        "--port", type=int, default=8000, help="Puerto del dashboard (solo serve)."
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Interfaz del dashboard (solo serve). Por defecto solo local.",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        os.environ["DRY_RUN"] = "true"

    # `report` y `serve` solo leen la base: no se les exigen credenciales, para
    # poder revisar la operativa con el .env a medio rellenar.
    if args.command in {"report", "serve"}:
        dash = DashboardSettings.load(env_file=args.env_file)
        setup_logging((os.getenv("LOG_LEVEL") or "INFO").strip().upper())
        try:
            if args.command == "serve":
                return command_serve(dash, host=args.host, port=args.port)
            return command_report(dash)
        except KeyboardInterrupt:
            print("\nInterrumpido por el usuario.", file=sys.stderr)
            return 130

    try:
        settings = Settings.load(env_file=args.env_file)
    except ConfigError as exc:
        print(f"Error de configuracion: {exc}", file=sys.stderr)
        return 2

    setup_logging(settings.log_level)

    if not settings.alpaca_paper and args.command == "cycle" and not settings.dry_run:
        print("\n" + "!" * 70)
        print("  ALPACA_PAPER=false: este ciclo enviara ordenes con DINERO REAL.")
        print("!" * 70)
        answer = input("  Escribe 'CONFIRMO' para continuar: ").strip()
        if answer != "CONFIRMO":
            print("  Cancelado.")
            return 1

    handlers = {
        "check": command_check,
        "status": command_status,
        "cycle": command_cycle,
    }
    try:
        return handlers[args.command](settings)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
