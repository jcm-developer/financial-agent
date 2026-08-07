"""Ensamblado de los datos que consume el frontend.

Se mantiene separado del servidor HTTP para que sea testeable sin abrir sockets,
y separado del ciclo porque solo lee: no contacta con el broker ni con el LLM.
Consecuencia practica: el dashboard funciona sin conexion y no puede alterar
nada, ni por accidente ni por un fallo.

El precio "actual" que se muestra es el ultimo registrado en `market_snapshots`,
no una cotizacion en vivo. Se etiqueta como tal en la interfaz: mentir sobre la
frescura de un precio es peor que no darlo.
"""

from __future__ import annotations

import json
from typing import Any

from .db import Database


def build_dashboard(db: Database, *, portfolio_name: str) -> dict[str, Any]:
    """Payload completo del dashboard. Un solo viaje: la base es local y
    pequena, y asi el frontend no encadena peticiones."""
    portfolio = _fetch_portfolio(db, portfolio_name)
    if portfolio is None:
        return {
            "portfolio": None,
            "message": (
                f"Todavia no hay datos para la cartera {portfolio_name!r}. "
                "Ejecuta: python run.py cycle"
            ),
        }

    portfolio_id = portfolio["id"]
    prices = _latest_prices(db)

    equity_curve = db.query(
        "select as_of, equity, cash, positions_value, open_positions, day_pnl_pct "
        "from equity_snapshots where portfolio_id = ? order by as_of asc",
        (portfolio_id,),
    )
    open_positions = _open_positions(db, portfolio_id, prices)
    closed_positions = _closed_positions(db, portfolio_id)

    return {
        "portfolio": portfolio,
        "summary": _summary(db, portfolio_id, equity_curve, open_positions, closed_positions),
        "equity_curve": equity_curve,
        "cycles": _cycles(db, portfolio_id),
        "open_positions": open_positions,
        "closed_positions": closed_positions,
        "performance_by_symbol": db.query(
            "select * from v_performance_by_symbol where portfolio_id = ? "
            "order by total_pnl desc",
            (portfolio_id,),
        ),
        "calibration": db.query(
            "select * from v_conviction_calibration where portfolio_id = ?",
            (portfolio_id,),
        ),
        "rejections": db.query(
            "select rule, rejections, last_seen from v_risk_rejections "
            "where portfolio_id = ?",
            (portfolio_id,),
        ),
        "decisions": _decisions(db, portfolio_id),
        "orders": _orders(db, portfolio_id),
        "conviction_histogram": _conviction_histogram(db, portfolio_id),
    }


# ----------------------------------------------------------------------

def _fetch_portfolio(db: Database, name: str) -> dict[str, Any] | None:
    rows = db.query(
        "select id, name, mode, initial_budget, created_at from portfolios "
        "where name = ? limit 1",
        (name,),
    )
    return rows[0] if rows else None


def list_portfolios(db: Database) -> list[dict[str, Any]]:
    """Para el selector de cartera del frontend."""
    return db.query(
        "select p.name, p.mode, p.created_at, "
        "       (select count(*) from cycles c where c.portfolio_id = p.id) as cycles "
        "from portfolios p order by p.created_at desc"
    )


def _latest_prices(db: Database) -> dict[str, dict[str, Any]]:
    """Ultimo precio observado por simbolo, con su fecha."""
    rows = db.query(
        "select symbol, price, as_of from market_snapshots "
        "where id in (select max(id) from market_snapshots group by symbol)"
    )
    return {row["symbol"]: row for row in rows}


def _open_positions(
    db: Database, portfolio_id: str, prices: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = db.query(
        "select id, symbol, qty, entry_price, stop_price, target_price, thesis, "
        "       horizon_days, opened_at "
        "from positions where portfolio_id = ? and status = 'open' "
        "order by opened_at desc",
        (portfolio_id,),
    )
    for row in rows:
        snapshot = prices.get(row["symbol"])
        last = snapshot["price"] if snapshot else None
        row["last_price"] = last
        row["last_price_as_of"] = snapshot["as_of"] if snapshot else None

        entry, qty = row["entry_price"], row["qty"]
        if last is not None and entry:
            row["market_value"] = round(last * qty, 2)
            row["unrealized_pnl"] = round((last - entry) * qty, 2)
            row["unrealized_pnl_pct"] = round((last / entry - 1) * 100, 2)
            # Cuanto queda hasta el stop, en % del precio actual: la medida de
            # cuanto respira la posicion.
            stop = row["stop_price"]
            row["stop_distance_pct"] = (
                round((last / stop - 1) * 100, 2) if stop else None
            )
        else:
            row["market_value"] = None
            row["unrealized_pnl"] = None
            row["unrealized_pnl_pct"] = None
            row["stop_distance_pct"] = None
    return rows


def _closed_positions(db: Database, portfolio_id: str) -> list[dict[str, Any]]:
    return db.query(
        "select symbol, qty, entry_price, exit_price, realized_pnl, exit_reason, "
        "       thesis, opened_at, closed_at, "
        "       round(julianday(closed_at) - julianday(opened_at), 1) as holding_days, "
        "       case when entry_price > 0 "
        "            then round((exit_price / entry_price - 1) * 100, 2) end as return_pct "
        "from positions where portfolio_id = ? and status = 'closed' "
        "order by closed_at desc limit 200",
        (portfolio_id,),
    )


def _cycles(db: Database, portfolio_id: str) -> list[dict[str, Any]]:
    rows = db.query(
        "select c.id, c.started_at, c.finished_at, c.status, c.equity_start, "
        "       c.equity_end, c.market_open, c.llm_model, c.error, "
        "       c.symbols_scanned_json, "
        "       (select count(*) from decisions d where d.cycle_id = c.id) as decisions, "
        "       (select count(*) from risk_events r where r.cycle_id = c.id "
        "               and r.verdict = 'approved') as approved, "
        "       (select count(*) from risk_events r where r.cycle_id = c.id "
        "               and r.verdict = 'rejected') as rejected, "
        "       (select count(*) from orders o where o.cycle_id = c.id) as orders "
        "from cycles c where c.portfolio_id = ? order by c.started_at desc limit 60",
        (portfolio_id,),
    )
    for row in rows:
        row["symbols_scanned"] = _loads(row.pop("symbols_scanned_json"), default=[])
        start, end = row["equity_start"], row["equity_end"]
        row["equity_delta"] = round(end - start, 2) if (start and end) else None
    return rows


def _decisions(db: Database, portfolio_id: str) -> list[dict[str, Any]]:
    """Decisiones con el veredicto de riesgo asociado.

    Es la tabla que da sentido al experimento: junta lo que el modelo propuso
    con lo que el Risk Manager permitio.
    """
    rows = db.query(
        "select d.id, d.created_at, d.symbol, d.kind, d.action, d.conviction, "
        "       d.thesis, d.risks, d.horizon_days, d.reference_price, "
        "       d.suggested_stop, d.suggested_target, d.llm_model, d.latency_ms, "
        "       d.prompt_tokens, d.completion_tokens, "
        "       r.verdict, r.rule, r.reason as risk_reason, r.approved_qty, "
        "       r.approved_notional, r.stop_price, r.target_price, "
        "       o.status as order_status, o.filled_avg_price "
        "from decisions d "
        "left join risk_events r on r.decision_id = d.id "
        "left join orders o on o.decision_id = d.id "
        "where d.portfolio_id = ? order by d.created_at desc limit 300",
        (portfolio_id,),
    )
    return rows


def _orders(db: Database, portfolio_id: str) -> list[dict[str, Any]]:
    return db.query(
        "select submitted_at, symbol, side, qty, status, filled_qty, "
        "       filled_avg_price, stop_price, target_price, broker_order_id, error "
        "from orders where portfolio_id = ? order by submitted_at desc limit 100",
        (portfolio_id,),
    )


def _conviction_histogram(db: Database, portfolio_id: str) -> list[dict[str, Any]]:
    """Reparto de la conviccion declarada, separando compras de mantenimientos.

    Si el modelo declara 80 en todo, la conviccion no informa de nada y la vista
    de calibracion lo confirmara.
    """
    return db.query(
        "select (cast(conviction / 10 as integer) * 10) as bucket, "
        "       sum(case when action = 'buy' then 1 else 0 end) as buys, "
        "       sum(case when action = 'hold' then 1 else 0 end) as holds, "
        "       sum(case when action = 'sell' then 1 else 0 end) as sells, "
        "       count(*) as total "
        "from decisions where portfolio_id = ? group by bucket order by bucket",
        (portfolio_id,),
    )


def _summary(
    db: Database,
    portfolio_id: str,
    equity_curve: list[dict[str, Any]],
    open_positions: list[dict[str, Any]],
    closed_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    latest = equity_curve[-1] if equity_curve else None
    first = equity_curve[0] if equity_curve else None

    realized = sum(row["realized_pnl"] or 0.0 for row in closed_positions)
    wins = sum(1 for row in closed_positions if (row["realized_pnl"] or 0) > 0)
    losses = sum(1 for row in closed_positions if (row["realized_pnl"] or 0) < 0)
    gross_win = sum(
        row["realized_pnl"] for row in closed_positions if (row["realized_pnl"] or 0) > 0
    )
    gross_loss = -sum(
        row["realized_pnl"] for row in closed_positions if (row["realized_pnl"] or 0) < 0
    )

    unrealized = sum(
        row["unrealized_pnl"] for row in open_positions
        if row["unrealized_pnl"] is not None
    )

    counts = db.query(
        "select "
        "  (select count(*) from cycles where portfolio_id = ?) as cycles, "
        "  (select count(*) from decisions where portfolio_id = ?) as decisions, "
        "  (select count(*) from decisions where portfolio_id = ? and action = 'buy') "
        "     as buy_decisions, "
        "  (select count(*) from risk_events where portfolio_id = ? "
        "     and verdict = 'rejected') as rejections, "
        "  (select count(*) from orders where portfolio_id = ?) as orders, "
        "  (select round(avg(conviction), 1) from decisions where portfolio_id = ?) "
        "     as avg_conviction, "
        "  (select sum(prompt_tokens + completion_tokens) from decisions "
        "     where portfolio_id = ?) as tokens",
        (portfolio_id,) * 7,
    )[0]

    equity = latest["equity"] if latest else None
    equity_start = first["equity"] if first else None

    return {
        "equity": equity,
        "cash": latest["cash"] if latest else None,
        "positions_value": latest["positions_value"] if latest else None,
        "equity_start": equity_start,
        "total_return_pct": (
            round((equity / equity_start - 1) * 100, 2)
            if equity and equity_start else None
        ),
        "last_update": latest["as_of"] if latest else None,
        "open_positions": len(open_positions),
        "unrealized_pnl": round(unrealized, 2),
        "realized_pnl": round(realized, 2),
        "closed_trades": len(closed_positions),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": (
            round(100.0 * wins / len(closed_positions), 1) if closed_positions else None
        ),
        # Beneficio bruto / perdida bruta. Por debajo de 1 el sistema pierde
        # dinero aunque acierte mas veces de las que falla.
        "profit_factor": (
            round(gross_win / gross_loss, 2) if gross_loss > 0
            else (None if gross_win == 0 else float("inf"))
        ),
        "avg_win": round(gross_win / wins, 2) if wins else None,
        "avg_loss": round(-gross_loss / losses, 2) if losses else None,
        "max_drawdown_pct": _max_drawdown_pct(equity_curve),
        "cycles": counts["cycles"],
        "decisions": counts["decisions"],
        "buy_decisions": counts["buy_decisions"],
        "buy_rate_pct": (
            round(100.0 * counts["buy_decisions"] / counts["decisions"], 1)
            if counts["decisions"] else None
        ),
        "rejections": counts["rejections"],
        "orders": counts["orders"],
        "avg_conviction": counts["avg_conviction"],
        "tokens": counts["tokens"] or 0,
    }


def _max_drawdown_pct(equity_curve: list[dict[str, Any]]) -> float | None:
    """Maxima caida desde un maximo previo. Es la medida de riesgo que importa:
    dice cuanto habria dolido en el peor momento."""
    peak = None
    worst = 0.0
    for row in equity_curve:
        equity = row.get("equity")
        if not equity:
            continue
        peak = equity if peak is None else max(peak, equity)
        if peak > 0:
            worst = min(worst, (equity / peak - 1) * 100)
    return round(worst, 2) if peak is not None else None


def _loads(raw: Any, *, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default
