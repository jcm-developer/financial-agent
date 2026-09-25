#!/usr/bin/env python
"""Cycle scheduler. It is the main process of the `scheduler` container.

**Which experiments run, and at what times, is decided in the database** —
`profiles.status` and `agent_settings.cycle_times`— and not in the environment
(F6.10). That is the premise of F6: everything that defines an experiment lives
in the profile, so that comparing two of them is creating a second profile and
not editing a file and redeploying.

The practical consequence is the point of the whole change: **activating,
pausing or rescheduling an experiment from the interface takes effect without
touching the `.env` and without restarting the container.** The plan is re-read
every `SCHEDULER_REFRESH_SECONDS`, so the loop notices on its own.

Configuration by environment, which is now infrastructure only:

    RUN_ON_START                If true, runs a cycle at startup for every active
                                profile. Default false
    SCHEDULER_REFRESH_SECONDS   How often the plan is re-read. Default 60

`CYCLE_TIMES` and `CYCLE_TZ` are **no longer read from the environment.** They
were one set of hours for every profile, so a European experiment with three
intraday cycles and an American one at the close could not be expressed at the
same time — and they were also the reason a profile's own columns were mute.

Each cycle runs as a separate subprocess on purpose: if a call to the model hangs
or the process dies, the scheduler survives and the next run is still standing.

**Between cycles it also runs stop checks** (F9.36): every hour, at the minute of
the profile's cycle, from the first cycle of the day to the end of the operating
window. They are `run.py check-stops`, which does not call the model, so they
cost no quota; see `stop_check_times` for why those hours and no others.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

log = logging.getLogger("scheduler")

# Short waiting slices so the container answers `docker stop` in seconds instead
# of waiting out the SIGKILL timeout.
SLEEP_CHUNK_SECONDS = 30

_stopping = False


def _handle_signal(signum: int, _frame: object) -> None:
    global _stopping
    _stopping = True
    log.info("Recibida la señal %s: se detendrá tras el ciclo en curso.", signum)


class ScheduleError(ValueError):
    """The profile's schedule cannot be read.

    It is an exception and not a `SystemExit` because **`cycle_times` is now a
    field the interface writes** (F6.8): one profile with a typo must not take
    the scheduler down for all the others. The bad profile is skipped, loudly.
    """


def parse_times(raw: str) -> list[tuple[int, int]]:
    """Turns "09:30, 22:15" into [(9, 30), (22, 15)], sorted and deduplicated."""
    times: list[tuple[int, int]] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            hour_text, minute_text = chunk.split(":")
            hour, minute = int(hour_text), int(minute_text)
        except ValueError as exc:
            raise ScheduleError(
                f"hora no válida «{chunk}»: se espera HH:MM, por ejemplo "
                f"«17:40» o «11:20,14:20,17:40»."
            ) from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ScheduleError(f"hora fuera de rango: «{chunk}».")
        times.append((hour, minute))

    if not times:
        raise ScheduleError("no hay ninguna hora configurada.")
    return sorted(set(times))


@dataclass(frozen=True)
class Plan:
    """What one active experiment has to run, and when."""

    profile: str
    times: tuple[tuple[int, int], ...]
    tz_name: str
    tz: ZoneInfo
    market: str
    bar_interval: str
    #: The stop checks between cycles (F9.36), in the same zone as `times`.
    check_times: tuple[tuple[int, int], ...] = ()

    def describe(self) -> str:
        schedule = ", ".join(f"{h:02d}:{m:02d}" for h, m in self.times)
        checks = ", ".join(f"{h:02d}:{m:02d}" for h, m in self.check_times)
        return (
            f"{self.profile}: {schedule} ({self.tz_name}), "
            f"mercado {self.market}, barras {self.bar_interval}"
            + (f"; stops a las {checks}" if checks else "")
        )


def stop_check_times(
    times: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    *,
    tz: ZoneInfo,
    market: str,
    bar_interval: str,
    today: date | None = None,
) -> tuple[tuple[int, int], ...]:
    """The hours at which the stops are checked without the model (F9.36).

    Every hour at the minute of the profile's cycle, because that minute was
    chosen for the bar: at :20 the hourly bar that closed at :00 has arrived
    through the feed's 15-minute delay, and the next one is almost an hour away.

    **Only after the first cycle of the day.** Before it, the last complete bar
    is yesterday's, which the evening checks already saw; the overnight gap
    shows up in the first bar of the session, and the cycle reads that one.

    **Only inside the operating window**, converted from the market's zone to the
    profile's: a European profile scheduled in Madrid and an American one would
    otherwise get their windows mixed. Computed for `today` because the two
    zones need not change to summer time on the same day.

    **None with daily bars**: the price the stop is compared with does not move
    until the session closes, and the cycle after the close already reads it.
    """
    if bar_interval == "1d" or not times:
        return ()
    from src.market_calendar import get_market

    try:
        spec = get_market(market)
    except Exception:  # noqa: BLE001 - un mercado desconocido ya se avisa al resolver
        return ()

    day = today or datetime.now(tz).date()

    def in_profile_zone(moment: clock) -> datetime:
        return datetime.combine(day, moment, tzinfo=spec.tz).astimezone(tz)

    window_start = in_profile_zone(spec.operating_open)
    window_end = in_profile_zone(spec.operating_close)
    first_hour, minute = min(times)
    return tuple(
        (hour, minute)
        for hour in range(first_hour + 1, 24)
        if (hour, minute) not in times
        and window_start <= datetime.combine(day, clock(hour, minute), tzinfo=tz) <= window_end
    )


def load_plans(db) -> list[Plan]:
    """Reads the active profiles and the times each one asks for.

    A profile whose schedule cannot be read is skipped and reported, not fatal:
    see `ScheduleError`. Returns one plan per active profile that could be read.
    """
    plans: list[Plan] = []
    for profile in db.list_profiles():
        if profile["status"] != "active":
            continue
        settings = db.get_settings(profile["id"])
        try:
            times = parse_times(settings["cycle_times"])
        except ScheduleError as exc:
            log.error(
                "Perfil %r: %s No se planificará hasta que se corrija en Ajustes.",
                profile["name"], exc,
            )
            continue

        tz_name = (settings["cycle_tz"] or "Europe/Madrid").strip()
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            log.error(
                "Perfil %r: zona horaria desconocida %r. Se usa UTC.",
                profile["name"], tz_name,
            )
            tz_name, tz = "UTC", ZoneInfo("UTC")

        plans.append(Plan(
            profile=profile["name"],
            times=tuple(times),
            tz_name=tz_name,
            tz=tz,
            market=settings["market"],
            bar_interval=settings["bar_interval"],
            check_times=stop_check_times(
                times, tz=tz, market=settings["market"],
                bar_interval=settings["bar_interval"],
            ),
        ))
    return plans


def warn_about_odd_times(plan: Plan) -> None:
    """Points out a schedule that will analyse the wrong bar.

    Both cases are silent failures: nothing errors, the cycle runs and decides on
    data that is not what it looks like.

      * With **daily bars**, a cycle before the close analyses **today's
        unfinished bar** — the close it reads is whatever the price happened to
        be at that moment.
      * With **hourly bars**, a cycle outside the operating window (FE.13) reads
        the last complete bar, which after the close is fine and before the open
        is yesterday's.
    """
    from src.market_calendar import get_market

    try:
        market = get_market(plan.market)
    except Exception:  # noqa: BLE001 - un mercado desconocido ya se avisa al resolver
        return

    for hour, minute in plan.times:
        if plan.bar_interval == "1d" and (hour, minute) < (
            market.close_time.hour, market.close_time.minute
        ):
            log.warning(
                "Perfil %r: ciclo a las %02d:%02d con barras diarias, pero %s "
                "cierra a las %02d:%02d: analizaría la barra del día sin "
                "terminar.",
                plan.profile, hour, minute, market.label,
                market.close_time.hour, market.close_time.minute,
            )
        elif plan.bar_interval != "1d":
            window_start = (market.operating_open.hour, market.operating_open.minute)
            window_end = (market.operating_close.hour, market.operating_close.minute)
            if not (window_start <= (hour, minute) <= window_end):
                log.warning(
                    "Perfil %r: ciclo a las %02d:%02d fuera de la ventana "
                    "operativa de %s (%02d:%02d-%02d:%02d).",
                    plan.profile, hour, minute, market.label,
                    *window_start, *window_end,
                )


def next_run(now: datetime, times: tuple[tuple[int, int], ...]) -> datetime:
    """First scheduled time after `now`, today or tomorrow."""
    for day_offset in (0, 1):
        day = now + timedelta(days=day_offset)
        for hour, minute in times:
            candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate > now:
                return candidate
    # Unreachable: with two days of margin there is always a later time.
    raise RuntimeError("No se pudo calcular la siguiente ejecución.")


def run_cycle(profile: str) -> int:
    """Launches one cycle for one experiment, as a subprocess.

    `--profile` is explicit and never left to be guessed: with several active
    profiles, operating against the wrong experiment dirties two histories at
    once and does not undo. Returns the subprocess's exit code.
    """
    log.info("Lanzando ciclo de %r…", profile)
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "run.py", "cycle", "--profile", profile],
        cwd=str(APP_DIR), check=False,
    )
    elapsed = time.monotonic() - started

    if result.returncode == 0:
        log.info("Ciclo de %r completado en %.0f s.", profile, elapsed)
    else:
        # It is not aborted: a failed cycle (network, model quota) must not stop
        # the next one, nor the other profiles'.
        log.error(
            "El ciclo de %r terminó con código %d tras %.0f s. Revisa el log "
            "anterior y `python run.py report`.", profile, result.returncode, elapsed,
        )
    return result.returncode


def run_stop_check(profile: str) -> int:
    """Launches one stop check (F9.36), as a subprocess like a cycle.

    A subprocess for the same reason: a hung Yahoo download must not take the
    scheduler down. It logs less than a cycle because it runs seven times a day
    and almost always finds nothing; `check-stops` prints what it did.
    """
    result = subprocess.run(
        [sys.executable, "run.py", "check-stops", "--profile", profile],
        cwd=str(APP_DIR), check=False,
    )
    if result.returncode != 0:
        log.error(
            "La comprobación de stops de %r terminó con código %d.",
            profile, result.returncode,
        )
    return result.returncode


def _sleep_a_little(seconds: float) -> None:
    """Sleeps in slices so a stop signal is noticed within seconds."""
    remaining = seconds
    while remaining > 0 and not _stopping:
        slice_seconds = min(SLEEP_CHUNK_SECONDS, remaining)
        time.sleep(slice_seconds)
        remaining -= slice_seconds


def main() -> int:
    from src.formatting import utf8_console

    utf8_console()
    logging.basicConfig(
        level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
        format="%(asctime)s  %(levelname)-7s %(name)-12s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    from src.config import Infra
    from src.db import Database

    infra = Infra.load()
    refresh = float(os.getenv("SCHEDULER_REFRESH_SECONDS") or 60)
    run_on_start = (os.getenv("RUN_ON_START") or "").strip().lower() in {
        "1", "true", "yes", "y", "on",
    }

    # What each profile's next run is, by profile name. It survives the reload so
    # a plan that has not changed does not have its next time recomputed —which
    # would push it forward every minute and mean it never fires.
    upcoming: dict[str, datetime] = {}
    #: The same for the stop checks, kept apart so a check never pushes a cycle.
    upcoming_checks: dict[str, datetime] = {}
    known: dict[str, Plan] = {}
    first_pass = True

    log.info(
        "Planificador en marcha. Los horarios salen del perfil; "
        "la lista se relee cada %.0f s, así que activar o pausar un experimento "
        "desde la interfaz surte efecto sin reiniciar nada.", refresh,
    )

    while not _stopping:
        with Database(path=infra.db_path) as db:
            plans = load_plans(db)

        current = {plan.profile: plan for plan in plans}

        for name, plan in current.items():
            if known.get(name) != plan:
                # New, or its schedule changed in the interface.
                if name in known:
                    log.info("Planificación actualizada: %s", plan.describe())
                else:
                    log.info("Experimento activo: %s", plan.describe())
                warn_about_odd_times(plan)
                upcoming[name] = next_run(datetime.now(plan.tz), plan.times)
                log.info(
                    "  siguiente ciclo de %r: %s",
                    name, upcoming[name].strftime("%Y-%m-%d %H:%M %Z"),
                )
                if plan.check_times:
                    upcoming_checks[name] = next_run(datetime.now(plan.tz), plan.check_times)
                else:
                    upcoming_checks.pop(name, None)

        for name in list(known):
            if name not in current:
                log.info("El experimento %r ya no está activo: se deja de planificar.", name)
                upcoming.pop(name, None)
                upcoming_checks.pop(name, None)
        known = current

        if not current:
            log.info(
                "No hay ningún experimento activo. Actívalo desde la pantalla de "
                "Experimentos y el planificador lo recogerá solo."
            )

        if first_pass:
            first_pass = False
            if run_on_start:
                for name in current:
                    if _stopping:
                        break
                    log.info("Ciclo inmediato de %r al arrancar (RUN_ON_START).", name)
                    run_cycle(name)

        # Whatever is due is run. Several profiles can fall in the same slice, and
        # they go one after another rather than at once: two cycles in parallel
        # would double the requests to Yahoo and to the model at the same minute.
        for name, plan in current.items():
            if _stopping:
                break
            due = upcoming.get(name)
            if due is not None and datetime.now(plan.tz) >= due:
                run_cycle(name)
                upcoming[name] = next_run(datetime.now(plan.tz), plan.times)
                log.info(
                    "  siguiente ciclo de %r: %s",
                    name, upcoming[name].strftime("%Y-%m-%d %H:%M %Z"),
                )

        # The stop checks, after the cycles and one after another like them.
        for name, plan in current.items():
            if _stopping:
                break
            due = upcoming_checks.get(name)
            if due is not None and datetime.now(plan.tz) >= due:
                run_stop_check(name)
                upcoming_checks[name] = next_run(datetime.now(plan.tz), plan.check_times)

        if _stopping:
            break

        # Never sleep past the refresh: a change made in the interface has to be
        # noticed even if the next cycle is eight hours away.
        wait = refresh
        for name, plan in current.items():
            for due in (upcoming.get(name), upcoming_checks.get(name)):
                if due is not None:
                    wait = min(wait, max(0.0, (due - datetime.now(plan.tz)).total_seconds()))
        _sleep_a_little(max(1.0, wait))

    log.info("Planificador detenido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
