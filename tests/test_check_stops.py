"""Checking stops between cycles, without the model (F9.36).

The analysing cycle runs once a day, and until this the stops were only checked
inside it: a position that fell through its stop at 11:00 was sold at 10:20 the
next day. What matters here is what would fail silently: that the model is never
asked, that a check that finds nothing leaves no trace in the history, and that
it looks only at what is held instead of screening the universe.
"""

from __future__ import annotations

import json

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
    """Runs a normal cycle so there are positions with real stops."""
    settings = make_settings()
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    market = StubMarketData({s: rising() for s in WATCHLIST})
    report = make_cycle(db, settings, llm, market).run()
    assert report.orders_submitted == 2
    return settings, llm, market


def crash(market: StubMarketData, symbol: str) -> None:
    """Halves the symbol's price: two bars, because the decision price is the
    last *complete* one and the last is reserved for execution."""
    last = market.closes[symbol][-1]
    market.closes[symbol] = market.closes[symbol] + [last * 0.5, last * 0.5]


def cycle_count(db: Database) -> int:
    return db.query("select count(1) as n from cycles")[0]["n"]


def test_a_stop_hit_between_cycles_is_sold(db):
    settings, llm, market = open_two_positions(db)
    crash(market, "AAPL")
    cycle = make_cycle(db, settings, llm, market)

    report = cycle.check_stops()

    assert report.status == "completed"
    assert report.exits_forced == 1
    assert set(db.get_open_positions(cycle.portfolio_id)) == {"MSFT"}
    closed = db.query(
        "select exit_reason from positions where portfolio_id = ? and status = 'closed'",
        (cycle.portfolio_id,),
    )
    assert "stop_loss_hit" in closed[0]["exit_reason"]


def test_the_model_is_never_asked(db):
    settings, llm, market = open_two_positions(db)
    crash(market, "AAPL")
    llm.calls.clear()

    make_cycle(db, settings, llm, market).check_stops()

    assert llm.calls == []


def test_an_exit_is_recorded_as_a_cycle_without_a_model(db):
    """The order and the position need a cycle; `llm_model` NULL and
    `cycle_kind` tell it apart from an analysing one."""
    settings, llm, market = open_two_positions(db)
    crash(market, "AAPL")

    report = make_cycle(db, settings, llm, market).check_stops()

    row = db.query("select * from cycles where id = ?", (report.cycle_id,))[0]
    assert row["llm_model"] is None
    assert json.loads(row["settings_json"])["cycle_kind"] == "stop_check"
    assert db.query(
        "select count(1) as n from equity_snapshots where cycle_id = ?", (report.cycle_id,)
    )[0]["n"] == 1


def test_a_check_that_finds_nothing_writes_nothing(db):
    """Seven checks a day would otherwise fill the history with empty cycles."""
    settings, llm, market = open_two_positions(db)
    before = cycle_count(db)

    report = make_cycle(db, settings, llm, market).check_stops()

    assert report.status == "skipped"
    assert "Ningún stop" in (report.halted_reason or "")
    assert report.cycle_id is None
    assert cycle_count(db) == before


def test_only_the_held_symbols_are_fetched(db):
    """No screening: the universe is not what a stop check needs."""
    settings, llm, market = open_two_positions(db)
    market.requests.clear()

    make_cycle(db, settings, llm, market).check_stops()

    assert market.requests == [("positions", ("AAPL", "MSFT"))]


def test_with_nothing_open_it_does_nothing(db):
    settings = make_settings()
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, llm, market).check_stops()

    assert report.status == "skipped"
    assert market.requests == []
    assert cycle_count(db) == 0


def test_dry_run_checks_nothing(db):
    settings, llm, market = open_two_positions(db)
    crash(market, "AAPL")

    cycle = make_cycle(db, make_settings(dry_run=True), llm, market)
    report = cycle.check_stops()

    assert report.status == "skipped"
    assert "DRY_RUN" in (report.halted_reason or "")
    assert len(db.get_open_positions(cycle.portfolio_id)) == 2
