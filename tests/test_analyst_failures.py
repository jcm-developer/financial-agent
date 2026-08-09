"""F6.9: a cycle with no model must not look like a quiet day.

`Analyst` swallows the `LLMError`s on purpose —a 429 on one symbol must not take
the whole cycle down— and until F6.9 that had an expensive side effect: with the
quota exhausted, all 33 calls failed in a row and the cycle ended in 'completed'
with zero proposals, exactly like a session in which the model saw no
opportunity. In a two-week experiment that is ten lost sessions with the history
saying nothing about it.

What is pinned down here is the distinction: how many times it was asked, how
many got no answer, and when that degrades the cycle's status.
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
from src.config import RiskLimits
from src.db import Database
from src.llm import LLMError


class BrokenLLM:
    """It fails the way an exhausted quota fails: on every call.

    It does not inherit from `StubLLM` so it is clear it never answers; the call
    counter is kept because that is what gets compared.
    """

    def __init__(self, *, fail_after: int = 0) -> None:
        #: Cuantas llamadas responde antes de empezar a fallar. 0 = ninguna.
        self.fail_after = fail_after
        self.calls: list[str] = []
        self._ok = StubLLM(entry=BUY, exit_=HOLD_EXIT)

    def complete_json(self, *, system: str, user: str, max_tokens: int = 1600):
        self.calls.append(system[:20])
        if len(self.calls) > self.fail_after:
            raise LLMError("429 Too Many Requests (simulado)")
        return self._ok.complete_json(system=system, user=user, max_tokens=max_tokens)


def _cycle_row(db: Database) -> dict:
    return db.query("select * from cycles order by started_at desc limit 1")[0]


# ----------------------------------------------------------------------
# Total failure: the case that motivates the task
# ----------------------------------------------------------------------

def test_a_cycle_where_every_call_fails_is_not_recorded_as_completed(db):
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, BrokenLLM(), market).run()

    # What used to happen before F6.9: 'completed' and zero proposals.
    assert report.status == "failed"
    assert report.proposals_buy == 0
    assert report.analyst_calls == 2
    assert report.analyst_failures == 2


def test_the_total_failure_is_visible_in_the_history_not_just_in_the_log(db):
    """The log gets lost; the row is what is looked at two weeks later."""
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    make_cycle(db, settings, BrokenLLM(), market).run()

    row = _cycle_row(db)
    assert row["status"] == "failed"
    assert row["analyst_calls"] == 2
    assert row["analyst_failures"] == 2
    assert "no ha analizado nada" in (row["error"] or "")


def test_the_summary_names_the_failures(db):
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, BrokenLLM(), market).run()

    assert "2 de 2 llamadas sin respuesta" in report.summary()


def test_a_healthy_cycle_says_nothing_about_the_analyst(db):
    """A warning that is always on ends up unread."""
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    assert "sin respuesta" not in report.summary()


# ----------------------------------------------------------------------
# Fallo parcial: el ciclo sigue valiendo
# ----------------------------------------------------------------------

def test_a_partial_failure_keeps_the_cycle_valid(db):
    """With 1 failure out of 2 the cycle did analyse and could trade. Marking it
    'failed' would lie in the other direction: it would look as if nothing traded."""
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, BrokenLLM(fail_after=1), market).run()

    assert report.status == "completed"
    assert report.analyst_calls == 2
    assert report.analyst_failures == 1
    # And the one that was analysed made it all the way to an order.
    assert report.orders_submitted == 1


def test_a_partial_failure_still_leaves_a_note_in_the_row(db):
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    make_cycle(db, settings, BrokenLLM(fail_after=1), market).run()

    row = _cycle_row(db)
    assert row["status"] == "completed"
    assert row["analyst_failures"] == 1
    assert "1 de 2" in (row["error"] or "")


# ----------------------------------------------------------------------
# When NOT to degrade
# ----------------------------------------------------------------------

def test_counters_are_written_even_when_nothing_failed(db):
    """0 failures out of 20 calls is information; telling that apart from
    "nothing is known" is the task's goal, so the 0 gets written."""
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    row = _cycle_row(db)
    assert row["status"] == "completed"
    assert row["analyst_calls"] == 2
    assert row["analyst_failures"] == 0


def test_the_kill_switch_keeps_the_headline_of_its_cycle(db):
    """A cycle halted by the daily loss does not evaluate entries by definition,
    so its few calls are not representative: turning 'halted' into 'failed' would
    hide the real reason."""
    settings = make_settings(
        watchlist=("AAPL",),
        risk=RiskLimits(min_conviction=65, max_daily_loss_pct=3.0),
    )
    closes = rising()
    make_cycle(
        db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), StubMarketData({"AAPL": closes})
    ).run()

    db.execute("update sim_accounts set last_equity = 20000")

    report = make_cycle(db, settings, BrokenLLM(), StubMarketData({"AAPL": closes})).run()

    assert report.status == "halted"
    assert report.analyst_failures == report.analyst_calls
    assert report.analyst_failures > 0


def test_a_cycle_that_asked_nothing_is_not_a_failure(db):
    """With no candidates there are no calls, and 0 out of 0 is not a failure.
    Without this distinction, a day on which the screener selects nothing would be
    marked as a broken cycle."""
    settings = make_settings(watchlist=())
    report = make_cycle(db, settings, BrokenLLM(), StubMarketData({})).run()

    assert report.analyst_calls == 0
    assert report.analyst_failures == 0
    assert report.status == "completed"


# ----------------------------------------------------------------------
# Migracion
# ----------------------------------------------------------------------

def test_the_columns_reach_a_database_created_before_them(tmp_path):
    """`create table if not exists` does not add columns to a table that already
    exists. Without `ADDED_COLUMNS`, F6.9 would work on a new database and fail on
    precisely the one carrying the running experiment."""
    path = tmp_path / "vieja.db"
    with Database(path=path) as database:
        database.execute("alter table cycles drop column analyst_calls")
        database.execute("alter table cycles drop column analyst_failures")
        columnas = {c["name"] for c in database.query("pragma table_info(cycles)")}
        assert "analyst_calls" not in columnas

    # Al reabrir, `_add_missing_columns` las repone.
    with Database(path=path) as database:
        columnas = {c["name"] for c in database.query("pragma table_info(cycles)")}
        assert {"analyst_calls", "analyst_failures"} <= columnas
