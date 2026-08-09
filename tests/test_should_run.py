"""Tests of `should_run`: when a cycle is worth spending.

This module exists because of a concrete bug: the first version demanded an open
market, and with daily bars that ruled out precisely the best moment of the day
—the stretch after the close, when the session is already complete—. A scheduler
set to 22:15 Madrid time (16:15 ET) skipped EVERY cycle and the agent never
traded again. The cases below pin down the correct semantics.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src import market_calendar as mc

MADRID = ZoneInfo("Europe/Madrid")


def et(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=mc.EASTERN)


# -- Barras diarias ----------------------------------------------------------

def test_daily_runs_right_after_the_close():
    """The case that was broken: 16:15 ET is the natural moment for daily bars,
    with the session just finished."""
    allowed, reason = mc.should_run("1d", et(2026, 8, 10, 16, 15))

    assert allowed
    assert "dia de mercado" in reason


def test_the_users_scheduled_time_actually_runs():
    """22:15 in Madrid is 16:15 ET. It is the scheduler's default configuration,
    so it has to run."""
    madrid_2215 = datetime(2026, 8, 10, 22, 15, tzinfo=MADRID)

    allowed, _ = mc.should_run("1d", madrid_2215)

    assert allowed


def test_daily_runs_during_the_session_too():
    allowed, _ = mc.should_run("1d", et(2026, 8, 10, 12, 0))

    assert allowed


def test_daily_runs_before_the_open():
    """At 6 in the morning yesterday's bar is already complete: there is
    something to analyse."""
    allowed, _ = mc.should_run("1d", et(2026, 8, 10, 6, 0))

    assert allowed


@pytest.mark.parametrize("moment", [
    et(2026, 8, 8, 12, 0),    # sabado
    et(2026, 8, 9, 12, 0),    # domingo
])
def test_daily_skips_weekends(moment):
    """It is the saving that motivated the calendar: with no session, the bars are
    Friday's own and analysing them would be repeating decisions."""
    allowed, reason = mc.should_run("1d", moment)

    assert not allowed
    assert "sin sesion" in reason


def test_daily_skips_holidays():
    allowed, reason = mc.should_run("1d", et(2026, 11, 26, 12, 0))

    assert not allowed
    assert "sin sesion" in reason


# -- Barras horarias ---------------------------------------------------------

def test_hourly_runs_during_the_session():
    allowed, _ = mc.should_run("1h", et(2026, 8, 10, 12, 0))

    assert allowed


def test_hourly_skips_after_the_close():
    """Unlike the daily case: outside the session no new hourly bars arrive, so
    the cycle would repeat the previous analysis."""
    allowed, reason = mc.should_run("1h", et(2026, 8, 10, 16, 15))

    assert not allowed
    assert "sesion viva" in reason


def test_hourly_skips_before_the_open():
    allowed, _ = mc.should_run("1h", et(2026, 8, 10, 8, 0))

    assert not allowed


def test_hourly_skips_weekends():
    allowed, reason = mc.should_run("1h", et(2026, 8, 8, 12, 0))

    assert not allowed
    assert "sin sesion" in reason


def test_hourly_respects_an_early_close():
    """Christmas Eve closes at 13:00 ET."""
    assert mc.should_run("1h", et(2026, 12, 24, 12, 30))[0]
    assert not mc.should_run("1h", et(2026, 12, 24, 13, 30))[0]


# -- Cadencia intradia recomendada -------------------------------------------

@pytest.mark.parametrize("hora_madrid", [16, 18, 20])
def test_the_documented_intraday_schedule_falls_inside_the_session(hora_madrid):
    """`CYCLE_TIMES=16:30,18:30,20:30` is what .env.example recommends for hourly
    bars. If any fell outside the session, it would be a lost cycle."""
    moment = datetime(2026, 8, 10, hora_madrid, 30, tzinfo=MADRID)

    allowed, reason = mc.should_run("1h", moment)

    assert allowed, f"{hora_madrid}:30 Madrid queda fuera de sesion: {reason}"


def test_the_reason_is_always_informative():
    """The reason ends up in the log and in the dashboard: it must never be empty."""
    for interval in ("1d", "1h"):
        for moment in (et(2026, 8, 10, 12), et(2026, 8, 8, 12), et(2026, 8, 10, 20)):
            _, reason = mc.should_run(interval, moment)
            assert reason and len(reason) > 10
