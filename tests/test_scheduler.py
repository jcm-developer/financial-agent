"""Tests del planificador de ciclos.

Un fallo de planificacion es silencioso — el contenedor parece vivo y no ejecuta
nada, o ejecuta dos veces seguidas — asi que el calculo de la siguiente hora se
prueba explicitamente, incluido el cambio de dia y el de hora.
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
    """Devolver "ahora" provocaria una segunda ejecucion inmediata del ciclo que
    acaba de terminar."""
    now = at(2026, 8, 7, 22, 15)
    assert next_run(now, [(22, 15)]) == at(2026, 8, 8, 22, 15)


def test_next_run_crosses_the_month_boundary():
    assert next_run(at(2026, 8, 31, 23, 0), [(22, 15)]) == at(2026, 9, 1, 22, 15)


def test_next_run_crosses_the_year_boundary():
    assert next_run(at(2026, 12, 31, 23, 0), [(22, 15)]) == at(2027, 1, 1, 22, 15)


def test_next_run_is_timezone_aware():
    """La hora programada es local: en UTC ese mismo instante seria otra hora, y
    el ciclo caeria fuera del cierre de mercado."""
    result = next_run(at(2026, 8, 7, 10, 0), [(22, 15)])

    assert result.tzinfo is MADRID
    assert result.utcoffset().total_seconds() == 7200  # CEST = UTC+2 en agosto


def test_next_run_still_advances_across_a_dst_change():
    """El ultimo domingo de octubre Madrid pasa de UTC+2 a UTC+1. Lo que importa
    es que la siguiente ejecucion siga siendo posterior a la actual."""
    now = at(2026, 10, 24, 23, 0)

    result = next_run(now, [(22, 15)])

    assert result > now
    assert (result.year, result.month, result.day) == (2026, 10, 25)
