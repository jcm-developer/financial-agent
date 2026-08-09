#!/usr/bin/env python
"""Fills a demonstration book so the dashboard can be seen without waiting weeks
for real cycles.

    python tools/seed_demo.py            creates the 'demo' book
    python tools/seed_demo.py --reset    deletes it and creates it again

The data is synthetic and deterministic (a congruential generator of our own,
without `random`, so two runs give the same thing). The book is called `demo` so
it is never confused with the real one: change PORTFOLIO_NAME in `.env` or use
the dashboard's selector to see it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Infra  # noqa: E402
from src.db import Database  # noqa: E402
from src.models import MarketSnapshot, Proposal, RiskVerdict  # noqa: E402

DEMO_NAME = "demo"
SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AMD", "TSLA"]
START_EQUITY = 10_000.0
CYCLES = 40


class Deterministic:
    """Linear congruential generator. It replaces `random` so the demo is
    reproducible without depending on a global seed."""

    def __init__(self, seed: int = 20260807) -> None:
        self.state = seed

    def next(self) -> float:
        self.state = (self.state * 1103515245 + 12345) & 0x7FFFFFFF
        return self.state / 0x7FFFFFFF

    def between(self, low: float, high: float) -> float:
        return low + (high - low) * self.next()

    def pick(self, options: list):
        return options[int(self.next() * len(options)) % len(options)]


def backdate_cycle(db: Database, cycle_id: str, when: datetime) -> None:
    """Pushes the timestamps of an already-inserted cycle back in time.

    `Database`'s methods stamp everything with the current time, which is right in
    production but leaves the demo with 40 cycles at the same instant: the equity
    curve would come out flat on the X axis and the holding days at zero. Here the
    marks are rewritten to simulate 40 real days.
    """
    stamp = when.isoformat()
    db.execute("update cycles set started_at = ?, finished_at = ? where id = ?",
               (stamp, stamp, cycle_id))
    db.execute("update market_snapshots set as_of = ?, created_at = ? where cycle_id = ?",
               (stamp, stamp, cycle_id))
    db.execute("update decisions set created_at = ? where cycle_id = ?", (stamp, cycle_id))
    db.execute("update risk_events set created_at = ? where cycle_id = ?", (stamp, cycle_id))
    db.execute("update orders set submitted_at = ?, updated_at = ? where cycle_id = ?",
               (stamp, stamp, cycle_id))
    db.execute("update equity_snapshots set as_of = ? where cycle_id = ?", (stamp, cycle_id))


def _demo_profile(db: Database) -> str:
    """Creates the demo's profile and returns its book id.

    **It has been needed since F6.4**, and it went unnoticed until F4. This used
    to call only `ensure_portfolio`, which leaves a book with no profile: the
    console found it by name and the old dashboard offered it in its selector, so
    the demo seemed to work. The new interface navigates by profile
    (`/p/demo/...`), so an orphan book is invisible: `/api/profiles` returned an
    empty list and the README's demo showed nothing.
    """
    existente = db.get_profile_by_name(DEMO_NAME)
    if existente is not None:
        return str(existente["portfolio_id"])

    profile_id = db.create_profile(
        name=DEMO_NAME, description="Datos sinteticos para probar la interfaz."
    )
    db.update_settings(profile_id, {"initial_budget": START_EQUITY}, source="seed_demo")
    db.set_profile_universe(profile_id, list(SYMBOLS))
    db.set_profile_status(profile_id, "active")
    return str(db.get_profile(profile_id)["portfolio_id"])


def seed(db: Database) -> None:
    rng = Deterministic()
    portfolio_id = _demo_profile(db)

    base_price = {symbol: rng.between(80, 380) for symbol in SYMBOLS}
    equity = START_EQUITY
    cash = START_EQUITY
    open_rows: dict[str, dict] = {}
    start = datetime.now(timezone.utc) - timedelta(days=CYCLES)

    for day in range(CYCLES):
        as_of = start + timedelta(days=day)
        market_open = as_of.weekday() < 5

        cycle_id = db.start_cycle(
            portfolio_id=portfolio_id,
            equity_start=equity,
            cash_start=cash,
            market_open=market_open,
            symbols=SYMBOLS,
            llm_model="meta/llama-3.3-70b-instruct",
        )

        # Deriva de precios.
        for symbol in SYMBOLS:
            base_price[symbol] *= 1 + rng.between(-0.028, 0.030)

        snapshot_ids: dict[str, int] = {}
        for symbol in SYMBOLS:
            price = base_price[symbol]
            atr = price * rng.between(0.012, 0.032)
            snapshot_ids[symbol] = db.save_snapshot(
                cycle_id=cycle_id,
                snapshot=MarketSnapshot(
                    symbol=symbol,
                    as_of=as_of,
                    price=round(price, 2),
                    indicators={
                        "price": round(price, 2),
                        "rsi_14": round(rng.between(28, 78), 2),
                        "atr_14": round(atr, 4),
                        "atr_pct": round(atr / price * 100, 2),
                        "sma_50": round(price * rng.between(0.94, 1.05), 2),
                        "sma_200": round(price * rng.between(0.86, 1.10), 2),
                        "volatility_20d_pct": round(rng.between(16, 48), 2),
                        "volume_ratio": round(rng.between(0.6, 2.2), 2),
                        "bars_available": 200,
                    },
                ),
            )

        # --- Salidas: stop, objetivo o revision del analista ---------------
        for symbol, row in list(open_rows.items()):
            price = base_price[symbol]
            reason = rule = None
            if price <= row["stop"]:
                reason, rule = f"Precio {price:.2f} perforo el stop.", "stop_loss_hit"
            elif price >= row["target"]:
                reason, rule = f"Precio {price:.2f} alcanzo el objetivo.", "take_profit_hit"
            elif rng.next() < 0.06:
                reason, rule = "El deterioro tecnico invalida la tesis.", "llm_exit"

            if reason is None:
                continue

            order_id = db.save_order(
                cycle_id=cycle_id, portfolio_id=portfolio_id, symbol=symbol,
                side="sell", qty=row["qty"], status="filled",
                filled_qty=row["qty"], filled_avg_price=round(price, 2),
            )
            pnl = (price - row["entry"]) * row["qty"]
            db.close_position(
                row["id"], exit_price=price, realized_pnl=pnl,
                exit_reason=f"[{rule}] {reason}", exit_order_id=order_id,
            )
            db.execute(
                "update positions set closed_at = ? where id = ?",
                (as_of.isoformat(), row["id"]),
            )
            cash += price * row["qty"]
            del open_rows[symbol]

        # --- Entradas -----------------------------------------------------
        for symbol in SYMBOLS:
            if symbol in open_rows:
                continue

            price = base_price[symbol]
            buying = rng.next() < 0.16
            conviction = int(rng.between(58, 92)) if buying else int(rng.between(20, 62))
            atr = price * 0.02

            proposal = Proposal(
                symbol=symbol, kind="entry",
                action="buy" if buying else "hold",
                conviction=conviction,
                thesis=(
                    f"{symbol} cotiza sobre su media de 50 sesiones con el RSI en zona "
                    f"neutra y volumen por encima de la media; la estructura de maximos "
                    f"crecientes sigue intacta."
                    if buying else
                    f"{symbol} se mueve dentro de su rango sin una senal clara; el RSI "
                    f"no confirma ni ruptura ni agotamiento. Sin ventaja identificable."
                ),
                risks="Un giro del mercado general invalidaria la lectura tecnica.",
                horizon_days=int(rng.between(5, 30)),
                suggested_stop=round(price - atr * 2.2, 2),
                suggested_target=round(price + atr * 4, 2),
                reference_price=round(price, 2),
                model="meta/llama-3.3-70b-instruct",
                latency_ms=int(rng.between(900, 5200)),
                prompt_tokens=int(rng.between(1100, 1900)),
                completion_tokens=int(rng.between(180, 620)),
                raw_response={"parsed": {"action": "buy" if buying else "hold"}},
            )
            decision_id = db.save_decision(
                cycle_id=cycle_id, portfolio_id=portfolio_id,
                proposal=proposal, snapshot_id=snapshot_ids[symbol],
            )

            if not buying:
                continue

            # Risk verdict, with the same reasons as the real system.
            stop = price - atr * 2
            qty = int((equity * 0.01) / (price - stop))
            qty = min(qty, int((equity * 0.20) / price), int(cash / price))

            if conviction < 65:
                verdict = RiskVerdict(
                    approved=False, rule="min_conviction",
                    reason=f"Conviccion {conviction} por debajo del minimo 65.",
                )
            elif len(open_rows) >= 5:
                verdict = RiskVerdict(
                    approved=False, rule="max_open_positions",
                    reason=f"Ya hay {len(open_rows)} posiciones abiertas, el maximo es 5.",
                )
            elif qty < 1:
                verdict = RiskVerdict(
                    approved=False, rule="insufficient_cash",
                    reason=f"Efectivo insuficiente para una accion a {price:.2f}.",
                )
            else:
                target = price + (price - stop) * 2
                verdict = RiskVerdict(
                    approved=True, rule="risk_per_trade",
                    reason=f"Aprobadas {qty} acciones de {symbol}.",
                    qty=float(qty), notional=round(qty * price, 2),
                    stop_price=round(stop, 2), target_price=round(target, 2),
                    details={
                        "risk_budget": round(equity * 0.01, 2),
                        "risk_per_share": round(price - stop, 4),
                        "reward_risk": 2.0,
                        "stop_source": "atr",
                        "pct_of_equity": round(qty * price / equity * 100, 2),
                        "conviction": conviction,
                    },
                )

            risk_event_id = db.save_risk_event(
                cycle_id=cycle_id, portfolio_id=portfolio_id, symbol=symbol,
                verdict=verdict, decision_id=decision_id,
            )
            if not verdict.approved:
                continue

            order_id = db.save_order(
                cycle_id=cycle_id, portfolio_id=portfolio_id, symbol=symbol,
                side="buy", qty=verdict.qty, status="filled",
                decision_id=decision_id, risk_event_id=risk_event_id,
                filled_qty=verdict.qty, filled_avg_price=round(price, 2),
                stop_price=verdict.stop_price, target_price=verdict.target_price,
            )
            position_id = db.open_position(
                portfolio_id=portfolio_id, symbol=symbol, qty=verdict.qty,
                entry_price=price, stop_price=verdict.stop_price,
                target_price=verdict.target_price, thesis=proposal.thesis,
                horizon_days=proposal.horizon_days, entry_order_id=order_id,
            )
            db.execute(
                "update positions set opened_at = ? where id = ?",
                (as_of.isoformat(), position_id),
            )
            cash -= verdict.qty * price
            open_rows[symbol] = {
                "id": position_id, "qty": verdict.qty, "entry": price,
                "stop": verdict.stop_price, "target": verdict.target_price,
            }

        positions_value = sum(
            base_price[symbol] * row["qty"] for symbol, row in open_rows.items()
        )
        previous_equity = equity
        equity = cash + positions_value

        db.save_equity_snapshot(
            portfolio_id=portfolio_id, cycle_id=cycle_id, equity=equity, cash=cash,
            positions_value=positions_value, open_positions=len(open_rows),
            day_pnl=equity - previous_equity,
            day_pnl_pct=(equity / previous_equity - 1) * 100 if previous_equity else 0.0,
        )
        db.finish_cycle(cycle_id, status="completed", equity_end=equity)
        backdate_cycle(db, cycle_id, as_of)

    print(f"  Cartera '{DEMO_NAME}' creada con {CYCLES} ciclos.")
    print(f"  Equity final: ${equity:,.2f}  ({equity / START_EQUITY - 1:+.2%})")
    print(f"  Posiciones abiertas: {len(open_rows)}")


def reset(db: Database) -> None:
    rows = db.query("select id from portfolios where name = ?", (DEMO_NAME,))
    for row in rows:
        # `on delete cascade` limpia ciclos, decisiones, ordenes y posiciones.
        db.execute("delete from portfolios where id = ?", (row["id"],))
    if rows:
        print(f"  Cartera '{DEMO_NAME}' anterior eliminada.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera datos de demostracion.")
    # The default comes from `DB_PATH` as in every other command. It used to be
    # written by hand —"data/trading.db"— and that made `DB_PATH=other.db python
    # tools/seed_demo.py` write to the usual database without saying anything: in
    # Docker it coincided by chance, because the working directory is /app.
    parser.add_argument(
        "--db", default=Infra.load().db_path, help="Ruta de la base."
    )
    parser.add_argument("--reset", action="store_true",
                        help="Borra la cartera demo antes de generarla.")
    args = parser.parse_args()

    with Database(path=args.db) as db:
        if args.reset:
            reset(db)
        existing = db.query("select id from portfolios where name = ?", (DEMO_NAME,))
        if existing and not args.reset:
            print(f"  La cartera '{DEMO_NAME}' ya existe. Usa --reset para regenerarla.")
            return 0
        seed(db)

    print("\n  Ahora:  python run.py api")
    print(f"  Y abre http://localhost:8000  (entra directo a '{DEMO_NAME}')\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
