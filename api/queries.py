"""The API's read queries.

They live apart from `src/dashboard.py` because they answer a different question.
The dashboard assembles **one** complete payload for a book in a single trip,
which is what a screen painted whole at once needs. These endpoints are lists
that get filtered, sorted and paginated, which is what a table with 480 decisions
and a search box above it needs.

They are served from the **read-only** connection (see `deps.py`): looking at the
history cannot alter it, and here that is guaranteed by SQLite's `ro` mode, not
by these functions' good manners.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from src import market_calendar, risk_presets
from src.db import Database, DatabaseError
from src.profile_settings import mask_secret

#: Cap on rows per page. It is not paranoia: `decisions` stores the model's raw
#: response, so a few thousand rows are megabytes of JSON travelling to a browser
#: that is only going to paint twenty.
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
    """Everything the interface needs to know about an exchange, already resolved.

    It includes whether it is open **now**, and that is why it is not cached: the
    frontend consults it to decide whether offering the button to launch a cycle
    makes any sense.
    """
    from src.screener import load_universe

    market = market_calendar.get_market(code)
    hoy = market_calendar.now_local(market).date()
    try:
        universe_size = len(load_universe(market.universe_file))
    except Exception:  # noqa: BLE001 - an unreadable universe must not take the list down
        universe_size = 0

    return {
        "code": market.code,
        "label": market.label,
        "timezone": str(market.tz),
        "currency": market.currency,
        "currency_symbol": market.currency_symbol,
        "benchmark": market.benchmark,
        "universe_file": market.universe_file,
        "universe_size": universe_size,
        "min_turnover": market.min_turnover,
        "session_open": market.open_time.strftime("%H:%M"),
        "session_close": market.close_time.strftime("%H:%M"),
        "operating_open": market.operating_open.strftime("%H:%M"),
        "operating_close": market.operating_close.strftime("%H:%M"),
        "session_minutes": market.session_minutes,
        "operating_minutes": market.operating_minutes,
        "is_trading_day": market_calendar.is_trading_day(hoy, market=market),
        "is_session_open": market_calendar.is_session_open(market=market),
        "is_operating": market_calendar.is_operating(market=market),
        "status_text": market_calendar.describe(market=market),
    }


def all_markets() -> list[dict[str, Any]]:
    return [market_info(code) for code in market_calendar.MARKETS]


# ----------------------------------------------------------------------
# Perfiles
# ----------------------------------------------------------------------

def profile_summaries(db: Database, *, include_archived: bool = False) -> list[dict[str, Any]]:
    """The cards on the profiles screen (F5.2).

    One query per profile instead of a SQL statement with twelve correlated
    subqueries: the profiles are a handful —two or three— and the resulting code
    can be read. If there were ever fifty, this is the place.
    """
    return [
        _profile_summary(db, profile)
        for profile in db.list_profiles(include_archived=include_archived)
    ]


def _profile_summary(db: Database, profile: dict[str, Any]) -> dict[str, Any]:
    settings = db.get_settings(profile["id"])
    market = market_calendar.get_market(settings["market"])
    # With NVIDIA, an empty column does not mean "no key": it means "use
    # NVIDIA_API_KEY from the environment". Saying "(sin clave)" would send
    # someone hunting for a problem that does not exist.
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
        "market": market.code,
        "currency": market.currency,
        "currency_symbol": market.currency_symbol,
        "llm_provider": settings["llm_provider"],
        "llm_model": settings["llm_model"],
        "llm_api_key_masked": mask_secret(settings["llm_api_key"], empty=vacia),
        "universe_file": settings["universe_file"],
        # It travels in the summary and not only in the settings because the list
        # and the comparator have to be able to say which experiment is the
        # control (F5.7): comparing against a control you cannot identify is the
        # same as not having one.
        "screener_mode": settings["screener_mode"],
        "watched_symbols": len(db.get_profile_universe(profile["id"])),
        "risk_summary": risk_presets.describe(settings),
        "metrics": profile_metrics(db, profile.get("portfolio_id"), settings),
    }


def profile_metrics(
    db: Database, portfolio_id: str | None, settings: dict[str, Any]
) -> dict[str, Any]:
    empty = {
        "equity": None,
        "cash": None,
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
        "     order by started_at desc limit 1) as last_cycle_status, "
        # Straight from the broker's ledger and not from the last snapshot: cash
        # is the one figure in this whole set that does **not** depend on a price,
        # so it has no reason to be as old as the last cycle. It only moves on a
        # fill, and a fill only happens inside a cycle.
        "  (select cash from sim_accounts where id = ?) as cash",
        (portfolio_id,) * 11,
    )[0]

    budget = float(settings["initial_budget"])
    equity = row["equity"]
    closed = int(row["closed_trades"] or 0)
    return {
        "equity": equity,
        "cash": row["cash"],
        "initial_budget": budget,
        # Against the assigned budget, not against the first snapshot: it is the
        # question whoever compares two experiments asks.
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
    """The summary plus everything the settings screen needs."""
    settings = db.get_settings(profile["id"])
    payload = _profile_summary(db, profile)
    payload["settings"] = settings
    payload["limits"] = derived_limits(settings)
    payload["universe"] = db.get_profile_universe(profile["id"])
    payload["market_info"] = market_info(settings["market"])
    return payload


def derived_limits(settings: dict[str, Any]) -> dict[str, Any]:
    """The nine effective limits, plus which ones come from the sliders.

    `src/risk_presets.py` computes it, the same module the cycle uses. The
    interface not redoing the arithmetic is the whole point of F6.5: two formulas
    would end up disagreeing the day an anchor is tweaked, and the screen would
    promise one set of limits while the agent applied another.
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


def cycle_running_elsewhere(db: Database) -> bool:
    """Whether a cycle is running that this process did not launch.

    The API's `CycleRunner` only knows about the subprocess it spawned itself, so
    a cycle fired by the scheduler —another container— was invisible to it. This
    is the second source, and it lives here rather than in a route because **two
    routes need it**: `/run` to refuse a second cycle over the same book, and
    `/control/status` and the stream to stop claiming that nothing is running.

    A failure to read is **not** treated as "one is running": the start has its own
    check inside the cycle (`find_running_cycle`) and that is the one that really
    decides, so being wrong here in the cautious direction would only block a
    legitimate launch.
    """
    try:
        return bool(
            db.query("select count(1) as n from cycles where status = 'running'")[0]["n"]
        )
    except DatabaseError:
        return False


# ----------------------------------------------------------------------
# Prices: what is used to value open positions
# ----------------------------------------------------------------------

def _price_index(db: Database, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Last price per symbol, preferring the live quote.

    Two sources and one order: `quotes_live` is written by the ingestor every
    minute; `market_snapshots` is the price the analyst saw on its last cycle,
    which may be yesterday's. The first is preferred and **which one was used is
    stated**, instead of serving a bare number: a position valued with the close
    from two days ago and another with a price from a minute ago cannot be added
    together without knowing it.
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


def _commission_index(
    db: Database, portfolio_id: str, symbols: list[str]
) -> dict[str, float]:
    """Commission already paid to open each position, by symbol.

    It has to be looked up in the **broker's ledger** because `positions` never
    had the column: the experiment's table records what was decided, and the
    commission is what the execution cost. `sim_positions` keeps it precisely
    because the realized P&L cannot rebuild it afterwards (see `schema.sql`), and
    it accumulates when a symbol is bought twice.

    A symbol missing from the ledger comes back **absent, not zero**. Zero would
    be a claim —"this one traded free"— and here the truth is "the ledger does not
    know", which the caller reports as such instead of quietly serving a gross
    P&L dressed as a net one.

    @param db: Read-only connection.
    @param portfolio_id: The portfolio, which is also the simulated account's id.
    @param symbols: Symbols with an open position.
    @return: Commission per symbol, only for the ones the ledger has.
    """
    if not symbols:
        return {}

    marks = ", ".join("?" for _ in symbols)
    return {
        row["symbol"]: float(row["entry_commission"])
        for row in db.query(
            f"select symbol, entry_commission from sim_positions "
            f"where account_id = ? and symbol in ({marks})",
            (portfolio_id, *symbols),
        )
    }


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
        # Open ones first: that is what gets looked at. Then by date, with
        # `closed_at` falling back to `opened_at` so the open ones do not all end
        # up bunched at the end for having NULL.
        "order by (status = 'open') desc, coalesce(closed_at, opened_at) desc "
        "limit ? offset ?",
        (*params, clamp_limit(limit), max(0, offset)),
    )

    abiertas = sorted({row["symbol"] for row in rows if row["status"] == "open"})
    prices = _price_index(db, abiertas)
    commissions = _commission_index(db, portfolio_id, abiertas)
    for row in rows:
        _value_position(row, prices.get(row["symbol"]), commissions.get(row["symbol"]))
    return rows, total


def _value_position(
    row: dict[str, Any],
    price: dict[str, Any] | None,
    commission: float | None = None,
) -> None:
    """Values an open position at the last price, **net of what it cost to open**.

    The commission is subtracted because otherwise the same column means two
    different things depending on the row: `realized_pnl` on a closed position
    already nets both legs (`sim_broker.sell_market`), so an open one showing the
    gross figure made "P&L" mean net below and gross above, in a table read down
    the column. It also made the screen fail to reconcile: with a 3,00 EUR
    commission, a book down 1,07 EUR sat under a capital figure down 4,07 EUR, and
    nothing on screen explained the difference. Cash has the commission taken out
    of it from the moment of the fill, so netting it here is what makes
    `equity = cash + posiciones` and the sum of the P&Ls tell the same story.

    **A position whose commission is unknown keeps its gross P&L** and says so
    through `entry_commission` being null, rather than being netted by zero. It
    cannot happen through the normal route —the simulated broker writes both rows—
    and if it ever does, a figure that is visibly missing its cost beats one that
    silently claims to include it.
    """
    row["last_price"] = None
    row["last_price_as_of"] = None
    row["price_source"] = None
    row["market_value"] = None
    row["unrealized_pnl"] = None
    row["unrealized_pnl_pct"] = None
    row["stop_distance_pct"] = None
    row["entry_commission"] = commission if row["status"] == "open" else None

    if row["status"] != "open" or price is None:
        return

    last = price["price"]
    row["last_price"] = last
    row["last_price_as_of"] = price["as_of"]
    row["price_source"] = price["source"]

    entry, qty = row["entry_price"], row["qty"]
    if not (last and entry):
        return
    cost = entry * qty
    pnl = (last - entry) * qty - (commission or 0.0)
    row["market_value"] = round(last * qty, 2)
    row["unrealized_pnl"] = round(pnl, 2)
    # Over the cost and not `last / entry - 1`, so the percentage carries the
    # commission the amount beside it already carries. The bare price move is
    # still on the row: it is `Entrada` next to `Último`.
    row["unrealized_pnl_pct"] = round(pnl / cost * 100, 2)
    stop = row["stop_price"]
    # How much room the position has before touching the stop, in % of the current price.
    row["stop_distance_pct"] = round((last / stop - 1) * 100, 2) if stop else None


def decisions(
    db: Database, portfolio_id: str, *, symbol: str = "", action: str = "",
    verdict: str = "", cycle_id: str = "", limit: int = 100, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Decisions with the risk verdict and the order's fate.

    It is the table that gives the experiment its meaning: it joins what the
    model proposed with what the Risk Manager allowed and with what ended up
    happening.
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
        # The gap between `as_of` (what the provider says) and now is the datum's
        # real lag. It is exactly the open question of F2.1c: if Yahoo serves
        # Europe 15 minutes behind, it will show up here.
        row["age_seconds"] = _age_seconds(row.get("as_of"))
    rows.sort(key=lambda row: row["symbol"])
    return rows


def ingest_status(db: Database, *, recent: int = 20) -> dict[str, Any]:
    ticks = db.ingest_health(limit=60, kind="tick")
    backfills = db.ingest_health(limit=1, kind="backfill")
    por_mercado = db.active_universe_by_market()

    # Consecutive failures counted from the most recent one backwards: it is what
    # the ingestor itself looks at to escalate to an error (F2.8), and what tells
    # "one lost minute" from "Yahoo has not answered for half an hour".
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
    """Healthy or not, and why.

    "No ticks" is not a failure when no exchange is open: the ingestor sleeps
    outside the operating window on purpose. Without that distinction the panel
    would be red every night, and a red that is always on ends up unwatched.
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
    """The five series that paint the charts, in a single trip.

    They travel together and not in five endpoints because they are **one single
    screen**: five requests to draw it would give five loading states and five
    ways of half-failing, in order to read five aggregates of the same local file.

    Three of them come from views that already exist in `schema.sql`
    (`v_conviction_calibration`, `v_risk_rejections`, `v_performance_by_symbol`),
    so the console and the web cannot end up telling different stories: each
    number has one single definition, and it is in the schema.
    """
    curva = db.query(
        "select as_of, equity, cash, positions_value, open_positions, day_pnl_pct "
        "from equity_snapshots where portfolio_id = ? order by as_of asc",
        (portfolio_id,),
    )
    return {
        "equity_curve": [{**row, "drawdown_pct": dd} for row, dd in
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
    """Drop from the previous peak, point by point.

    It is computed here and not in the browser so the chart and `run.py report`'s
    `max_drawdown` cannot disagree: it is the same definition as
    `dashboard._max_drawdown_pct`, and having it in two languages would be having
    it twice.
    """
    pico: float | None = None
    output: list[float] = []
    for row in curva:
        equity = row.get("equity")
        if not equity:
            output.append(0.0)
            continue
        pico = equity if pico is None else max(pico, equity)
        output.append(round((equity / pico - 1) * 100, 2) if pico > 0 else 0.0)
    return output
