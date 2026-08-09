"""Tests of the market calendar.

A misconfigured calendar is silent in the worst direction: the agent runs at 3 in
the morning over stale data, or skips whole sessions and nobody notices. Every
case uses fixed dates, never the clock.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src import market_calendar as mc

MADRID = ZoneInfo("Europe/Madrid")


def et(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=mc.EASTERN)


# -- Dias de mercado ---------------------------------------------------------

def test_a_regular_weekday_is_a_trading_day():
    # Miercoles 5 de agosto de 2026.
    assert mc.is_trading_day(date(2026, 8, 5))


@pytest.mark.parametrize("day", [date(2026, 8, 8), date(2026, 8, 9)])
def test_weekends_are_not_trading_days(day):
    assert not mc.is_trading_day(day)


@pytest.mark.parametrize("day", [
    date(2026, 1, 1),    # Ano Nuevo
    date(2026, 7, 3),    # Independencia observado (el 4 es sabado)
    date(2026, 11, 26),  # Accion de Gracias
    date(2026, 12, 25),  # Navidad
])
def test_holidays_are_not_trading_days(day):
    assert not mc.is_trading_day(day)


def test_a_holiday_falling_on_a_weekend_is_observed_on_a_weekday():
    """4 July 2026 is a Saturday, so NYSE closes on Friday the 3rd."""
    assert not mc.is_trading_day(date(2026, 7, 3))
    assert mc.is_trading_day(date(2026, 7, 6))


# -- Horario de sesion -------------------------------------------------------

def test_the_session_is_open_at_midday():
    assert mc.is_session_open(et(2026, 8, 5, 12, 0))


def test_the_session_is_closed_before_the_opening_bell():
    assert not mc.is_session_open(et(2026, 8, 5, 9, 29))


def test_the_session_opens_exactly_at_930():
    assert mc.is_session_open(et(2026, 8, 5, 9, 30))


def test_the_session_is_closed_at_the_closing_bell():
    """At 16:00 sharp it is already closed: the interval is open at the top."""
    assert not mc.is_session_open(et(2026, 8, 5, 16, 0))
    assert mc.is_session_open(et(2026, 8, 5, 15, 59))


def test_the_session_is_closed_at_three_in_the_morning():
    """The case that motivated this module."""
    assert not mc.is_session_open(et(2026, 8, 5, 3, 0))


def test_the_session_is_closed_on_a_saturday_at_midday():
    assert not mc.is_session_open(et(2026, 8, 8, 12, 0))


def test_early_close_days_shut_at_one_pm():
    """Christmas Eve 2026 closes at 13:00."""
    assert mc.is_session_open(et(2026, 12, 24, 12, 30))
    assert not mc.is_session_open(et(2026, 12, 24, 13, 30))
    # Un dia normal a esa hora sigue abierto.
    assert mc.is_session_open(et(2026, 12, 23, 13, 30))


# -- Zonas horarias ----------------------------------------------------------

def test_a_madrid_datetime_is_converted_before_comparing():
    """18:00 in Madrid is 12:00 in New York: market open. Comparing the local
    time against Eastern hours without converting would give closed."""
    madrid_noon_ny = datetime(2026, 8, 5, 18, 0, tzinfo=MADRID)

    assert mc.is_session_open(madrid_noon_ny)


def test_a_madrid_morning_is_before_the_us_open():
    assert not mc.is_session_open(datetime(2026, 8, 5, 10, 0, tzinfo=MADRID))


def test_a_naive_datetime_is_read_as_eastern():
    """Reading it as UTC would shift the sessions by five hours without warning."""
    assert mc.is_session_open(datetime(2026, 8, 5, 12, 0))


# -- Navegacion --------------------------------------------------------------

def test_last_trading_day_on_a_weekend_points_to_friday():
    assert mc.last_trading_day(et(2026, 8, 8, 12, 0)) == date(2026, 8, 7)


def test_last_trading_day_before_the_open_points_to_yesterday():
    assert mc.last_trading_day(et(2026, 8, 5, 8, 0)) == date(2026, 8, 4)


def test_last_trading_day_after_the_open_is_today():
    assert mc.last_trading_day(et(2026, 8, 5, 10, 0)) == date(2026, 8, 5)


def test_last_trading_day_skips_a_holiday():
    """26 November 2026 is Thanksgiving (a Thursday)."""
    assert mc.last_trading_day(et(2026, 11, 26, 12, 0)) == date(2026, 11, 25)


def test_next_session_open_from_a_friday_evening_is_monday():
    upcoming = mc.next_session_open(et(2026, 8, 7, 20, 0))

    assert upcoming.date() == date(2026, 8, 10)
    assert upcoming.hour == 9 and upcoming.minute == 30


def test_next_session_open_before_the_bell_is_today():
    upcoming = mc.next_session_open(et(2026, 8, 5, 6, 0))

    assert upcoming.date() == date(2026, 8, 5)


def test_next_session_open_skips_the_new_year_holiday():
    """1 January 2027 is a holiday and falls on a Friday."""
    upcoming = mc.next_session_open(et(2026, 12, 31, 20, 0))

    assert upcoming.date() == date(2027, 1, 4)


# -- Descripcion -------------------------------------------------------------

def test_describe_says_open_and_how_long_is_left():
    text = mc.describe(et(2026, 8, 5, 15, 0))

    assert "abierto" in text
    assert "60 min" in text


def test_describe_says_closed_and_when_it_opens():
    text = mc.describe(et(2026, 8, 8, 12, 0))

    assert "cerrado" in text
    assert "abre en" in text


def test_describe_flags_a_half_session():
    assert "media sesion" in mc.describe(et(2026, 12, 24, 12, 0))


# -- Cobertura de la tabla ---------------------------------------------------

def test_dates_beyond_the_table_warn_instead_of_lying(caplog):
    """Past LAST_COVERED_YEAR only weekends are known. The warning is what stops
    an incomplete calendar being trusted without noticing."""
    with caplog.at_level("WARNING"):
        result = mc.is_trading_day(date(mc.LAST_COVERED_YEAR + 1, 1, 1))

    # 1 January: the table does not cover it, so it counts it a working day unless it is a weekend.
    assert result is (date(mc.LAST_COVERED_YEAR + 1, 1, 1).weekday() < 5)
    assert any("solo cubre hasta" in record.message for record in caplog.records)


def test_holidays_do_not_fall_on_weekends():
    """NYSE moves the holidays that fall at a weekend. If any in the table falls
    on a Saturday or Sunday, the nominal date was copied instead of the observed one."""
    misplaced = [day for day in mc.HOLIDAYS if day.weekday() >= 5]

    assert misplaced == []


def test_early_closes_are_trading_days():
    """A half session is a session: if it were also in HOLIDAYS, the agent would
    skip a day on which trading is possible."""
    for day in mc.EARLY_CLOSES:
        assert mc.is_trading_day(day), day
