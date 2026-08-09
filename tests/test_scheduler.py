"""Tests of the cycle scheduler.

A scheduling failure is silent — the container looks alive and runs nothing, or
runs twice in a row — so the calculation of the next time is tested explicitly,
including the day rollover and the daylight-saving change.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tools.scheduler import next_run, parse_times

MADRID = ZoneInfo("Europe/Madrid")


def at(year, month, day, hour, minute, tz=MADRID):
    return datetime(year, month, day, hour, minute, tzinfo=tz)


# -- parse_times -------------------------------------------------------------

def test_single_time():
    assert parse_times("22:15") == [(22, 15)]


def test_several_times_are_sorted_and_deduplicated():
    assert parse_times("22:15, 09:30 ,22:15") == [(9, 30), (22, 15)]


def test_whitespace_and_trailing_commas_are_tolerated():
    assert parse_times("  09:30 , ") == [(9, 30)]


def test_midnight_is_valid():
    assert parse_times("00:00") == [(0, 0)]


@pytest.mark.parametrize("raw", ["", "   ", ",,"])
def test_empty_configuration_is_rejected(raw):
    with pytest.raises(SystemExit, match="vacio"):
        parse_times(raw)


@pytest.mark.parametrize("raw", ["22", "22h15", "abc", "22:15:30"])
def test_malformed_time_is_rejected(raw):
    with pytest.raises(SystemExit, match="invalido"):
        parse_times(raw)


@pytest.mark.parametrize("raw", ["24:00", "22:60", "-1:00"])
def test_out_of_range_time_is_rejected(raw):
    with pytest.raises(SystemExit):
        parse_times(raw)


# -- next_run ----------------------------------------------------------------

def test_next_run_picks_todays_time_when_still_ahead():
    assert next_run(at(2026, 8, 7, 10, 0), [(22, 15)]) == at(2026, 8, 7, 22, 15)


def test_next_run_rolls_over_to_tomorrow_when_the_time_has_passed():
    assert next_run(at(2026, 8, 7, 23, 0), [(22, 15)]) == at(2026, 8, 8, 22, 15)


def test_next_run_chooses_the_earliest_upcoming_time():
    times = [(9, 30), (15, 35), (22, 15)]
    assert next_run(at(2026, 8, 7, 10, 0), times) == at(2026, 8, 7, 15, 35)


def test_next_run_never_returns_the_current_instant():
    """Returning "now" would cause an immediate second run of the cycle that has
    just finished."""
    now = at(2026, 8, 7, 22, 15)
    assert next_run(now, [(22, 15)]) == at(2026, 8, 8, 22, 15)


def test_next_run_crosses_the_month_boundary():
    assert next_run(at(2026, 8, 31, 23, 0), [(22, 15)]) == at(2026, 9, 1, 22, 15)


def test_next_run_crosses_the_year_boundary():
    assert next_run(at(2026, 12, 31, 23, 0), [(22, 15)]) == at(2027, 1, 1, 22, 15)


def test_next_run_is_timezone_aware():
    """The scheduled time is local: in UTC that same instant would be another
    hour, and the cycle would fall outside the market close."""
    result = next_run(at(2026, 8, 7, 10, 0), [(22, 15)])

    assert result.tzinfo is MADRID
    assert result.utcoffset().total_seconds() == 7200  # CEST = UTC+2 en agosto


def test_next_run_still_advances_across_a_dst_change():
    """On the last Sunday of October Madrid goes from UTC+2 to UTC+1. What
    matters is that the next run is still later than the current one."""
    now = at(2026, 10, 24, 23, 0)

    result = next_run(now, [(22, 15)])

    assert result > now
    assert (result.year, result.month, result.day) == (2026, 10, 25)
