"""Tests of the discretionary trailing stop.

This module was born from a real incident too (F9.25): the first cycle run with
`nemotron-3-super` raised the stop on six of nine positions, and the book went
from stops at 3x ATR to stops between 0,48x and 1,20x — one of them above the
live price, which is a position that exits on the next tick.

The rule the incident taught is not "the model must not move the stop": it is
that the distance to the stop is this system's risk unit, so the model may
tighten it but not past a floor. `risk.py` already applied the mirror image of
that on entries (`llm_wider`, "never the other way round"); here it was missing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.config import RiskLimits
from src.cycle import TRAILING_STOP_FLOOR_FRACTION
from src.models import BrokerPosition, MarketSnapshot, Proposal

from helpers import (
    BUY,
    HOLD_EXIT,
    StubLLM,
    StubMarketData,
    make_cycle,
    make_settings,
    rising,
)

ATR = 2.0
PRICE = 100.0


def _cycle(db, **risk_overrides):
    defaults = dict(min_conviction=65, max_open_positions=5, stop_atr_multiple=3.0)
    defaults.update(risk_overrides)
    settings = make_settings(watchlist=("AAPL",), risk=RiskLimits(**defaults))
    return make_cycle(
        db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT),
        StubMarketData({"AAPL": rising()}),
    )


def _snapshot(atr: float | None = ATR) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="AAPL",
        as_of=datetime(2026, 8, 27, tzinfo=timezone.utc),
        price=PRICE,
        indicators={"atr_14": atr},
    )


def _position() -> BrokerPosition:
    return BrokerPosition(
        symbol="AAPL", qty=10, avg_entry_price=90.0, current_price=PRICE,
        market_value=PRICE * 10, unrealized_pl=100.0, unrealized_pl_pct=11.1,
    )


def _exit_proposal(stop: float | None) -> Proposal:
    return Proposal(
        symbol="AAPL", kind="exit", action="hold", conviction=70,
        thesis="La tesis sigue viva.", suggested_stop=stop,
    )


def _stop_after(db, cycle, suggested: float, current: float | None) -> float | None:
    """Runs the trailing stop over one position and returns the stored stop."""
    position_id = db.open_position(
        portfolio_id=cycle.portfolio_id, symbol="AAPL", qty=10,
        entry_price=90.0, stop_price=current, target_price=None,
        thesis="Compra inicial.", horizon_days=45, entry_order_id=None,
    )
    row = {"id": position_id, "stop_price": current}
    cycle._maybe_raise_stop(row, _exit_proposal(suggested), _position(), _snapshot())
    stored = db.query("select stop_price from positions where id = ?", (position_id,))
    return stored[0]["stop_price"]


# ----------------------------------------------------------------------
# El suelo
# ----------------------------------------------------------------------

def test_a_stop_within_the_floor_is_clipped_to_the_floor(db):
    """The measured case: the analyst asks for a stop 0,5 ATR away and gets the
    floor instead, which with a 3x profile is 1,5 ATR."""
    cycle = _cycle(db)
    floor = PRICE - ATR * 3.0 * TRAILING_STOP_FLOOR_FRACTION

    assert _stop_after(db, cycle, suggested=99.0, current=94.0) == floor == 97.0


def test_a_stop_beyond_the_floor_is_honoured_as_asked(db):
    """The floor only clips; it does not become the answer to every suggestion.
    A stop 2 ATR away is further out than the 1,5 ATR floor, so it stands."""
    cycle = _cycle(db)

    assert _stop_after(db, cycle, suggested=96.0, current=94.0) == 96.0


def test_the_floor_follows_the_profile_and_not_a_fixed_number(db):
    """An aggressive profile with a 1,5x stop gets a 0,75x floor: the same
    decision, expressed in its own risk unit."""
    cycle = _cycle(db, stop_atr_multiple=1.5)

    assert _stop_after(db, cycle, suggested=99.5, current=94.0) == 98.5


def test_a_stop_the_floor_does_not_improve_leaves_the_stop_alone(db):
    """With the position already stopped above the floor there is nothing to
    raise, and writing the floor would LOWER the stop — the one thing this
    function has never been allowed to do."""
    cycle = _cycle(db)

    assert _stop_after(db, cycle, suggested=99.0, current=98.0) == 98.0


def test_without_atr_the_suggestion_still_applies(db):
    """No ATR, no floor: the pre-F9.25 behaviour is what is left, and it is
    better than refusing to trail. `_assign_stops_to_orphans` skips the position
    in the same situation for the opposite reason — there it would be inventing
    the whole stop, here it is only failing to bound one."""
    cycle = _cycle(db)
    position_id = db.open_position(
        portfolio_id=cycle.portfolio_id, symbol="AAPL", qty=10,
        entry_price=90.0, stop_price=94.0, target_price=None,
        thesis="Compra inicial.", horizon_days=45, entry_order_id=None,
    )
    row = {"id": position_id, "stop_price": 94.0}

    cycle._maybe_raise_stop(
        row, _exit_proposal(99.0), _position(), _snapshot(atr=None)
    )

    stored = db.query("select stop_price from positions where id = ?", (position_id,))
    assert stored[0]["stop_price"] == 99.0


# ----------------------------------------------------------------------
# Lo que ya valia y no debe romperse
# ----------------------------------------------------------------------

def test_a_stop_above_the_price_is_refused(db):
    """It is not a trailing stop, it is an instant exit disguised as a level."""
    cycle = _cycle(db)

    assert _stop_after(db, cycle, suggested=101.0, current=94.0) == 94.0


def test_the_stop_is_never_widened(db):
    """The premise: the model can only ever reduce the distance to the stop."""
    cycle = _cycle(db)

    assert _stop_after(db, cycle, suggested=92.0, current=94.0) == 94.0


def test_a_position_with_no_stop_yet_accepts_the_suggestion_within_the_floor(db):
    """`current` is NULL on a position adopted without levels, and the floor has
    to hold there too — that is the case with no previous stop to fall back on."""
    cycle = _cycle(db)

    assert _stop_after(db, cycle, suggested=99.0, current=None) == 97.0
