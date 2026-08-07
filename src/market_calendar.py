"""Calendario de la bolsa estadounidense (NYSE / Nasdaq).

Hace falta desde el momento en que el agente decide durante la sesion: sin esto
el planificador ejecutaria ciclos a las 3 de la manana sobre datos rancios, y los
fines de semana gastaria cuota del modelo analizando barras identicas a las del
viernes.

Sin dependencias: los festivos van en una tabla porque `pandas_market_calendars`
arrastraria media libreria para lo que aqui son treinta lineas. El precio es que
la tabla hay que mantenerla; `LAST_COVERED_YEAR` marca hasta donde llega, y las
funciones avisan en lugar de mentir cuando se pasa esa fecha.

Horario regular: 09:30 - 16:00 hora del Este. Los dias de media sesion cierran a
las 13:00.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")

OPEN_TIME = time(9, 30)
CLOSE_TIME = time(16, 0)
EARLY_CLOSE_TIME = time(13, 0)

# Ultimo ano con festivos verificados. Mas alla de aqui, el calendario solo sabe
# de fines de semana y lo advierte.
LAST_COVERED_YEAR = 2027

# Festivos con mercado cerrado. Cuando caen en fin de semana, NYSE los traslada
# al viernes anterior o al lunes siguiente; las fechas de abajo ya son las
# observadas, no las nominales.
HOLIDAYS: frozenset[date] = frozenset({
    # 2025
    date(2025, 1, 1), date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4),
    date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
})

# Media sesion: cierre a las 13:00 ET. Vispera de Independencia, dia siguiente a
# Accion de Gracias y vispera de Navidad.
EARLY_CLOSES: frozenset[date] = frozenset({
    date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
    date(2026, 11, 27), date(2026, 12, 24),
    date(2027, 11, 26),
})


def now_eastern() -> datetime:
    return datetime.now(EASTERN)


def _to_eastern(moment: datetime | None) -> datetime:
    if moment is None:
        return now_eastern()
    if moment.tzinfo is None:
        # Un datetime sin zona se interpreta como hora del Este, no como UTC:
        # asumir UTC desplazaria las sesiones cinco horas sin avisar.
        return moment.replace(tzinfo=EASTERN)
    return moment.astimezone(EASTERN)


def _warn_if_uncovered(day: date) -> None:
    if day.year > LAST_COVERED_YEAR:
        log.warning(
            "El calendario de festivos solo cubre hasta %d; %s se evalua solo por "
            "el dia de la semana. Actualiza HOLIDAYS en src/market_calendar.py.",
            LAST_COVERED_YEAR, day,
        )


def is_trading_day(day: date) -> bool:
    """True si hay sesion ese dia (ni fin de semana ni festivo)."""
    _warn_if_uncovered(day)
    if day.weekday() >= 5:
        return False
    return day not in HOLIDAYS


def close_time_for(day: date) -> time:
    return EARLY_CLOSE_TIME if day in EARLY_CLOSES else CLOSE_TIME


def is_session_open(moment: datetime | None = None) -> bool:
    """True si el mercado esta abierto en ese instante."""
    eastern = _to_eastern(moment)
    if not is_trading_day(eastern.date()):
        return False
    return OPEN_TIME <= eastern.time() < close_time_for(eastern.date())


def session_bounds(day: date) -> tuple[datetime, datetime] | None:
    """Apertura y cierre de la sesion de ese dia, o None si no hay sesion."""
    if not is_trading_day(day):
        return None
    return (
        datetime.combine(day, OPEN_TIME, tzinfo=EASTERN),
        datetime.combine(day, close_time_for(day), tzinfo=EASTERN),
    )


def last_trading_day(moment: datetime | None = None) -> date:
    """Ultimo dia con sesion, contando hoy si ya ha abierto."""
    eastern = _to_eastern(moment)
    day = eastern.date()
    if is_trading_day(day) and eastern.time() >= OPEN_TIME:
        return day
    day -= timedelta(days=1)
    for _ in range(10):
        if is_trading_day(day):
            return day
        day -= timedelta(days=1)
    # Diez dias seguidos sin sesion no ocurre; si ocurre, la tabla esta mal.
    raise RuntimeError("No se encontro ningun dia de mercado en los ultimos 10 dias.")


def next_session_open(moment: datetime | None = None) -> datetime:
    """Proxima apertura, para poder decir cuanto falta."""
    eastern = _to_eastern(moment)
    day = eastern.date()
    if is_trading_day(day) and eastern.time() < OPEN_TIME:
        return datetime.combine(day, OPEN_TIME, tzinfo=EASTERN)
    for _ in range(1, 12):
        day += timedelta(days=1)
        if is_trading_day(day):
            return datetime.combine(day, OPEN_TIME, tzinfo=EASTERN)
    raise RuntimeError("No se encontro ninguna sesion en los proximos 12 dias.")


def should_run(interval: str, moment: datetime | None = None) -> tuple[bool, str]:
    """Decide si merece la pena gastar un ciclo. Devuelve (ejecutar, motivo).

    La pregunta no es "esta el mercado abierto" sino **"hay datos nuevos que
    analizar"**, y la respuesta depende del intervalo:

      * Con barras diarias, el momento natural es justo DESPUES del cierre, cuando
        la sesion ya esta completa. Exigir mercado abierto dejaria fuera
        precisamente el mejor momento del dia. Solo se salta si no hay sesion:
        fines de semana y festivos, donde las barras son identicas a las del
        ciclo anterior.
      * Con barras horarias hace falta la sesion viva: una barra nueva cada hora
        es justamente lo que se quiere aprovechar, y fuera de sesion no llegan.
    """
    eastern = _to_eastern(moment)

    if not is_trading_day(eastern.date()):
        return False, f"sin sesion: {describe(eastern)}"

    if interval == "1d":
        # Dia de mercado: hay barra nueva, este abierto o ya cerrado.
        return True, f"dia de mercado ({eastern:%a %d %b}), {describe(eastern)}"

    if is_session_open(eastern):
        return True, describe(eastern)
    return False, f"barras de {interval} necesitan sesion viva: {describe(eastern)}"


def describe(moment: datetime | None = None) -> str:
    """Frase para el log de arranque del ciclo."""
    eastern = _to_eastern(moment)
    if is_session_open(eastern):
        _, close = session_bounds(eastern.date())
        remaining = (close - eastern).total_seconds() / 60
        early = " (media sesion)" if eastern.date() in EARLY_CLOSES else ""
        return (
            f"mercado abierto{early}, cierra en {remaining:.0f} min "
            f"({eastern:%H:%M} ET)"
        )
    upcoming = next_session_open(eastern)
    hours = (upcoming - eastern).total_seconds() / 3600
    return (
        f"mercado cerrado ({eastern:%a %H:%M} ET), abre en {hours:.1f} h "
        f"el {upcoming:%a %d %b %H:%M} ET"
    )
