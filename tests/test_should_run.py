"""Tests de `should_run`: cuando merece la pena gastar un ciclo.

Este modulo existe por un fallo concreto: la primera version exigia mercado
abierto, y con barras diarias eso descartaba justo el mejor momento del dia —el
rato posterior al cierre, cuando la sesion ya esta completa—. Un planificador
puesto a las 22:15 de Madrid (16:15 ET) se saltaba TODOS los ciclos y el agente no
volvia a operar. Los casos de abajo fijan la semantica correcta.
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
    """El caso que estaba roto: 16:15 ET es el momento natural para barras
    diarias, con la sesion recien terminada."""
    allowed, reason = mc.should_run("1d", et(2026, 8, 10, 16, 15))

    assert allowed
    assert "dia de mercado" in reason


def test_the_users_scheduled_time_actually_runs():
    """22:15 en Madrid son 16:15 ET. Es la configuracion por defecto del
    planificador, asi que tiene que ejecutarse."""
    madrid_2215 = datetime(2026, 8, 10, 22, 15, tzinfo=MADRID)

    allowed, _ = mc.should_run("1d", madrid_2215)

    assert allowed


def test_daily_runs_during_the_session_too():
    allowed, _ = mc.should_run("1d", et(2026, 8, 10, 12, 0))

    assert allowed


def test_daily_runs_before_the_open():
    """A las 6 de la manana la barra de ayer ya esta completa: hay algo que
    analizar."""
    allowed, _ = mc.should_run("1d", et(2026, 8, 10, 6, 0))

    assert allowed


@pytest.mark.parametrize("moment", [
    et(2026, 8, 8, 12, 0),    # sabado
    et(2026, 8, 9, 12, 0),    # domingo
])
def test_daily_skips_weekends(moment):
    """Es el ahorro que motivo el calendario: sin sesion, las barras son las
    mismas del viernes y analizarlas seria repetir decisiones."""
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
    """A diferencia del diario: fuera de sesion no llegan barras horarias nuevas,
    asi que el ciclo repetiria el analisis anterior."""
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
    """La vispera de Navidad cierra a las 13:00 ET."""
    assert mc.should_run("1h", et(2026, 12, 24, 12, 30))[0]
    assert not mc.should_run("1h", et(2026, 12, 24, 13, 30))[0]


# -- Cadencia intradia recomendada -------------------------------------------

@pytest.mark.parametrize("hora_madrid", [16, 18, 20])
def test_the_documented_intraday_schedule_falls_inside_the_session(hora_madrid):
    """`CYCLE_TIMES=16:30,18:30,20:30` es lo que recomienda el .env.example para
    barras horarias. Si alguna cayera fuera de sesion, seria un ciclo perdido."""
    moment = datetime(2026, 8, 10, hora_madrid, 30, tzinfo=MADRID)

    allowed, reason = mc.should_run("1h", moment)

    assert allowed, f"{hora_madrid}:30 Madrid queda fuera de sesion: {reason}"


def test_the_reason_is_always_informative():
    """El motivo acaba en el log y en el dashboard: nunca debe quedar vacio."""
    for interval in ("1d", "1h"):
        for moment in (et(2026, 8, 10, 12), et(2026, 8, 8, 12), et(2026, 8, 10, 20)):
            _, reason = mc.should_run(interval, moment)
            assert reason and len(reason) > 10
