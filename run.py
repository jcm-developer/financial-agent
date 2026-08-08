#!/usr/bin/env python
"""Punto de entrada del agente.

    python run.py check       Verifica configuracion y conectividad. Empieza aqui.
    python run.py status      Muestra el estado de la cuenta y las posiciones.
    python run.py cycle       Ejecuta un ciclo completo de analisis y operativa.
    python run.py report      Analitica del historico: P&L, calibracion, rechazos.
    python run.py serve       Dashboard web en http://127.0.0.1:8000
    python run.py profiles    Lista los perfiles de experimento.

Para empezar un experimento nuevo en una bolsa concreta:

    python run.py new-profile --name europa-01 --market eu
    python run.py activate --profile europa-01

El mercado del perfil (`eu` o `us`) fija horario, calendario de festivos y
divisa. Un perfil cubre una sola bolsa: no hay conversion de divisa en ninguna
parte del proyecto. Ver [src/market_calendar.py](src/market_calendar.py).

**Los parametros del agente viven en la base de datos, no en el `.env`** (F6.4):
cada perfil de experimento lleva los suyos en `agent_settings`. Del entorno solo
sale la infraestructura (`DB_PATH`, `NVIDIA_API_KEY`, `LOG_LEVEL`).

Si vienes de la version anterior, importa tu `.env` a un perfil una sola vez:

    python run.py import-profile --name experimento-01

Con varios perfiles activos, `--profile <nombre>` elige contra cual se opera.

`cycle` esta pensado para lanzarse desde el planificador una o dos veces al dia,
no en bucle continuo: los modelos gratuitos de NIM tienen limites de peticiones y
el horizonte del agente es de dias.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace

from src.config import ConfigError, DashboardSettings, Infra, Settings
from src.cycle import TradingCycle
from src.llm import LLMClient, LLMError


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


def _print_header(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


# ----------------------------------------------------------------------
# check
# ----------------------------------------------------------------------

def command_check(settings: Settings) -> int:
    """Comprueba las integraciones por separado para que un fallo diga
    exactamente que pieza esta mal configurada."""
    failures: list[str] = []

    _print_header("Configuracion")
    print(f"  {settings.describe()}")
    if settings.screener.enabled:
        # Se dice explicitamente: con embudo la watchlist no se usa, y verla
        # impresa hacia pensar lo contrario.
        print("  Watchlist ignorada: manda el fichero de universo.")
    else:
        print(f"  Watchlist: {', '.join(settings.watchlist)}")

    if settings.risk_summary:
        # Ya lleva los nueve limites y de donde salen, asi que no se repiten.
        print(f"  {settings.risk_summary}")
    else:
        risk = settings.risk
        print(
            f"  Riesgo: {risk.risk_per_trade_pct}% por operacion, "
            f"max {risk.max_position_pct}% por posicion, "
            f"max {risk.max_open_positions} posiciones, "
            f"kill switch a -{risk.max_daily_loss_pct}%"
        )
        print("  Parametros leidos del .env: todavia no hay perfil. Crealo con "
              "python run.py import-profile")

    from src import market_calendar

    mercado = market_calendar.get_market(settings.market)
    _print_header(f"Calendario de mercado -- {mercado.label}")

    print(f"  {market_calendar.describe(market=mercado)}")
    print(f"  Sesion {mercado.open_time:%H:%M}-{mercado.close_time:%H:%M} "
          f"hora local, divisa {mercado.currency}")
    # La ventana operativa solo se nombra cuando difiere de la sesion: repetir
    # las mismas horas dos veces seguidas solo invita a leerlas mal.
    if (mercado.warmup_minutes or mercado.drain_minutes):
        print(f"  Ventana operativa {mercado.operating_open:%H:%M}-"
              f"{mercado.operating_close:%H:%M}  "
              f"(+{mercado.warmup_minutes} min tras la apertura, "
              f"+{mercado.drain_minutes} tras el cierre)")
    allowed, reason = market_calendar.should_run(
        settings.bar_interval, market=mercado
    )
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
        print(f"  OK  fuente=Yahoo Finance (yfinance)  "
              f"intervalo={settings.bar_interval}")
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
        print("      Si el error viene de yfinance, prueba: pip install -U yfinance")
        failures.append("Datos de mercado")

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
        money = market_calendar.get_market(settings.market).currency_symbol
        print(f"      efectivo={money}{account.cash:,.2f}  "
              f"equity={money}{account.equity:,.2f}")
        print(f"      posiciones={len(account.positions)}  ejecuciones registradas={fills}")
        print(f"      deslizamiento={settings.sim_slippage_bps:.0f} pb  "
              f"comision={money}{settings.sim_commission:,.2f} por orden")
        for position in account.positions:
            print(
                f"        {position.symbol:<6} {position.qty:>8g} @ "
                f"{position.avg_entry_price:>8.2f}"
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
        print(f"  OK  modelo={response.model}  latencia={response.latency_ms}ms")
        print(f"      respuesta={response.parsed}")
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
    from src.sim_broker import Quote, SimBroker

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
            commission_per_order=settings.sim_commission,
        )
        held = broker.held_symbols()
        if held:
            # Se piden solo las posiciones abiertas: `status` no necesita cribar
            # el universo ni gastar peticiones en candidatos nuevos.
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

    from src import market_calendar

    mercado = market_calendar.get_market(settings.market)
    money = mercado.currency_symbol

    _print_header(
        f"Cuenta ({settings.portfolio_name}, {settings.mode}, {mercado.currency})"
    )
    print(f"  Equity          {money}{account.equity:>14,.2f}")
    print(f"  Cash            {money}{account.cash:>14,.2f}")
    print(f"  En posiciones   {money}{account.positions_value:>14,.2f}")
    print(f"  P&L del dia     {money}{account.day_pnl:>+14,.2f}  "
          f"({account.day_pnl_pct:+.2f}%)")

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

    from src import market_calendar

    # La divisa sale del perfil de la cartera. `report` no recibe `Settings`
    # -mirar el historico no debe exigir la clave del modelo-, asi que se
    # consulta aqui. Sin perfil (carteras anteriores a F1.4) se cae al default,
    # que es justo lo que esas carteras eran.
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
    print(f"  Presupuesto asignado       {money}{portfolio['initial_budget']:>13,.2f}")
    print(f"  Equity del primer ciclo    {money}{_or_zero(summary['equity_start']):>13,.2f}")
    print(f"  Equity actual              {money}{_or_zero(summary['equity']):>13,.2f}"
          f"   ({_signed_pct(summary['total_return_pct'])})")
    print(f"  Efectivo                   {money}{_or_zero(summary['cash']):>13,.2f}")
    print(f"  Ultimo ciclo               {str(summary['last_update'])[:19]}")

    _print_header("Resultados")
    print(f"  P&L realizado              {money}{summary['realized_pnl']:>+13,.2f}"
          f"   ({summary['closed_trades']} operaciones cerradas)")
    print(f"  P&L abierto                {money}{summary['unrealized_pnl']:>+13,.2f}"
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
        api_key=settings.model_api_key,
        provider=settings.llm_provider,
        base_url=settings.model_base_url,
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
            print("\n  No hay ningun perfil todavia.")
            print("  Importa el .env actual con:  "
                  "python run.py import-profile --name experimento-01")
            return 0

        _print_header(f"Perfiles ({len(profiles)})")
        for profile in profiles:
            settings = database.get_settings(profile["id"])
            symbols = database.get_profile_universe(profile["id"])
            universe = (
                settings["universe_file"] or f"{len(symbols)} simbolos propios"
            )
            # La divisa sale del mercado del perfil. Escribir '$' en un perfil
            # europeo invita a comparar dos presupuestos como si fueran la misma
            # unidad, y con dos experimentos en paralelo eso pasa solo.
            mercado = market_calendar.get_market(settings["market"])
            print(f"  {profile['name']}  [{profile['status']}]  "
                  f"mercado={mercado.code} ({mercado.currency})")
            print(f"      {describe(settings)}")
            # Con NVIDIA, una columna vacia no significa "sin clave": significa
            # que se usa NVIDIA_API_KEY del entorno. Decir "(sin clave)" ahi
            # mandaria a buscar un problema que no existe.
            sin_clave = (
                "(NVIDIA_API_KEY del entorno)"
                if settings["llm_provider"] == "nvidia" else "(sin clave)"
            )
            print(f"      modelo={settings['llm_provider']}/{settings['llm_model']}"
                  f"  clave={mask_secret(settings['llm_api_key'], empty=sin_clave)}")
            print(f"      universo={universe}  "
                  f"({len(symbols)} en vivo)  presupuesto="
                  f"{mercado.currency_symbol}{float(settings['initial_budget']):,.2f}")
    return 0


def command_import_profile(infra: Infra, *, name: str, env_file: str | None) -> int:
    """Crea un perfil que reproduce el `.env`. Puente de un solo uso hacia F6.4."""
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

    _print_header(f"Perfil {name or env_settings.portfolio_name!r} creado y activado")
    print(f"  {describe(settings)}")
    print("\n  Los limites de riesgo se han importado en MODO AVANZADO, con los")
    print("  numeros exactos que traia el .env, para no cambiar el comportamiento")
    print("  del agente al mover la configuracion de sitio. Para pasarte a los")
    print("  deslizadores de F6.5, apaga advanced_overrides.")
    print("\n  Ya puedes ejecutar:  python run.py cycle")
    return 0


#: Por encima de esto, seguir el universo entero minuto a minuto deja de ser
#: razonable: son peticiones a Yahoo por minuto desde una IP domestica (R2). El
#: S&P 500 cae de este lado; el europeo de 89, no.
MAX_UNIVERSO_SEGUIDO = 120


def command_new_profile(
    infra: Infra, *, name: str, market: str, watch: int, budget: float
) -> int:
    """Crea un perfil desde cero para un mercado, con su universo ya puesto.

    Existe porque `import-profile` solo sabe partir de un `.env`, y ese `.env`
    describe el experimento americano heredado. Sin este comando, montar un
    perfil europeo exigia abrir la base a mano.

    Deja el perfil en `draft`: activarlo es un paso aparte y explicito, igual que
    elegir contra que perfil se opera. Un perfil que naciera activo empezaria a
    consumir ingesta antes de que nadie hubiera revisado sus parametros.
    """
    from src import market_calendar
    from src.db import Database, DatabaseError
    from src.screener import load_universe

    try:
        mercado = market_calendar.get_market(market)
    except market_calendar.UnknownMarket as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2

    if not name:
        print("  new-profile necesita --name <nombre>.", file=sys.stderr)
        return 2

    try:
        universo = load_universe(mercado.universe_file)
    except Exception as exc:  # noqa: BLE001 - load_universe lanza tipos variados
        print(f"  No se pudo leer {mercado.universe_file}: {exc}", file=sys.stderr)
        return 1

    forasteros = mercado.foreign_symbols(universo)
    if forasteros:
        # Si el fichero del mercado trae simbolos de otra bolsa, el perfil no
        # llegaria ni a resolverse. Mejor decirlo aqui que en el primer ciclo.
        print(f"  {mercado.universe_file} tiene simbolos que no son de "
              f"{mercado.code}: {', '.join(forasteros[:8])}", file=sys.stderr)
        return 1

    seguidos = universo
    if watch > 0:
        seguidos = universo[:watch]
    elif len(universo) > MAX_UNIVERSO_SEGUIDO:
        print(
            f"  {mercado.universe_file} tiene {len(universo)} simbolos y el "
            f"ingestor pide uno por peticion cada minuto.\n"
            f"  Elige cuantos seguir en vivo:  --watch 50\n"
            f"  (el screener sigue cribando el universo entero para el ciclo)",
            file=sys.stderr,
        )
        return 2

    with Database(path=infra.db_path) as database:
        try:
            profile_id = database.create_profile(
                name=name,
                description=f"Mercado {mercado.code}: {mercado.label}",
                settings={
                    "market": mercado.code,
                    "benchmark": mercado.benchmark,
                    "universe_file": mercado.universe_file,
                    "initial_budget": budget,
                },
            )
            # El universo en vivo es lo que el ingestor sigue minuto a minuto;
            # `universe_file` es lo que criba el screener para el ciclo. Son dos
            # cosas distintas y por eso se rellenan las dos: un perfil con solo
            # fichero no aparece en `active_universe_by_market` y se queda sin
            # precios en vivo sin que nada lo diga.
            database.set_profile_universe(profile_id, seguidos)
        except DatabaseError as exc:
            print(f"  No se pudo crear el perfil: {exc}", file=sys.stderr)
            return 1

    _print_header(f"Perfil {name!r} creado en {mercado.label}")
    print(f"  Divisa: {mercado.currency}   "
          f"sesion {mercado.open_time:%H:%M}-{mercado.close_time:%H:%M} hora local")
    print(f"  Screener sobre {mercado.universe_file} ({len(universo)} simbolos)")
    print(f"  Ingesta en vivo de {len(seguidos)} simbolos")
    print(f"  Presupuesto inicial: {mercado.currency_symbol}{budget:,.2f}")
    print(f"  Benchmark: {mercado.benchmark}")
    if mercado.code == "eu":
        print("\n  Baja screener_min_dollar_volume a 5.000.000: el default de")
        print("  20 M esta pensado para el S&P 500 y aqui deja fuera 15 valores.")
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
    print(f"  Perfil {profile['name']!r} activado.")
    return 0


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
        choices=["check", "status", "cycle", "report", "serve",
                 "profiles", "new-profile", "import-profile", "activate"],
        help="check: diagnostico (por defecto). status: estado de la cuenta. "
             "cycle: ejecutar un ciclo. report: analitica en consola. "
             "serve: dashboard web. profiles: listar experimentos. "
             "new-profile: crear un perfil para un mercado. "
             "import-profile: crear un perfil a partir del .env. "
             "activate: marcar un perfil como activo.",
    )
    parser.add_argument(
        "--profile", default="",
        help="Nombre del perfil de experimento. Sin esto se usa el unico activo.",
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
        help="Cuantos simbolos del universo seguir minuto a minuto (solo "
             "new-profile). 0 = todos, permitido solo si el universo es "
             f"pequeno (<= {MAX_UNIVERSO_SEGUIDO}).",
    )
    parser.add_argument(
        "--budget", type=float, default=10_000.0,
        help="Capital inicial del perfil nuevo (solo new-profile).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analiza y registra en la base de datos pero no envia ordenes al broker. "
             "Solo para esta ejecucion; no toca el parametro del perfil.",
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

    infra = Infra.load(env_file=args.env_file)
    setup_logging(infra.log_level)

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
        print(f"Error de configuracion: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.", file=sys.stderr)
        return 130

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


def _settings_for(args, infra: Infra, *, allow_env_fallback: bool) -> Settings:
    """Resuelve los parametros del perfil. `check` puede caer al `.env`.

    Esa excepcion es deliberada: `check` es la herramienta de diagnostico y tiene
    que poder correr en una instalacion recien clonada, antes de que exista
    ningun perfil. `cycle` y `status`, en cambio, exigen perfil: operar con
    parametros que no quedan registrados en ningun sitio es justo lo que F6.4
    viene a arreglar.
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
