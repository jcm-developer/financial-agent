"""Tests of the cycle scheduler.

A scheduling failure is silent — the container looks alive and runs nothing, or
runs twice in a row — so the calculation of the next time is tested explicitly,
including the day rollover and the daylight-saving change.

Since F6.10 the plan comes from the database and not from the environment, so
what is checked as well is that only the **active** profiles are scheduled, that
each one carries its own times, and that a profile with a bad schedule is skipped
instead of taking the scheduler down with it.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.db import Database
from tools.scheduler import ScheduleError, load_plans, next_run, parse_times

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


@pytest.mark.parametrize("raw", ["", "   ", ",,", None])
def test_empty_configuration_is_rejected(raw):
    with pytest.raises(ScheduleError):
        parse_times(raw)


@pytest.mark.parametrize("raw", ["22", "22h15", "abc", "22:15:30"])
def test_malformed_time_is_rejected(raw):
    with pytest.raises(ScheduleError, match="invalida"):
        parse_times(raw)


@pytest.mark.parametrize("raw", ["24:00", "22:60", "-1:00"])
def test_out_of_range_time_is_rejected(raw):
    with pytest.raises(ScheduleError):
        parse_times(raw)


def test_a_bad_schedule_is_no_longer_fatal():
    """It used to be `SystemExit`, and since F6.8 that is a loaded gun.

    `cycle_times` is a field the interface writes, so a typo in one profile would
    have taken the scheduler down for every other experiment — and the symptom is
    a container that looks alive and runs nothing.
    """
    assert issubclass(ScheduleError, ValueError)
    assert not issubclass(ScheduleError, SystemExit)


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


# -- El plan sale de la base, no del entorno (F6.10) --------------------------

@pytest.fixture
def db(tmp_path):
    with Database(path=tmp_path / "sched.db") as database:
        yield database


def make_profile(db, name, *, status="active", **settings):
    profile_id = db.create_profile(name=name)
    if settings:
        db.update_settings(profile_id, settings)
    db.set_profile_status(profile_id, status)
    return profile_id


def test_only_the_active_profiles_are_scheduled(db):
    """This is the whole point of F6.10: which experiment runs is decided by the
    interface —activating and pausing— and not by a variable in the .env that has
    to be edited and redeployed."""
    make_profile(db, "vivo", status="active", cycle_times="17:40")
    make_profile(db, "pausado", status="paused", cycle_times="17:40")
    make_profile(db, "borrador", status="draft", cycle_times="17:40")
    make_profile(db, "archivado", status="archived", cycle_times="17:40")

    plans = load_plans(db)

    assert [plan.profile for plan in plans] == ["vivo"]


def test_each_profile_carries_its_own_times(db):
    """With one set of hours for everyone, a European experiment with three
    intraday cycles and an American one at the close could not both be
    expressed."""
    make_profile(db, "europa", cycle_times="11:20,14:20,17:40", market="eu")
    make_profile(db, "usa", cycle_times="22:15", market="us")

    plans = {plan.profile: plan for plan in load_plans(db)}

    assert plans["europa"].times == ((11, 20), (14, 20), (17, 40))
    assert plans["usa"].times == ((22, 15),)


def test_a_profile_with_a_bad_schedule_is_skipped_not_fatal(db):
    """One typo must not leave every other experiment unscheduled."""
    make_profile(db, "roto", cycle_times="a las cinco")
    make_profile(db, "bueno", cycle_times="17:40")

    plans = load_plans(db)

    assert [plan.profile for plan in plans] == ["bueno"]


def test_an_unknown_timezone_falls_back_to_utc_without_dropping_the_profile(db):
    """Losing the hour is bad; losing the experiment is worse, and the log says
    which one happened."""
    make_profile(db, "rara", cycle_times="17:40", cycle_tz="Marte/Olympus")

    plans = load_plans(db)

    assert [plan.profile for plan in plans] == ["rara"]
    assert plans[0].tz_name == "UTC"


def test_the_timezone_is_the_profiles_own(db):
    make_profile(db, "madrid", cycle_times="17:40", cycle_tz="Europe/Madrid")

    assert load_plans(db)[0].tz == ZoneInfo("Europe/Madrid")
