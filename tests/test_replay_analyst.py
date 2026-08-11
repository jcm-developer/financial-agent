"""Tests of the offline analyst replay (F9.15).

What is worth testing here is not the report —it is text— but the three pieces
that decide whether the measurement means anything:

  * `cache_moment`, because taking a snapshot's `as_of` for the instant the cache
    held is an off-by-one that moves every indicator back a bar. It happened, and
    the fidelity check caught it with the RSI drifting 54 %; this is the
    regression test for it.
  * `daily_sigma_pct` and `sigmas`, because the whole expectancy check is that
    arithmetic and a factor of sqrt(252) misplaced would leave every level
    looking sixteen times bigger than it is.
  * `rebuild_snapshot`, because it has to read the interval it is asked for and
    reserve the execution bar exactly as `market_data.build_snapshot` does.

The model is never called: everything below is arithmetic over synthetic bars.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.db import Database
from tools.replay_analyst import (
    BARS_FLOOR,
    TRADING_DAYS,
    bars_to_read,
    cache_moment,
    daily_sigma_pct,
    measure,
    pct_from,
    rebuild_snapshot,
    sigmas,
    summary,
)


@pytest.fixture
def db(tmp_path):
    with Database(path=tmp_path / "replay.db") as database:
        yield database


def seed_bars(db: Database, symbol: str, interval: str, *, count: int,
              start: datetime, step: timedelta, price: float = 100.0) -> None:
    """`count` synthetic bars, each one a cent above the previous."""
    for index in range(count):
        moment = start + step * index
        close = price + index * 0.01
        db.execute(
            "insert or replace into bar_cache "
            "(symbol, interval, ts, open, high, low, close, volume) "
            "values (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, interval, moment.isoformat(), close - 0.05, close + 0.10,
             close - 0.10, close, 1_000_000.0),
        )


# -- cache_moment ------------------------------------------------------------

def test_cache_moment_advances_one_bar():
    """`as_of` names the decision bar, and the cache already held the next one."""
    assert cache_moment("2026-08-10T11:00:00+00:00", "1h") == "2026-08-10T12:00:00+00:00"
    assert cache_moment("2026-08-07T00:00:00+00:00", "1d") == "2026-08-08T00:00:00+00:00"


def test_cache_moment_keeps_the_timezone():
    assert cache_moment("2026-08-10T11:00:00+00:00", "1h").endswith("+00:00")


# -- La aritmetica de las sigmas ---------------------------------------------

def test_daily_sigma_undoes_the_annualisation():
    annual = 31.75
    assert daily_sigma_pct({"volatility_20d_pct": annual}) == pytest.approx(
        annual / (TRADING_DAYS ** 0.5)
    )


@pytest.mark.parametrize("indicators", [{}, {"volatility_20d_pct": None},
                                        {"volatility_20d_pct": 0.0}])
def test_daily_sigma_without_data_is_none(indicators):
    """A missing volatility is None and not zero: zero would divide by zero and,
    worse, would print every level as infinitely many sigmas."""
    assert daily_sigma_pct(indicators) is None


def test_sigmas_scale_with_the_square_root_of_the_horizon():
    """Four times the horizon is twice the sigma, not four times."""
    one_day = sigmas(2.0, 1.0, 1)
    four_days = sigmas(2.0, 1.0, 4)
    assert one_day == pytest.approx(2.0)
    assert four_days == pytest.approx(1.0)


def test_sigmas_ignore_the_sign():
    """A stop is below the price and an objective above it; what is being asked
    is the size of the move, so both come back positive."""
    assert sigmas(-3.0, 1.5, 4) == sigmas(3.0, 1.5, 4)


@pytest.mark.parametrize("move,sigma,horizon", [
    (None, 1.0, 7), (3.0, None, 7), (3.0, 1.0, None), (3.0, 1.0, 0),
])
def test_sigmas_without_data_are_none(move, sigma, horizon):
    assert sigmas(move, sigma, horizon) is None


def test_pct_from_is_signed():
    assert pct_from(100.0, 103.0) == pytest.approx(3.0)
    assert pct_from(100.0, 97.0) == pytest.approx(-3.0)
    assert pct_from(100.0, None) is None


# -- measure -----------------------------------------------------------------

def test_measure_gives_the_multiple_over_the_arms_own_atr():
    """The multiplier the model applies to the ruler it was handed. It is the
    figure that tells a timid model from a short ruler."""
    row = measure(
        arm="1h", symbol="AIR.PA", as_of="2026-08-10T11:00:00+00:00", price=200.0,
        action="buy", conviction=60, horizon_days=14, stop=196.0, target=207.0,
        atr_pct=0.5, daily_sigma=1.0,
    )
    assert row["target_pct"] == pytest.approx(3.5)
    assert row["stop_pct"] == pytest.approx(-2.0)
    assert row["target_over_atr"] == pytest.approx(7.0)
    assert row["target_sigmas"] == pytest.approx(3.5 / (14 ** 0.5))
    assert row["stop_sigmas"] == pytest.approx(2.0 / (14 ** 0.5))


# -- summary -----------------------------------------------------------------

def test_summary_reports_the_quartiles():
    stats = summary([1.0, 2.0, 3.0, 4.0, 5.0])
    assert stats == {"n": 5, "min": 1.0, "p25": 2.0, "median": 3.0, "p75": 4.0,
                     "max": 5.0, "mean": 3.0}


def test_summary_of_nothing_is_none():
    assert summary([]) is None


# -- rebuild_snapshot --------------------------------------------------------

def test_rebuild_reserves_the_execution_bar(db):
    """The last bar is the execution price and the one before it is the decision.

    Same rule as `market_data.build_snapshot`, and it is checked here because the
    replay is what chooses which bars to hand it.
    """
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    seed_bars(db, "AIR.PA", "1d", count=80, start=start, step=timedelta(days=1))
    last = start + timedelta(days=79)

    snapshot = rebuild_snapshot(db, "AIR.PA", last.isoformat(), "1d")

    assert snapshot is not None
    assert snapshot.as_of == last - timedelta(days=1)
    assert snapshot.price == pytest.approx(100.0 + 78 * 0.01)
    assert snapshot.indicators["bars_available"] == 79


def test_rebuild_ignores_bars_after_the_moment(db):
    """The point of the replay: what was known then, not what is known now."""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    seed_bars(db, "AIR.PA", "1d", count=120, start=start, step=timedelta(days=1))
    cut = start + timedelta(days=79)

    snapshot = rebuild_snapshot(db, "AIR.PA", cut.isoformat(), "1d")

    assert snapshot is not None
    assert snapshot.indicators["bars_available"] == 79


def test_rebuild_reads_the_interval_it_is_asked_for(db):
    """Both intervals live in the same table and are told apart by a column: a
    replay that mixed them would compare an interval with itself."""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    seed_bars(db, "AIR.PA", "1d", count=80, start=start, step=timedelta(days=1),
              price=100.0)
    seed_bars(db, "AIR.PA", "1h", count=80, start=start, step=timedelta(hours=1),
              price=500.0)
    moment = start + timedelta(days=79)

    daily = rebuild_snapshot(db, "AIR.PA", moment.isoformat(), "1d")
    hourly = rebuild_snapshot(db, "AIR.PA", moment.isoformat(), "1h")

    assert daily is not None and hourly is not None
    assert daily.price == pytest.approx(100.0 + 78 * 0.01)
    assert hourly.price == pytest.approx(500.0 + 78 * 0.01)


def test_rebuild_reads_only_the_bars_the_funnel_would(db):
    """`bars_available` reaches the model, so reading more than the cycle read
    would change a figure in the prompt without changing the interval."""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    seed_bars(db, "AIR.PA", "1h", count=400, start=start, step=timedelta(hours=1))
    last = start + timedelta(hours=399)

    snapshot = rebuild_snapshot(db, "AIR.PA", last.isoformat(), "1h")

    assert snapshot is not None
    assert snapshot.indicators["bars_available"] == BARS_FLOOR - 1


class _FakeSettings:
    def __init__(self, lookback_days: int) -> None:
        self.lookback_days = lookback_days


@pytest.mark.parametrize("lookback,expected", [(200, 260), (260, 260), (400, 400)])
def test_bars_to_read_mirrors_the_funnel(lookback, expected):
    """Same expression as `universe_data.fetch_snapshots`: max(lookback, 260)."""
    assert bars_to_read(_FakeSettings(lookback)) == expected


def test_rebuild_without_enough_bars_is_none(db):
    """Below the 60-bar floor `build_snapshot` refuses, and the replay skips the
    symbol instead of comparing against a degraded bundle."""
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    seed_bars(db, "AIR.PA", "1d", count=10, start=start, step=timedelta(days=1))

    snapshot = rebuild_snapshot(
        db, "AIR.PA", (start + timedelta(days=9)).isoformat(), "1d"
    )

    assert snapshot is None
