"""Consultas de lectura de la API.

Viven aparte de `src/dashboard.py` porque responden a otra pregunta. El
dashboard arma **un** payload completo de una cartera en un solo viaje, que es lo
que necesita una pantalla que se pinta entera de golpe. Estos endpoints son
listas que se filtran, se ordenan y se paginan, que es lo que necesita una tabla
con 480 decisiones y un buscador encima.

Se sirven de la conexion en **solo lectura** (ver `deps.py`): mirar el historico
no puede alterarlo, y aqui eso lo garantiza el modo `ro` de SQLite, no la buena
educacion de estas funciones.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from src import market_calendar, risk_presets
from src.db import Database
from src.profile_settings import mask_secret

#: Tope de filas por pagina. No es paranoia: `decisions` guarda la respuesta
#: cruda del modelo, asi que unos pocos miles de filas son megabytes de JSON
#: viajando a un navegador que solo va a pintar veinte.
MAX_LIMIT = 500


def _loads(raw: Any, *, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _count(db: Database, sql: str, params: tuple) -> int:
    return int(db.query(f"select count(1) as n from {sql}", params)[0]["n"])


def clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


# ----------------------------------------------------------------------
# Mercados
# ----------------------------------------------------------------------

def market_info(code: str) -> dict[str, Any]:
    """Todo lo que la interfaz necesita saber de una bolsa, ya resuelto.

    Incluye si esta abierta **ahora**, y por eso no se cachea: el frontend lo
    consulta para decidir si tiene sentido ofrecer el boton de lanzar un ciclo.
    """
    from src.screener import load_universe

    mercado = market_calendar.get_market(code)
    hoy = market_calendar.now_local(mercado).date()
    try:
        universe_size = len(load_universe(mercado.universe_file))
    except Exception:  # noqa: BLE001 - un universo ilegible no debe tumbar la lista
        universe_size = 0

    return {
        "code": mercado.code,
        "label": mercado.label,
        "timezone": str(mercado.tz),
        "currency": mercado.currency,
        "currency_symbol": mercado.currency_symbol,
        "benchmark": mercado.benchmark,
        "universe_file": mercado.universe_file,
        "universe_size": universe_size,
        "min_turnover": mercado.min_turnover,
        "session_open": mercado.open_time.strftime("%H:%M"),
        "session_close": mercado.close_time.strftime("%H:%M"),
        "operating_open": mercado.operating_open.strftime("%H:%M"),
        "operating_close": mercado.operating_close.strftime("%H:%M"),
        "session_minutes": mercado.session_minutes,
        "operating_minutes": mercado.operating_minutes,
        "is_trading_day": market_calendar.is_trading_day(hoy, market=mercado),
        "is_session_open": market_calendar.is_session_open(market=mercado),
        "is_operating": market_calendar.is_operating(market=mercado),
        "status_text": market_calendar.describe(market=mercado),
    }


def all_markets() -> list[dict[str, Any]]:
    return [market_info(code) for code in market_calendar.MARKETS]


# ----------------------------------------------------------------------
# Perfiles
# ----------------------------------------------------------------------

def profile_summaries(db: Database, *, include_archived: bool = False) -> list[dict[str, Any]]:
    """Las tarjetas de la pantalla de perfiles (F5.2).

    Se hace una consulta por perfil en lugar de un SQL con doce subconsultas
    correlacionadas: los perfiles son un puñado —dos o tres— y el codigo que
    resulta se puede leer. Si algun dia hubiera cincuenta, este es el sitio.
    """
    return [
        _profile_summary(db, profile)
        for profile in db.list_profiles(include_archived=include_archived)
    ]


def _profile_summary(db: Database, profile: dict[str, Any]) -> dict[str, Any]:
    settings = db.get_settings(profile["id"])
    mercado = market_calendar.get_market(settings["market"])
    # Con NVIDIA, una columna vacia no es "sin clave": es "usa NVIDIA_API_KEY del
    # entorno". Decir "(sin clave)" mandaria a buscar un problema inexistente.
    vacia = (
        "(NVIDIA_API_KEY del entorno)"
        if settings["llm_provider"] == "nvidia" else "(sin clave)"
    )
    return {
        "id": profile["id"],
        "name": profile["name"],
        "description": profile["description"],
        "status": profile["status"],
        "created_at": profile["created_at"],
        "updated_at": profile["updated_at"],
        "portfolio_id": profile.get("portfolio_id"),
        "market": mercado.code,
        "currency": mercado.currency,
        "currency_symbol": mercado.currency_symbol,
        "llm_provider": settings["llm_provider"],
        "llm_model": settings["llm_model"],
        "llm_api_key_masked": mask_secret(settings["llm_api_key"], empty=vacia),
        "universe_file": settings["universe_file"],
        "watched_symbols": len(db.get_profile_universe(profile["id"])),
        "risk_summary": risk_presets.describe(settings),
        "metrics": profile_metrics(db, profile.get("portfolio_id"), settings),
    }


def profile_metrics(
    db: Database, portfolio_id: str | None, settings: dict[str, Any]
) -> dict[str, Any]:
    empty = {
        "equity": None,
        "initial_budget": float(settings["initial_budget"]),
        "total_return_pct": None,
        "day_pnl_pct": None,
        "open_positions": 0,
        "closed_trades": 0,
        "win_rate_pct": None,
        "realized_pnl": None,
        "cycles": 0,
        "decisions": 0,
        "last_cycle_at": None,
        "last_cycle_status": None,
    }
    if not portfolio_id:
        return empty

    row = db.query(
        "select "
        "  (select equity from equity_snapshots where portfolio_id = ? "
        "     order by as_of desc limit 1) as equity, "
        "  (select day_pnl_pct from equity_snapshots where portfolio_id = ? "
        "     order by as_of desc limit 1) as day_pnl_pct, "
        "  (select count(1) from positions where portfolio_id = ? "
        "     and status = 'open') as open_positions, "
        "  (select count(1) from positions where portfolio_id = ? "
        "     and status = 'closed') as closed_trades, "
        "  (select count(1) from positions where portfolio_id = ? "
        "     and status = 'closed' and realized_pnl > 0) as wins, "
        "  (select round(sum(realized_pnl), 2) from positions "
        "     where portfolio_id = ? and status = 'closed') as realized_pnl, "
        "  (select count(1) from cycles where portfolio_id = ?) as cycles, "
        "  (select count(1) from decisions where portfolio_id = ?) as decisions, "
        "  (select started_at from cycles where portfolio_id = ? "
        "     order by started_at desc limit 1) as last_cycle_at, "
        "  (select status from cycles where portfolio_id = ? "
        "     order by started_at desc limit 1) as last_cycle_status",
        (portfolio_id,) * 10,
    )[0]

    budget = float(settings["initial_budget"])
    equity = row["equity"]
    closed = int(row["closed_trades"] or 0)
    return {
        "equity": equity,
        "initial_budget": budget,
        # Contra el presupuesto asignado, no contra el primer snapshot: es la
        # pregunta que se hace quien compara dos experimentos.
        "total_return_pct": (
            round((equity / budget - 1) * 100, 2) if equity and budget else None
        ),
        "day_pnl_pct": row["day_pnl_pct"],
        "open_positions": int(row["open_positions"] or 0),
        "closed_trades": closed,
        "win_rate_pct": (
            round(100.0 * int(row["wins"] or 0) / closed, 1) if closed else None
        ),
        "realized_pnl": row["realized_pnl"],
        "cycles": int(row["cycles"] or 0),
        "decisions": int(row["decisions"] or 0),
        "last_cycle_at": row["last_cycle_at"],
        "last_cycle_status": row["last_cycle_status"],
    }


def profile_detail(db: Database, profile: dict[str, Any]) -> dict[str, Any]:
    """El resumen mas todo lo que la pantalla de ajustes necesita."""
    settings = db.get_settings(profile["id"])
    payload = _profile_summary(db, profile)
    payload["settings"] = settings
    payload["limits"] = derived_limits(settings)
    payload["universe"] = db.get_profile_universe(profile["id"])
    payload["market_info"] = market_info(settings["market"])
    return payload


def derived_limits(settings: dict[str, Any]) -> dict[str, Any]:
    """Los nueve limites efectivos, mas cuales salen de los deslizadores.

    Lo calcula `src/risk_presets.py`, el mismo modulo que usa el ciclo. Que la
    interfaz no rehaga la cuenta es el punto entero de F6.5: dos formulas
    acabarian discrepando el dia que se retoque un ancla, y la pantalla
    prometeria unos limites mientras el agente aplica otros.
    """
    limits = risk_presets.resolve_limits(settings)
    payload = {
        field: getattr(limits, field) for field in risk_presets.DERIVED_FIELDS
    }
    payload["sector_cap"] = risk_presets.sector_cap(
        settings.get("diversification", 5), limits.max_open_positions
    )
    payload["derived_fields"] = [
        field for field in risk_presets.DERIVED_FIELDS
        if risk_presets.is_derived(settings, field)
    ]
    payload["summary"] = risk_presets.describe(settings)
    return payload


# ----------------------------------------------------------------------
# Precios: lo que se usa para valorar posiciones abiertas
# ----------------------------------------------------------------------

def _price_index(db: Database, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Ultimo precio por simbolo, prefiriendo la cotizacion en vivo.

    Dos fuentes y un orden: `quotes_live` la escribe el ingestor cada minuto;
    `market_snapshots` es el precio que vio el analista en su ultimo ciclo, que
    puede ser de ayer. Se prefiere la primera y **se dice cual se ha usado**, en
    lugar de servir un numero a secas: una posicion valorada con el cierre de
    anteayer y otra con el precio de hace un minuto no se pueden sumar sin
    saberlo.
    """
    if not symbols:
        return {}

    index: dict[str, dict[str, Any]] = {}
    marks = ", ".join("?" for _ in symbols)
    for row in db.query(
        f"select symbol, price, as_of from quotes_live where symbol in ({marks})",
        tuple(symbols),
    ):
        index[row["symbol"]] = {
            "price": row["price"], "as_of": row["as_of"], "source": "live",
        }

    faltan = [symbol for symbol in symbols if symbol not in index]
    if faltan:
        marks = ", ".join("?" for _ in faltan)
        for row in db.query(
            f"select symbol, price, as_of from market_snapshots "
            f"where id in (select max(id) from market_snapshots "
            f"             where symbol in ({marks}) group by symbol)",
            tuple(faltan),
        ):
            index[row["symbol"]] = {
                "price": row["price"], "as_of": row["as_of"], "source": "cycle",
            }
    return index


# ----------------------------------------------------------------------
# Listas paginadas
# ----------------------------------------------------------------------

def positions(
    db: Database, portfolio_id: str, *, status: str = "", symbol: str = "",
    limit: int = 100, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where = ["portfolio_id = ?"]
    params: list[Any] = [portfolio_id]
    if status:
        where.append("status = ?")
        params.append(status)
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    clause = " and ".join(where)

    total = _count(db, f"positions where {clause}", tuple(params))
    rows = db.query(
        "select id, symbol, status, qty, entry_price, stop_price, target_price, "
        "       thesis, horizon_days, opened_at, closed_at, exit_price, "
        "       realized_pnl, exit_reason "
        f"from positions where {clause} "
        # Las abiertas primero: es lo que se mira. Luego por fecha, con
        # `closed_at` cayendo a `opened_at` para que las abiertas no queden todas
        # juntas al final por tener NULL.
        "order by (status = 'open') desc, coalesce(closed_at, opened_at) desc "
        "limit ? offset ?",
        (*params, clamp_limit(limit), max(0, offset)),
    )

    abiertas = [row["symbol"] for row in rows if row["status"] == "open"]
    precios = _price_index(db, sorted(set(abiertas)))
    for row in rows:
        _value_position(row, precios.get(row["symbol"]))
    return rows, total


def _value_position(row: dict[str, Any], price: dict[str, Any] | None) -> None:
    row["last_price"] = None
    row["last_price_as_of"] = None
    row["price_source"] = None
    row["market_value"] = None
    row["unrealized_pnl"] = None
    row["unrealized_pnl_pct"] = None
    row["stop_distance_pct"] = None

    if row["status"] != "open" or price is None:
        return

    last = price["price"]
    row["last_price"] = last
    row["last_price_as_of"] = price["as_of"]
    row["price_source"] = price["source"]

    entry, qty = row["entry_price"], row["qty"]
    if not (last and entry):
        return
    row["market_value"] = round(last * qty, 2)
    row["unrealized_pnl"] = round((last - entry) * qty, 2)
    row["unrealized_pnl_pct"] = round((last / entry - 1) * 100, 2)
    stop = row["stop_price"]
    # Cuanto respira la posicion antes de tocar el stop, en % del precio actual.
    row["stop_distance_pct"] = round((last / stop - 1) * 100, 2) if stop else None


def decisions(
    db: Database, portfolio_id: str, *, symbol: str = "", action: str = "",
    verdict: str = "", cycle_id: str = "", limit: int = 100, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Decisiones con el veredicto de riesgo y el destino de la orden.

    Es la tabla que da sentido al experimento: junta lo que el modelo propuso
    con lo que el Risk Manager permitio y con lo que acabo ocurriendo.
    """
    where = ["d.portfolio_id = ?"]
    params: list[Any] = [portfolio_id]
    if symbol:
        where.append("d.symbol = ?")
        params.append(symbol.upper())
    if action:
        where.append("d.action = ?")
        params.append(action)
    if cycle_id:
        where.append("d.cycle_id = ?")
        params.append(cycle_id)
    if verdict:
        where.append("r.verdict = ?")
        params.append(verdict)
    clause = " and ".join(where)

    joins = (
        "decisions d "
        "left join risk_events r on r.decision_id = d.id "
        "left join orders o on o.decision_id = d.id "
    )
    total = _count(db, f"{joins} where {clause}", tuple(params))
    rows = db.query(
        "select d.id, d.cycle_id, d.created_at, d.symbol, d.kind, d.action, "
        "       d.conviction, d.thesis, d.risks, d.horizon_days, "
        "       d.reference_price, d.suggested_stop, d.suggested_target, "
        "       d.llm_model, d.latency_ms, d.prompt_tokens, d.completion_tokens, "
        "       r.verdict, r.rule, r.reason as risk_reason, r.approved_qty, "
        "       r.approved_notional, "
        "       o.status as order_status, o.filled_avg_price "
        f"from {joins} where {clause} "
        "order by d.created_at desc limit ? offset ?",
        (*params, clamp_limit(limit), max(0, offset)),
    )
    return rows, total


def orders(
    db: Database, portfolio_id: str, *, symbol: str = "", status: str = "",
    limit: int = 100, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where = ["portfolio_id = ?"]
    params: list[Any] = [portfolio_id]
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if status:
        where.append("status = ?")
        params.append(status)
    clause = " and ".join(where)

    total = _count(db, f"orders where {clause}", tuple(params))
    rows = db.query(
        "select id, cycle_id, decision_id, submitted_at, updated_at, symbol, "
        "       side, qty, order_type, status, filled_qty, filled_avg_price, "
        "       stop_price, target_price, broker_order_id, error "
        f"from orders where {clause} order by submitted_at desc limit ? offset ?",
        (*params, clamp_limit(limit), max(0, offset)),
    )
    return rows, total


def risk_events(
    db: Database, portfolio_id: str, *, verdict: str = "", rule: str = "",
    symbol: str = "", limit: int = 100, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where = ["portfolio_id = ?"]
    params: list[Any] = [portfolio_id]
    if verdict:
        where.append("verdict = ?")
        params.append(verdict)
    if rule:
        where.append("rule = ?")
        params.append(rule)
    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    clause = " and ".join(where)

    total = _count(db, f"risk_events where {clause}", tuple(params))
    rows = db.query(
        "select id, cycle_id, decision_id, created_at, symbol, verdict, rule, "
        "       reason, approved_qty, approved_notional, stop_price, target_price "
        f"from risk_events where {clause} order by created_at desc limit ? offset ?",
        (*params, clamp_limit(limit), max(0, offset)),
    )
    return rows, total


_CYCLE_COLUMNS = (
    "select c.id, c.started_at, c.finished_at, c.status, c.equity_start, "
    "       c.equity_end, c.market_open, c.llm_model, c.error, "
    "       c.symbols_scanned_json, c.analyst_calls, c.analyst_failures, "
    "       (select count(1) from decisions d where d.cycle_id = c.id) as decisions, "
    "       (select count(1) from risk_events r where r.cycle_id = c.id "
    "               and r.verdict = 'approved') as approved, "
    "       (select count(1) from risk_events r where r.cycle_id = c.id "
    "               and r.verdict = 'rejected') as rejected, "
    "       (select count(1) from orders o where o.cycle_id = c.id) as orders "
    "from cycles c "
)


def _shape_cycle(row: dict[str, Any]) -> dict[str, Any]:
    row["symbols_scanned"] = _loads(row.pop("symbols_scanned_json"), default=[])
    start, end = row["equity_start"], row["equity_end"]
    row["equity_delta"] = round(end - start, 2) if (start and end) else None
    row["market_open"] = None if row["market_open"] is None else bool(row["market_open"])
    return row


def cycles(
    db: Database, portfolio_id: str, *, status: str = "",
    limit: int = 60, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where = ["c.portfolio_id = ?"]
    params: list[Any] = [portfolio_id]
    if status:
        where.append("c.status = ?")
        params.append(status)
    clause = " and ".join(where)

    total = _count(db, f"cycles c where {clause}", tuple(params))
    rows = db.query(
        f"{_CYCLE_COLUMNS} where {clause} order by c.started_at desc limit ? offset ?",
        (*params, clamp_limit(limit), max(0, offset)),
    )
    return [_shape_cycle(row) for row in rows], total


def cycle_detail(db: Database, cycle_id: str) -> dict[str, Any] | None:
    from src.profile_settings import cycle_settings

    rows = db.query(f"{_CYCLE_COLUMNS} where c.id = ?", (cycle_id,))
    if not rows:
        return None
    row = _shape_cycle(rows[0])
    row["settings"] = cycle_settings(db, cycle_id)
    return row


# ----------------------------------------------------------------------
# Cotizaciones e ingesta
# ----------------------------------------------------------------------

def _age_seconds(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - moment).total_seconds(), 1)


def quotes(db: Database, *, symbols: list[str] | None = None) -> list[dict[str, Any]]:
    rows = list(db.latest_quotes(symbols).values())
    for row in rows:
        # La distancia entre `as_of` (lo que dice el proveedor) y ahora es el
        # retraso real del dato. Es exactamente la pregunta abierta de F2.1c: si
        # Yahoo sirve Europa con 15 minutos de desfase, se vera aqui.
        row["age_seconds"] = _age_seconds(row.get("as_of"))
    rows.sort(key=lambda row: row["symbol"])
    return rows


def ingest_status(db: Database, *, recent: int = 20) -> dict[str, Any]:
    ticks = db.ingest_health(limit=60, kind="tick")
    backfills = db.ingest_health(limit=1, kind="backfill")
    por_mercado = db.active_universe_by_market()

    # Fallos seguidos contados desde el mas reciente hacia atras: es lo que mira
    # el propio ingestor para escalar a error (F2.8), y lo que distingue "un
    # minuto perdido" de "Yahoo lleva media hora sin responder".
    seguidos = 0
    for run in ticks:
        if run["symbols_ok"] or not run["finished_at"]:
            break
        seguidos += 1

    hechos = [run for run in ticks if run["latency_ms"] is not None]
    latencias = [run["latency_ms"] for run in hechos]
    ultimo = ticks[0] if ticks else None
    desde = _age_seconds(ultimo["started_at"]) if ultimo else None

    contados = db.query(
        "select (select count(1) from bars_1m) as bars, "
        "       (select count(1) from quotes_live) as quotes"
    )[0]

    healthy, message = _ingest_verdict(ticks, seguidos, desde, por_mercado)
    return {
        "healthy": healthy,
        "message": message,
        "last_tick_at": ultimo["started_at"] if ultimo else None,
        "seconds_since_last_tick": desde,
        "consecutive_failures": seguidos,
        "rate_limited_recently": any(run["rate_limited"] for run in ticks[:10]),
        "avg_latency_ms": (
            round(sum(latencias) / len(latencias), 1) if latencias else None
        ),
        "symbols_tracked": sum(len(s) for s in por_mercado.values()),
        "symbols_by_market": {code: len(s) for code, s in por_mercado.items()},
        "bars_stored": int(contados["bars"] or 0),
        "quotes_stored": int(contados["quotes"] or 0),
        "last_backfill_at": backfills[0]["started_at"] if backfills else None,
        "recent": [
            {**run, "rate_limited": bool(run["rate_limited"])}
            for run in ticks[:recent]
        ],
    }


def _ingest_verdict(
    ticks: list[dict[str, Any]], seguidos: int, desde: float | None,
    por_mercado: dict[str, list[str]],
) -> tuple[bool, str]:
    """Sano o no, y por que.

    "Sin ticks" no es un fallo si no hay ninguna bolsa abierta: el ingestor
    duerme fuera de la ventana operativa a proposito. Sin esta distincion el
    panel estaria en rojo todas las noches, y un rojo que sale siempre acaba
    sin mirarse.
    """
    abierto = [
        code for code in por_mercado
        if market_calendar.is_operating(market=code)
    ]
    if not ticks:
        if not por_mercado:
            return True, "Ningun perfil activo: el ingestor no tiene que pedir nada."
        if not abierto:
            return True, "Sin ticks todavia; ninguna bolsa seguida esta operando."
        return False, "Ninguna pasada registrada con la bolsa abierta."

    if seguidos >= 5:
        return False, (
            f"{seguidos} pasadas seguidas sin datos. Mira si Yahoo esta "
            "devolviendo 429 o si los simbolos siguen existiendo."
        )
    if not abierto:
        return True, (
            "Fuera de la ventana operativa: el ingestor duerme hasta la proxima "
            "apertura."
        )
    if desde is not None and desde > 180:
        return False, (
            f"El ultimo tick fue hace {math.floor(desde / 60)} minutos y hay "
            "bolsa abierta."
        )
    if seguidos:
        return True, f"{seguidos} pasada(s) sin datos; el siguiente tick reintenta."
    return True, "Ingesta al dia."


# ----------------------------------------------------------------------
# Series para las graficas (F4.6)
# ----------------------------------------------------------------------

def analytics(db: Database, portfolio_id: str) -> dict[str, Any]:
    """Las cinco series que pintan las graficas, en un solo viaje.

    Van juntas y no en cinco endpoints porque son **una sola pantalla**: cinco
    peticiones para dibujarla darian cinco estados de carga y cinco formas de
    fallar a medias, para leer cinco agregados del mismo fichero local.

    Tres de ellas salen de vistas que ya existen en `schema.sql`
    (`v_conviction_calibration`, `v_risk_rejections`, `v_performance_by_symbol`),
    asi que la consola y la web no pueden acabar contando cosas distintas: cada
    numero tiene una sola definicion, y esta en el esquema.
    """
    curva = db.query(
        "select as_of, equity, cash, positions_value, open_positions, day_pnl_pct "
        "from equity_snapshots where portfolio_id = ? order by as_of asc",
        (portfolio_id,),
    )
    return {
        "equity_curve": [{**fila, "drawdown_pct": dd} for fila, dd in
                         zip(curva, _drawdown_series(curva))],
        "calibration": db.query(
            "select conviction_bucket, trades, avg_pnl, win_rate_pct "
            "from v_conviction_calibration where portfolio_id = ?",
            (portfolio_id,),
        ),
        "rejections": db.query(
            "select rule, rejections, last_seen from v_risk_rejections "
            "where portfolio_id = ?",
            (portfolio_id,),
        ),
        "by_symbol": db.query(
            "select symbol, trades, wins, win_rate_pct, total_pnl, avg_pnl, "
            "       avg_holding_days "
            "from v_performance_by_symbol where portfolio_id = ? "
            "order by total_pnl desc",
            (portfolio_id,),
        ),
        "conviction_histogram": db.query(
            "select (cast(conviction / 10 as integer) * 10) as bucket, "
            "       sum(case when action = 'buy' then 1 else 0 end) as buys, "
            "       sum(case when action = 'hold' then 1 else 0 end) as holds, "
            "       sum(case when action = 'sell' then 1 else 0 end) as sells, "
            "       count(*) as total "
            "from decisions where portfolio_id = ? group by bucket order by bucket",
            (portfolio_id,),
        ),
    }


def _drawdown_series(curva: list[dict[str, Any]]) -> list[float]:
    """Caida desde el maximo previo, punto a punto.

    Se calcula aqui y no en el navegador para que la grafica y el `max_drawdown`
    de `run.py report` no puedan discrepar: es la misma definicion que
    `dashboard._max_drawdown_pct`, y tenerla en dos lenguajes seria tenerla dos
    veces.
    """
    pico: float | None = None
    salida: list[float] = []
    for fila in curva:
        equity = fila.get("equity")
        if not equity:
            salida.append(0.0)
            continue
        pico = equity if pico is None else max(pico, equity)
        salida.append(round((equity / pico - 1) * 100, 2) if pico > 0 else 0.0)
    return salida
