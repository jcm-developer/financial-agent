#!/usr/bin/env python
"""The agent's entry point.

    python run.py check       Verifies configuration and connectivity. Start here.
    python run.py status      Shows the account's state and its positions.
    python run.py cycle       Runs a full cycle of analysis and trading.
    python run.py report      Analytics of the history: P&L, calibration, rejections.
    python run.py api         REST API + interface at http://127.0.0.1:8000
    python run.py profiles    Lists the experiment profiles.

To start a new experiment on a specific exchange:

    python run.py new-profile --name europa-01 --market eu
    python run.py activate --profile europa-01

The profile's market (`eu` or `us`) fixes the hours, the holiday calendar and the
currency. One profile covers a single exchange: there is no currency conversion
anywhere in the project. See [src/market_calendar.py](src/market_calendar.py).

**The agent's parameters live in the database, not in the `.env`** (F6.4): each
experiment profile carries its own in `agent_settings`. Only the infrastructure
comes from the environment (`DB_PATH`, `NVIDIA_API_KEY`, `LOG_LEVEL`).

If you are coming from the previous version, import your `.env` into a profile
just once:

    python run.py import-profile --name experimento-01

With several active profiles, `--profile <name>` picks which one is traded.

`cycle` is meant to be launched by the scheduler once or twice a day, not in a
continuous loop: NIM's free models have request limits and the agent's horizon is
measured in days.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace

from src import cycle_log, fees
from src import formatting as fmt
from src.config import ConfigError, DashboardSettings, Infra, Settings
from src.cycle import TradingCycle
from src.llm import LLMClient, LLMError
#: The cap on symbols followed live lives in `profile_settings` because F3.3's
#: `POST /api/profiles` applies the same rule. Here it is only used for the
#: --help text.
from src.profile_settings import MAX_LIVE_SYMBOLS as MAX_LIVE_SYMBOLS


#: Commands whose output is mirrored into the shared log file, for the Ciclos
#: screen to show live (see `src/cycle_log.py`). They are the two that operate on
#: the book: the rest either read or configure, and nobody watches them from the
#: interface.
MIRRORED_COMMANDS = {"cycle", "close-experiment"}


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s  %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # httpx y sus dependencias son muy verbosos en DEBUG.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)


STATUS_LABELS = {
    "draft": "borrador",
    "active": "activo",
    "paused": "pausado",
    "archived": "archivado",
}

CYCLE_STATUS_LABELS = {
    "running": "en curso",
    "completed": "completado",
    "failed": "fallido",
    "halted": "detenido",
}


def _print_header(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


# ----------------------------------------------------------------------
# check
# ----------------------------------------------------------------------

def command_check(settings: Settings) -> int:
    """Checks the integrations separately so a failure says exactly which piece
    is misconfigured."""
    failures: list[str] = []

    _print_header("Configuración")
    print(f"  {settings.describe()}")
    if settings.screener.enabled:
        # Said explicitly: with the funnel the watchlist is not used, and seeing
        # it printed made people think otherwise.
        print("  Watchlist ignorada: manda el fichero de universo.")
    else:
        print(f"  Watchlist: {', '.join(settings.watchlist)}")

    if settings.risk_summary:
        # It already carries the nine limits and where they come from, so they are not repeated.
        print(f"  {settings.risk_summary}")
    else:
        risk = settings.risk
        print(
            f"  Riesgo: {fmt.percent(risk.risk_per_trade_pct)} por operación, "
            f"máximo del {fmt.percent(risk.max_position_pct)} por posición, "
            f"hasta {risk.max_open_positions} posiciones y sin operar el resto del "
            f"día si la cartera cae un {fmt.percent(risk.max_daily_loss_pct)}"
        )
        print("  Parámetros leídos del .env: todavía no hay perfil. Créalo con "
              "python run.py new-profile --market eu")

    from src import market_calendar

    market = market_calendar.get_market(settings.market)
    _print_header(f"Calendario de mercado: {market.label}")

    print(f"  {market_calendar.describe(market=market)}")
    print(f"  Sesión de {market.open_time:%H:%M} a {market.close_time:%H:%M}, "
          f"hora local, divisa {market.currency}")
    # The operating window is only named when it differs from the session:
    # repeating the same hours twice in a row only invites misreading them.
    if (market.warmup_minutes or market.drain_minutes):
        print(f"  Ventana operativa de {market.operating_open:%H:%M} a "
              f"{market.operating_close:%H:%M} "
              f"({market.warmup_minutes} min tras la apertura y "
              f"{market.drain_minutes} tras el cierre)")
    allowed, reason = market_calendar.should_run(
        settings.bar_interval, market=market
    )
    if allowed:
        print(f"  Un ciclo ahora sí se ejecutaría: {reason}")
    elif settings.skip_when_market_closed:
        print(f"  Un ciclo ahora se omitiría: {reason}")
        print("  Para forzarlo de todos modos: SKIP_WHEN_MARKET_CLOSED=false")
    else:
        print(f"  {reason}, pero SKIP_WHEN_MARKET_CLOSED=false: se ejecutaría.")

    _print_header("Datos de mercado")
    try:
        from src.market_data import YahooMarketData, build_market_data

        if settings.screener.enabled:
            # With the funnel the whole universe is not tested: that would mean
            # downloading 500 symbols just to diagnose. Three are probed instead.
            from src.screener import load_universe

            universe = load_universe(settings.screener.universe_file)
            print(f"  Universo: {len(universe)} símbolos en "
                  f"{settings.screener.universe_file}")
            print(f"  Embudo: los {settings.screener.top_n} mejores según "
                  f"«{settings.screener.mode}» pasan al modelo")
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
                "Comprueba la conexión y que los símbolos existan."
            )
        print(f"  OK  fuente: Yahoo Finance (yfinance), "
              f"barras de {settings.bar_interval}")
        print(f"      {'ACTIVO':<8}{'DECISIÓN':>10}{'EJECUCIÓN':>11}"
              f"{'RSI':>7}{'ATR':>8}{'BARRAS':>8}  SESIÓN")
        for symbol, snapshot in snapshots.items():
            indicators = snapshot.indicators
            print(
                f"      {symbol:<8}{fmt.number(snapshot.price):>10}"
                f"{fmt.number(snapshot.execution_price):>11}"
                f"{_show(indicators.get('rsi_14')):>7}"
                f"{_show(indicators.get('atr_14')):>8}"
                f"{indicators.get('bars_available'):>8}  {snapshot.session or 'n/d'}"
            )
        unit = "sesión" if settings.bar_interval == "1d" else "hora"
        print(f"      DECISIÓN = cierre de la última {unit} completa (lo que ve el")
        print(f"      analista). EJECUCIÓN = apertura de la {unit} siguiente,")
        print("      donde se opera. Que sean distintos es lo que evita operar")
        print("      con información del futuro.")
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO  {exc}")
        print("      Si el error viene de yfinance, prueba: pip install -U yfinance")
        failures.append("Datos de mercado")

    _print_header("Broker simulado")
    try:
        from src.db import Database
        from src.sim_broker import SimBroker

        money = market_calendar.get_market(settings.market).currency_symbol

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
                extra_commission=settings.sim_commission,
                currency_symbol=money,
            )
            account = broker.get_account_state()
            fills = database.query(
                "select count(*) as n from sim_fills where account_id = ?",
                (portfolio_id,),
            )[0]["n"]

        print(f"  OK  sin cuenta de broker: la contabilidad es local")
        print(f"      efectivo: {fmt.money(account.cash, money)}, "
              f"valor de la cartera: {fmt.money(account.equity, money)}")
        print(f"      posiciones: {len(account.positions)}, ejecuciones registradas: {fills}")
        # The tariff is per leg and depends on the exchange, so a single number
        # would be wrong for a European profile holding Spanish names at 4,11
        # and the rest at 3,00. It is printed grouped, dearest first.
        groups = sorted(fees.tariffs_for_market(settings.market).items(), reverse=True)
        standard = "  ".join(
            fmt.money(amount, money)
            + (f" ({', '.join(suffixes)})" if 0 < len(suffixes) <= 2
               else " (el resto)" if suffixes else "")
            for amount, suffixes in groups
        )
        print(f"      deslizamiento de {settings.sim_slippage_bps:.0f} pb; "
              f"comisión estándar por orden y por lado: {standard}")
        if settings.sim_commission:
            print(f"      recargo del perfil: +{fmt.money(settings.sim_commission, money)} "
                  f"por orden, sobre la tarifa")
        for position in account.positions:
            print(
                f"        {position.symbol:<6} {position.qty:>8g} @ "
                f"{fmt.number(position.avg_entry_price):>8}"
            )
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO  {exc}")
        failures.append("Broker simulado")

    from src.llm import resolve_provider

    _print_header(f"Modelo ({resolve_provider(settings.llm_provider).label})")
    try:
        with LLMClient(
            api_key=settings.model_api_key,
            provider=settings.llm_provider,
            base_url=settings.model_base_url,
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
        print(f"  OK  modelo: {response.model}, latencia: {response.latency_ms} ms")
        print(f"      respuesta: {response.parsed}")
        print(f"      tokens: {response.prompt_tokens} entrada / "
              f"{response.completion_tokens} salida")
    except LLMError as exc:
        print(f"  FALLO  {exc}")
        print("      Revisa la clave del perfil (llm_api_key) y que el modelo exista")
        print("      en el proveedor elegido.")
        failures.append("Modelo")
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO  {exc}")
        failures.append("Modelo")

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
        print(f"  OK  fichero: {database.path}")
        print(f"      tablas: {tables[0]['n']}, cartera: {settings.portfolio_name}")
        print(f"      ciclos registrados: {cycles[0]['n']}, "
              f"posiciones abiertas: {len(open_positions)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FALLO  {exc}")
        print("      Comprueba que DB_PATH apunta a una ruta escribible.")
        failures.append("Base de datos")

    print()
    if failures:
        print(("Falló 1 comprobación" if len(failures) == 1
               else f"Fallaron {len(failures)} comprobaciones")
              + f": {', '.join(failures)}")
        return 1
    print("Todas las comprobaciones han pasado. Ya puedes ejecutar: python run.py cycle")
    return 0


def _show(value: object) -> str:
    if isinstance(value, (int, float)):
        return fmt.number(value)
    return "n/d"


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------

def command_status(settings: Settings) -> int:
    """Account state.

    With the simulated broker, prices have to be downloaded before the book can
    be valued: the simulator has no data source of its own, it uses the same
    prices the analyst sees.
    """
    from src import market_calendar
    from src.db import Database
    from src.sim_broker import Quote, SimBroker

    market = market_calendar.get_market(settings.market)
    money = market.currency_symbol

    with Database(path=settings.db_path) as database:
        portfolio_id = database.ensure_portfolio(
            name=settings.portfolio_name, mode=settings.mode,
            initial_budget=settings.initial_budget,
        )
        tracked = database.get_open_positions(portfolio_id)

        broker = SimBroker(
            database=database,
            portfolio_id=portfolio_id,
            initial_cash=settings.initial_budget,
            slippage_bps=settings.sim_slippage_bps,
            extra_commission=settings.sim_commission,
            currency_symbol=money,
        )
        held = broker.held_symbols()
        if held:
            # Only the open positions are requested: `status` needs neither to
            # sift the universe nor to spend requests on new candidates.
            from src.market_data import YahooMarketData

            snapshots = YahooMarketData(
                watchlist=sorted(held),
                lookback_days=settings.lookback_days,
                interval=settings.bar_interval,
            ).fetch_snapshots(sorted(held))
            broker.set_quotes({
                symbol: Quote(
                    fill_price=snapshot.execution_price,
                    mark_price=snapshot.price,
                    basis=snapshot.fill_basis,
                )
                for symbol, snapshot in snapshots.items()
            })
        account = broker.get_account_state()

    _print_header(
        f"Cuenta ({settings.portfolio_name}, {settings.mode}, {market.currency})"
    )
    print(f"  Valor total     {fmt.money(account.equity, money):>16}")
    print(f"  Efectivo        {fmt.money(account.cash, money):>16}")
    print(f"  En posiciones   {fmt.money(account.positions_value, money):>16}")
    print(f"  Resultado hoy   {fmt.signed_money(account.day_pnl, money):>16}  "
          f"({fmt.percent(account.day_pnl_pct, signed=True)})")

    _print_header(f"Posiciones abiertas ({len(account.positions)})")
    if not account.positions:
        print("  (ninguna)")
    else:
        print(f"  {'SÍMBOLO':<8}{'CANT.':>7}{'ENTRADA':>10}{'ACTUAL':>10}"
              f"{'STOP':>10}{'OBJETIVO':>10}{'RESULTADO':>12}")
        for position in account.positions:
            row = tracked.get(position.symbol, {})
            print(
                f"  {position.symbol:<8}{position.qty:>7g}"
                f"{fmt.number(position.avg_entry_price):>10}"
                f"{fmt.number(position.current_price):>10}"
                f"{_show(row.get('stop_price')):>10}{_show(row.get('target_price')):>10}"
                f"{fmt.signed_money(position.unrealized_pl, ''):>12}"
            )
        untracked = account.open_symbols - set(tracked)
        if untracked:
            print(f"\n  Sin registro en la base de datos (se adoptarán en el próximo "
                  f"ciclo): {', '.join(sorted(untracked))}")
    return 0


# ----------------------------------------------------------------------
# report
# ----------------------------------------------------------------------

def command_report(dash: DashboardSettings) -> int:
    """Console version of the dashboard.

    It reuses the same data assembly as the web (`build_dashboard`), so the two
    views cannot diverge. It opens the database read-only and needs neither
    broker nor LLM credentials.
    """
    from src.dashboard import build_dashboard
    from src.db import Database, DatabaseError

    from src import market_calendar

    # The currency comes from the book's profile. `report` receives no `Settings`
    # -looking at the history must not demand the model key-, so it is looked up
    # here. With no profile (books predating F1.4) it falls back to the default,
    # which is exactly what those books were.
    money = market_calendar.get_market().currency_symbol
    try:
        with Database(path=dash.db_path, read_only=True) as database:
            data = build_dashboard(database, portfolio_name=dash.portfolio_name)
            portfolio_row = data.get("portfolio") or {}
            if portfolio_row.get("id"):
                rows = database.query(
                    "select s.market as market from portfolios p "
                    "  join agent_settings s on s.profile_id = p.profile_id "
                    " where p.id = ?",
                    (portfolio_row["id"],),
                )
                if rows:
                    money = market_calendar.get_market(
                        rows[0]["market"]
                    ).currency_symbol
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
    print(f"  Presupuesto asignado       {fmt.money(portfolio['initial_budget'], money):>16}")
    print(f"  Valor en el primer ciclo   {fmt.money(_or_zero(summary['equity_start']), money):>16}")
    print(f"  Valor actual               {fmt.money(_or_zero(summary['equity']), money):>16}"
          f"   ({_signed_pct(summary['total_return_pct'])})")
    print(f"  Efectivo                   {fmt.money(_or_zero(summary['cash']), money):>16}")
    print(f"  Último ciclo               {str(summary['last_update'])[:19].replace('T', ' ')}")

    _print_header("Resultados")
    print(f"  Resultado realizado        {fmt.signed_money(summary['realized_pnl'], money):>16}"
          f"   ({summary['closed_trades']} operaciones cerradas)")
    print(f"  Resultado abierto          {fmt.signed_money(summary['unrealized_pnl'], money):>16}"
          f"   ({summary['open_positions']} posiciones)")
    print(f"  Acierto                    {_pct(summary['win_rate_pct']):>16}"
          f"   ({summary['wins']} ganadoras y {summary['losses']} perdedoras)")
    print(f"  Factor de beneficio        {_show(summary['profit_factor']):>16}"
          f"   (por debajo de 1 pierde dinero)")
    print(f"  Caída máxima               {_pct(summary['max_drawdown_pct']):>16}")

    _print_header("Actividad del modelo")
    print(f"  Ciclos ejecutados          {summary['cycles']:>16}")
    print(f"  Decisiones                 {summary['decisions']:>16}"
          f"   ({_pct(summary['buy_rate_pct'])} fueron compras)")
    print(f"  Convicción media           {_show(summary['avg_conviction']):>16}")
    print(f"  Rechazos de riesgo         {summary['rejections']:>16}")
    print(f"  Órdenes                    {summary['orders']:>16}")
    print(f"  Tokens consumidos          {fmt.number(summary['tokens'] or 0, 0):>16}")

    _print_header("Últimos ciclos")
    print(f"  {'INICIO':<20}{'ESTADO':<12}{'VALOR':>12}{'CAMBIO':>10}"
          f"{'DEC':>5}{'APR':>5}{'REC':>5}  MERCADO")
    for row in data["cycles"][:12]:
        print(
            f"  {str(row['started_at'])[:19].replace('T', ' '):<20}"
            f"{CYCLE_STATUS_LABELS.get(row['status'], row['status']):<12}"
            f"{fmt.number(_or_zero(row['equity_end'])):>12}"
            f"{fmt.signed_money(_or_zero(row['equity_delta']), ''):>10}"
            f"{row['decisions']:>5}{row['approved']:>5}{row['rejected']:>5}"
            f"  {'abierto' if row['market_open'] else 'cerrado'}"
        )

    _print_header("Posiciones abiertas")
    if not data["open_positions"]:
        print("  (ninguna)")
    else:
        print(f"  {'SÍMBOLO':<9}{'CANT.':>7}{'ENTRADA':>10}{'ÚLTIMO':>10}"
              f"{'STOP':>10}{'OBJETIVO':>10}{'RESULTADO':>12}")
        for row in data["open_positions"]:
            print(
                f"  {row['symbol']:<9}{row['qty']:>7g}{fmt.number(row['entry_price']):>10}"
                f"{_show(row['last_price']):>10}{_show(row['stop_price']):>10}"
                f"{_show(row['target_price']):>10}"
                f"{fmt.signed_money(_or_zero(row['unrealized_pnl']), ''):>12}"
            )

    _print_header("Rendimiento por símbolo (posiciones cerradas)")
    performance = data["performance_by_symbol"]
    if not performance:
        print("  (todavía no hay posiciones cerradas)")
    else:
        print(f"  {'SÍMBOLO':<9}{'OPS.':>5}{'ACIERTO':>9}{'TOTAL':>12}"
              f"{'MEDIO':>12}{'DÍAS':>7}")
        for row in performance:
            print(
                f"  {row['symbol']:<9}{row['trades']:>5}"
                f"{_pct(row['win_rate_pct']):>9}"
                f"{fmt.signed_money(row['total_pnl'], ''):>12}"
                f"{fmt.signed_money(row['avg_pnl'], ''):>12}"
                f"{_show(row['avg_holding_days']):>7}"
            )
        total = sum(row["total_pnl"] for row in performance)
        trades = sum(row["trades"] for row in performance)
        print(f"  {'TOTAL':<9}{trades:>5}{'':>9}{fmt.signed_money(total, ''):>12}")

    _print_header("Calibración de la convicción del modelo")
    print("  Si el acierto no sube con la convicción, el modelo no aporta señal.")
    calibration = data["calibration"]
    if not calibration:
        print("  (hacen falta posiciones cerradas para medirlo)")
    else:
        print(f"  {'CONVICCIÓN':<12}{'OPS.':>5}{'ACIERTO':>9}{'MEDIO':>12}")
        for row in calibration:
            bucket = f"{row['conviction_bucket']}-{row['conviction_bucket'] + 9}"
            print(
                f"  {bucket:<12}{row['trades']:>5}{_pct(row['win_rate_pct']):>9}"
                f"{fmt.signed_money(_or_zero(row['avg_pnl']), ''):>12}"
            )

    _print_header("Rechazos del control de riesgo")
    if not data["rejections"]:
        print("  (ninguno)")
    else:
        for row in data["rejections"]:
            print(f"  {row['rule']:<28}{row['rejections']:>5}")

    print()
    print(f"  Base de datos: {dash.db_path}")
    print("  Interfaz: python run.py api")
    print(f"  Consultas libres: sqlite3 {dash.db_path}")
    return 0


def _or_zero(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _signed_pct(value: object) -> str:
    if isinstance(value, (int, float)):
        return fmt.percent(value, signed=True)
    return "n/d"


def _pct(value: object) -> str:
    if isinstance(value, (int, float)):
        return fmt.percent(value, 1)
    return "n/d"


# ----------------------------------------------------------------------
# cycle
# ----------------------------------------------------------------------

def command_cycle(settings: Settings) -> int:
    with LLMClient(
        api_key=settings.model_api_key,
        provider=settings.llm_provider,
        base_url=settings.model_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
        reasoning_effort=settings.llm_reasoning_effort,
    ) as llm:
        cycle = TradingCycle.build(settings, llm)
        report = cycle.run()

    _print_header("Resumen del ciclo")
    print(report.summary())
    return 0 if report.status in {"completed", "halted"} else 1


def command_close_experiment(settings: Settings) -> int:
    """Liquidates the book to end an experiment (F5.8).

    **The model is not consulted**, so no client is opened and no quota is
    spent: this is not a decision about the market, it is the end of the
    experiment. `TradingCycle.build` needs an `LLMClient` to assemble itself, so
    one is built with the profile's settings and simply never used.
    """
    with LLMClient(
        api_key=settings.model_api_key,
        provider=settings.llm_provider,
        base_url=settings.model_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_tokens,
        reasoning_effort=settings.llm_reasoning_effort,
    ) as llm:
        cycle = TradingCycle.build(settings, llm)
        report = cycle.close_all_positions()

    _print_header("Cierre del experimento")
    if report.status == "skipped":
        print(f"  {report.halted_reason}")
        # Not an error: "there was nothing to close" and "it could not be closed"
        # both leave the caller with nothing to do, and neither is a failure of
        # the command.
        return 0

    print(f"  Posiciones liquidadas: {report.exits_forced}")
    print(f"  Capital: de {fmt.number(report.equity_start)} a {fmt.number(report.equity_end)}")
    for error in report.errors:
        print(f"  Error: {error}", file=sys.stderr)
    return 0 if report.status == "completed" else 1


def command_check_stops(settings: Settings) -> int:
    """Checks stops and targets without the model (F9.36). The scheduler runs it
    every hour between cycles.

    Not in `MIRRORED_COMMANDS`: the shared log file is the Ciclos screen's live
    view, and seven checks a day would overwrite the analysing cycle's log with
    "nothing hit". What an exit does leave is a cycle in the history.

    Like `close-experiment`, `TradingCycle.build` needs an `LLMClient`; one is
    built and never called.
    """
    with LLMClient(
        api_key=settings.model_api_key,
        provider=settings.llm_provider,
        base_url=settings.model_base_url,
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
    ) as llm:
        cycle = TradingCycle.build(settings, llm)
        report = cycle.check_stops()

    if report.status == "skipped":
        print(f"  {report.halted_reason}")
        return 0
    print(f"  Salidas obligatorias: {report.exits_forced}")
    for error in report.errors:
        print(f"  Error: {error}", file=sys.stderr)
    return 0 if report.status == "completed" else 1


# ----------------------------------------------------------------------

def command_api(dash: DashboardSettings, *, host: str, port: int) -> int:
    """F3's API, which also serves the React build from `app/dist` (F3.7).

    It is the only interface since F4.11: the `serve` that used to bring up the
    `web/index.html` dashboard was retired with it, so no two screens would be
    left fighting over port 8000 and counting the same experiment two ways.
    """
    from api.main import serve as serve_api

    return serve_api(host=host, port=port, db_path=dash.db_path)


# ----------------------------------------------------------------------
# Perfiles de experimento
# ----------------------------------------------------------------------

def command_profiles(infra: Infra) -> int:
    from src import market_calendar
    from src.db import Database
    from src.profile_settings import mask_secret
    from src.risk_presets import describe

    with Database(path=infra.db_path) as database:
        profiles = database.list_profiles(include_archived=True)
        if not profiles:
            print("\n  Todavía no hay ningún perfil.")
            print("  Crea uno con:  "
                  "python run.py new-profile --name europa-01 --market eu --watch 89")
            print("  Si vienes de un .env de la versión anterior:  "
                  "python run.py import-profile --name experimento-01")
            return 0

        _print_header(f"Perfiles ({len(profiles)})")
        for profile in profiles:
            settings = database.get_settings(profile["id"])
            symbols = database.get_profile_universe(profile["id"])
            universe = (
                settings["universe_file"] or f"{len(symbols)} símbolos propios"
            )
            # The currency comes from the profile's market. Writing '$' in a
            # European profile invites comparing two budgets as if they were the
            # same unit, and with two experiments in parallel that happens by itself.
            market = market_calendar.get_market(settings["market"])
            print(f"  {profile['name']}  [{STATUS_LABELS.get(profile['status'], profile['status'])}]  "
                  f"mercado {market.code} ({market.currency})")
            print(f"      {describe(settings)}")
            # With NVIDIA, an empty column does not mean "no key": it means
            # NVIDIA_API_KEY from the environment is used. Saying "(sin clave)"
            # there would send someone hunting for a problem that does not exist.
            sin_clave = (
                "(NVIDIA_API_KEY del entorno)"
                if settings["llm_provider"] == "nvidia" else "(sin clave)"
            )
            print(f"      modelo: {settings['llm_provider']}/{settings['llm_model']}, "
                  f"clave: {mask_secret(settings['llm_api_key'], empty=sin_clave)}")
            print(f"      universo: {universe} ({len(symbols)} en vivo), presupuesto: "
                  f"{fmt.money(float(settings['initial_budget']), market.currency_symbol)}")
    return 0


def command_import_profile(infra: Infra, *, name: str, env_file: str | None) -> int:
    """Creates a profile reproducing the `.env`. A single-use bridge to F6.4."""
    from src.db import Database, DatabaseError
    from src.profile_settings import import_env_profile

    try:
        env_settings = Settings.load(env_file=env_file)
    except ConfigError as exc:
        print(f"No se pudo leer el .env: {exc}", file=sys.stderr)
        return 2

    with Database(path=infra.db_path) as database:
        try:
            profile_id = import_env_profile(database, env_settings, name=name)
        except DatabaseError as exc:
            print(f"No se pudo crear el perfil: {exc}", file=sys.stderr)
            return 1
        settings = database.get_settings(profile_id)

    from src.risk_presets import describe

    _print_header(f"Perfil «{name or env_settings.portfolio_name}» creado y activado")
    print(f"  {describe(settings)}")
    print("\n  Los límites de riesgo se han importado en modo avanzado, con los")
    print("  números exactos que traía el .env, para no cambiar el comportamiento")
    print("  del agente al mover la configuración de sitio. Para pasarte a los")
    print("  deslizadores, apaga advanced_overrides.")
    print("\n  Ya puedes ejecutar:  python run.py cycle")
    return 0


def command_new_profile(
    infra: Infra, *, name: str, market: str, watch: int, budget: float
) -> int:
    """Creates a profile from scratch for a market, with its universe in place.

    It exists because `import-profile` only knows how to start from a `.env`, and
    that `.env` describes the inherited American experiment. Without this command,
    setting up a European profile meant opening the database by hand.

    The logic lives in `src/profile_settings.create_market_profile`, shared with
    F3.3's `POST /api/profiles`: only the printing and the exit codes are left
    here.
    """
    from src.db import Database, DatabaseError
    from src.profile_settings import UniverseError, create_market_profile

    with Database(path=infra.db_path) as database:
        try:
            created = create_market_profile(
                database, name=name, market=market, watch=watch, budget=budget
            )
        except ConfigError as exc:
            # "Pick something else": unknown market, no name, universe too large
            # to follow whole.
            print(f"  {exc}", file=sys.stderr)
            return 2
        except UniverseError as exc:
            # The repository's file is wrong; it is not a choice of the user's.
            print(f"  {exc}", file=sys.stderr)
            return 1
        except DatabaseError as exc:
            print(f"  No se pudo crear el perfil: {exc}", file=sys.stderr)
            return 1

    market = created.market

    _print_header(f"Perfil «{name}» creado en {market.label}")
    print(f"  Divisa: {market.currency}; "
          f"sesión de {market.open_time:%H:%M} a {market.close_time:%H:%M}, hora local")
    print(f"  Criba sobre {market.universe_file} "
          f"({created.universe_size} símbolos)")
    print(f"  Precios en vivo de {created.watched} símbolos")
    print(f"  Presupuesto inicial: {fmt.money(budget, market.currency_symbol)}")
    print(f"  Índice de referencia: {market.benchmark}")
    # The figure is in the market's currency despite the column's name (F8.7):
    # showing it with its symbol stops it being read as dollars.
    print(f"  Liquidez mínima para entrar en la criba: "
          f"{fmt.money(market.min_turnover, market.currency_symbol, 0)} al día")
    print(f"\n  Actívalo cuando lo tengas revisado:  "
          f"python run.py activate --profile {name}")
    return 0


def command_activate(infra: Infra, *, name: str) -> int:
    from src.db import Database
    from src.profile_settings import select_profile

    with Database(path=infra.db_path) as database:
        profile_id = select_profile(database, name=name)
        database.set_profile_status(profile_id, "active")
        profile = database.get_profile(profile_id)
    print(f"  Perfil «{profile['name']}» activado.")
    return 0


# ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    from src.formatting import utf8_console

    utf8_console()
    parser = argparse.ArgumentParser(
        prog="financial-agent",
        description="Agente de trading con análisis por LLM y control de riesgo determinista.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="check",
        choices=["check", "status", "cycle", "close-experiment", "check-stops", "report", "api",
                 "profiles", "new-profile", "import-profile", "activate"],
        help="check: diagnóstico (por defecto). status: estado de la cuenta. "
             "cycle: ejecutar un ciclo. "
             "close-experiment: vender todas las posiciones y cerrar el "
             "experimento. check-stops: comprobar stops y objetivos sin "
             "consultar al modelo. report: analítica en consola. "
             "api: API REST + interfaz web. "
             "profiles: listar experimentos. "
             "new-profile: crear un perfil para un mercado. "
             "import-profile: crear un perfil a partir del .env. "
             "activate: marcar un perfil como activo.",
    )
    parser.add_argument(
        "--profile", default="",
        help="Nombre del perfil de experimento. Sin esto se usa el único activo.",
    )
    parser.add_argument(
        "--name", default="",
        help="Nombre del perfil a crear (new-profile e import-profile). En "
             "import-profile, por defecto PORTFOLIO_NAME del .env.",
    )
    parser.add_argument(
        "--market", default="eu",
        help="Bolsa del perfil nuevo (solo new-profile): eu o us.",
    )
    parser.add_argument(
        "--watch", type=int, default=0,
        help="Cuántos símbolos del universo seguir minuto a minuto (solo "
             "new-profile). 0 = todos, permitido solo si el universo es "
             f"pequeño (hasta {MAX_LIVE_SYMBOLS}).",
    )
    parser.add_argument(
        "--budget", type=float, default=10_000.0,
        help="Capital inicial del perfil nuevo (solo new-profile).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analiza y registra en la base de datos, pero no envía órdenes al broker. "
             "Solo para esta ejecución; no toca el parámetro del perfil.",
    )
    parser.add_argument("--env-file", default=None, help="Ruta a un .env alternativo.")
    parser.add_argument(
        "--port", type=int, default=8000, help="Puerto de la API (solo api)."
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Interfaz de escucha de la API (solo api). Por defecto solo local.",
    )
    args = parser.parse_args(argv)

    # `report` and `api` only read the database -or write configuration-: no
    # credentials are demanded of them, so the trading can be reviewed with the
    # .env half filled in.
    if args.command in {"report", "api"}:
        dash = DashboardSettings.load(env_file=args.env_file)
        setup_logging((os.getenv("LOG_LEVEL") or "INFO").strip().upper())
        try:
            if args.command == "api":
                return command_api(dash, host=args.host, port=args.port)
            return command_report(dash)
        except KeyboardInterrupt:
            print("\nInterrumpido por el usuario.", file=sys.stderr)
            return 130

    infra = Infra.load(env_file=args.env_file)

    # The mirror wraps `setup_logging` and not just the command, and that order is
    # the whole trick: `basicConfig` keeps the stream it is handed, so installing
    # the mirror afterwards would leave every log line out of the file and only
    # the `print`s in it.
    if args.command in MIRRORED_COMMANDS:
        with cycle_log.capture(infra.db_path):
            setup_logging(infra.log_level)
            return _dispatch(args, infra)

    setup_logging(infra.log_level)
    return _dispatch(args, infra)


def _dispatch(args, infra: Infra) -> int:
    """Runs the command. Split out of `main` so the mirror can wrap the whole of
    it —logging included— without indenting the dispatch inside a `with`."""
    try:
        if args.command == "profiles":
            return command_profiles(infra)
        if args.command == "new-profile":
            return command_new_profile(
                infra, name=args.name, market=args.market,
                watch=args.watch, budget=args.budget,
            )
        if args.command == "import-profile":
            return command_import_profile(
                infra, name=args.name or args.profile, env_file=args.env_file
            )
        if args.command == "activate":
            target = args.profile or args.name
            if not target:
                print("activate necesita --profile <nombre>.", file=sys.stderr)
                return 2
            return command_activate(infra, name=target)

        settings = _settings_for(args, infra, allow_env_fallback=args.command == "check")
    except ConfigError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        return 130

    handlers = {
        "check": command_check,
        "status": command_status,
        "cycle": command_cycle,
        "close-experiment": command_close_experiment,
        "check-stops": command_check_stops,
    }
    try:
        return handlers[args.command](settings)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        return 130


def _settings_for(args, infra: Infra, *, allow_env_fallback: bool) -> Settings:
    """Resolves the profile's parameters. `check` may fall back to the `.env`.

    That exception is deliberate: `check` is the diagnostic tool and has to be
    able to run on a freshly cloned install, before any profile exists. `cycle`
    and `status`, by contrast, demand a profile: trading with parameters that are
    recorded nowhere is precisely what F6.4 came to fix.
    """
    from src.profile_settings import load_for_cycle

    try:
        _, settings = load_for_cycle(infra, profile_name=args.profile)
    except ConfigError:
        if not allow_env_fallback:
            raise
        settings = Settings.load(env_file=args.env_file)

    if args.dry_run:
        settings = replace(settings, dry_run=True)
    return settings


if __name__ == "__main__":
    sys.exit(main())
