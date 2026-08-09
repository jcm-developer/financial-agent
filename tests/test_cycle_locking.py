"""Tests of the concurrent-cycle lock.

This module was born from a real incident: a cycle was launched by hand while the
scheduler was starting its own, and both began two seconds apart over the same
book. Two cycles in parallel step on each other's cash and positions, and leave a
history with duplicated decisions that can no longer be interpreted.

The other side of the problem is just as important: a process that dies mid-run
leaves its row in 'running' forever, and with no expiry that corpse would block
the agent indefinitely.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.cycle import STALE_CYCLE_MINUTES
from src.db import Database

from helpers import (
    BUY,
    HOLD_EXIT,
    StubLLM,
    StubMarketData,
    make_cycle,
    make_settings,
    rising,
)


def a_running_cycle(database: Database, portfolio_id: str, *, minutes_ago: float):
    """Inserts a cycle hanging in 'running' with whatever age is asked for."""
    cycle_id = database.start_cycle(
        portfolio_id=portfolio_id, equity_start=10_000.0, cash_start=10_000.0,
        market_open=True, symbols=["AAPL"], llm_model="stub",
    )
    started = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    database.execute(
        "update cycles set started_at = ? where id = ?",
        (started.isoformat(), cycle_id),
    )
    return cycle_id


def portfolio_of(database: Database) -> str:
    return database.query("select id from portfolios limit 1")[0]["id"]


# -- Consultas de la capa de datos -------------------------------------------

def test_find_running_cycle_returns_nothing_when_idle(db):
    portfolio = db.ensure_portfolio(name="p", mode="paper", initial_budget=1000.0)

    assert db.find_running_cycle(portfolio) is None


def test_find_running_cycle_spots_an_open_one(db):
    portfolio = db.ensure_portfolio(name="p", mode="paper", initial_budget=1000.0)
    cycle_id = a_running_cycle(db, portfolio, minutes_ago=1)

    found = db.find_running_cycle(portfolio)

    assert found is not None and found["id"] == cycle_id


def test_a_finished_cycle_does_not_block(db):
    portfolio = db.ensure_portfolio(name="p", mode="paper", initial_budget=1000.0)
    cycle_id = a_running_cycle(db, portfolio, minutes_ago=1)
    db.finish_cycle(cycle_id, status="completed", equity_end=10_000.0)

    assert db.find_running_cycle(portfolio) is None


def test_a_cycle_of_another_portfolio_does_not_block(db):
    """Dos carteras distintas pueden operar a la vez sin problema."""
    first = db.ensure_portfolio(name="a", mode="paper", initial_budget=1000.0)
    second = db.ensure_portfolio(name="b", mode="paper", initial_budget=1000.0)
    a_running_cycle(db, first, minutes_ago=1)

    assert db.find_running_cycle(second) is None


def test_abandon_cycle_marks_it_failed_with_the_reason(db):
    portfolio = db.ensure_portfolio(name="p", mode="paper", initial_budget=1000.0)
    cycle_id = a_running_cycle(db, portfolio, minutes_ago=200)

    db.abandon_cycle(cycle_id, "murio el contenedor")

    row = db.query("select status, error, finished_at from cycles")[0]
    assert row["status"] == "failed"
    assert "murio el contenedor" in row["error"]
    assert row["finished_at"] is not None
    assert db.find_running_cycle(portfolio) is None


# -- Comportamiento del ciclo ------------------------------------------------

def test_a_second_cycle_refuses_to_start(db):
    """The exact incident: a cycle by hand while the scheduler runs."""
    settings = make_settings(watchlist=("AAPL",))
    cycle = make_cycle(
        db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT),
        StubMarketData({"AAPL": rising()}),
    )
    a_running_cycle(db, portfolio_of(db), minutes_ago=3)

    report = cycle.run()

    assert report.status == "skipped"
    assert "ya hay un ciclo en marcha" in report.halted_reason.lower()
    # Nothing was opened: the lock acts before the broker is touched.
    assert db.query("select * from sim_positions") == []


def test_the_blocked_cycle_does_not_download_anything(db):
    """The lock comes before the data download, which is the expensive part: with
    the funnel that is minutes and hundreds of thousands of bars."""
    class CountingMarketData(StubMarketData):
        calls = 0

        def fetch_snapshots(self, must_include=()):
            CountingMarketData.calls += 1
            return super().fetch_snapshots(must_include)

    settings = make_settings(watchlist=("AAPL",))
    cycle = make_cycle(
        db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT),
        CountingMarketData({"AAPL": rising()}),
    )
    a_running_cycle(db, portfolio_of(db), minutes_ago=3)

    cycle.run()

    assert CountingMarketData.calls == 0


def test_a_stale_cycle_is_abandoned_and_stops_blocking(db):
    """A container that dies leaves the row in 'running'. Past the deadline it is
    presumed dead and the agent starts working again on its own."""
    settings = make_settings(watchlist=("AAPL",))
    cycle = make_cycle(
        db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT),
        StubMarketData({"AAPL": rising()}),
    )
    stale_id = a_running_cycle(
        db, portfolio_of(db), minutes_ago=STALE_CYCLE_MINUTES + 10
    )

    report = cycle.run()

    assert report.status == "completed"
    stale = db.query("select status, error from cycles where id = ?", (stale_id,))[0]
    assert stale["status"] == "failed"
    assert "abandonado" in stale["error"].lower()


def test_a_cycle_just_under_the_limit_still_blocks(db):
    """The expiry must not arrive early: a slow but living cycle would still be
    working, and abandoning it would leave two running."""
    settings = make_settings(watchlist=("AAPL",))
    cycle = make_cycle(
        db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT),
        StubMarketData({"AAPL": rising()}),
    )
    a_running_cycle(db, portfolio_of(db), minutes_ago=STALE_CYCLE_MINUTES - 5)

    assert cycle.run().status == "skipped"


def test_the_stale_limit_leaves_room_for_a_real_cycle():
    """A cycle with the funnel takes ~20 minutes; the limit has to sit comfortably
    above that so legitimate runs are not killed."""
    assert STALE_CYCLE_MINUTES >= 60


def test_consecutive_cycles_work_normally(db):
    """The lock must not get in the way of the normal case: one cycle after another."""
    settings = make_settings(watchlist=("AAPL",))
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)

    first = make_cycle(db, settings, llm, StubMarketData({"AAPL": rising()})).run()
    second = make_cycle(db, settings, llm, StubMarketData({"AAPL": rising(81)})).run()

    assert first.status == "completed"
    assert second.status == "completed"
