"""Closing an experiment: liquidating the book at the end (F5.8).

What matters here is not that the positions disappear —that would also happen
with an `UPDATE`— but that they disappear **the right way**: through the broker,
leaving orders, exit prices and reasons behind. A position closed by hand in the
database leaves a history that no longer explains the result it is being read
for.
"""

from __future__ import annotations

from helpers import (
    BUY,
    HOLD_EXIT,
    WATCHLIST,
    StubLLM,
    StubMarketData,
    make_cycle,
    make_settings,
    rising,
)
from src.db import Database


def open_two_positions(db: Database):
    """Runs a normal cycle so there is something real to close."""
    settings = make_settings()
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    market = StubMarketData({s: rising() for s in WATCHLIST})
    cycle = make_cycle(db, settings, llm, market)
    report = cycle.run()
    assert report.orders_submitted == 2
    return settings, llm, market


def test_closing_sells_every_open_position(db):
    settings, llm, market = open_two_positions(db)
    cycle = make_cycle(db, settings, llm, market)

    report = cycle.close_all_positions()

    assert report.status == "completed"
    assert report.exits_forced == 2
    portfolio_id = cycle.portfolio_id
    assert db.get_open_positions(portfolio_id) == {}


def test_the_close_leaves_orders_and_a_reason_behind(db):
    """This is the whole point of going through the broker.

    Without the order and the exit price, the closed position says "it is no
    longer there" and not "it was sold at 104.20 because the experiment ended",
    and the second is the one the analytics read.
    """
    settings, llm, market = open_two_positions(db)
    cycle = make_cycle(db, settings, llm, market)

    cycle.close_all_positions()

    closed = db.query(
        "select * from positions where portfolio_id = ? and status = 'closed'",
        (cycle.portfolio_id,),
    )
    assert len(closed) == 2
    for row in closed:
        assert row["exit_price"] is not None
        assert row["exit_order_id"] is not None
        assert "experiment_closed" in (row["exit_reason"] or "")

    sells = db.query(
        "select * from orders where portfolio_id = ? and side = 'sell'",
        (cycle.portfolio_id,),
    )
    assert len(sells) == 2
    assert all(row["status"] == "filled" for row in sells)


def test_the_model_is_not_consulted(db):
    """It is not a decision about the market: the experiment is over.

    Asking the analyst would record a "sell" as if it had been judged, and it
    was not — besides spending quota on a foregone conclusion.
    """
    settings, llm, market = open_two_positions(db)
    llm.calls.clear()
    cycle = make_cycle(db, settings, llm, market)

    cycle.close_all_positions()

    assert llm.calls == []


def test_the_close_is_recorded_as_a_cycle(db):
    """So the liquidation appears in the history, which is where it is read."""
    settings, llm, market = open_two_positions(db)
    cycle = make_cycle(db, settings, llm, market)

    report = cycle.close_all_positions()

    assert report.cycle_id is not None
    row = db.query("select * from cycles where id = ?", (report.cycle_id,))[0]
    assert row["status"] == "completed"
    # The settings it ran with travel with it, like any other cycle (F6.3).
    assert row["settings_json"]

    snapshots = db.query(
        "select * from equity_snapshots where cycle_id = ?", (report.cycle_id,)
    )
    assert len(snapshots) == 1
    assert snapshots[0]["open_positions"] == 0


def test_closing_with_nothing_open_does_nothing_and_says_so(db):
    """"There was nothing to close" is not a failure, and must not write an
    empty cycle into the history."""
    settings = make_settings()
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    market = StubMarketData({s: rising() for s in WATCHLIST})
    cycle = make_cycle(db, settings, llm, market)

    report = cycle.close_all_positions()

    assert report.status == "skipped"
    assert "posicion abierta" in (report.halted_reason or "")
    assert report.cycle_id is None
    assert db.query("select count(1) as n from cycles")[0]["n"] == 0


def test_dry_run_refuses_instead_of_faking_the_sale(db):
    """With `dry_run` there is no execution, so there is no closure either.

    It is refused up front rather than recording two cancelled orders: the
    positions stay open, and saying so is what stops someone reading a book that
    was never liquidated as a final result.
    """
    settings, llm, market = open_two_positions(db)
    dry = make_settings(dry_run=True)
    cycle = make_cycle(db, dry, llm, market)

    report = cycle.close_all_positions()

    assert report.status == "skipped"
    assert "DRY_RUN" in (report.halted_reason or "")
    assert len(db.get_open_positions(cycle.portfolio_id)) == 2
