"""Firing and stopping cycles from the interface (F3.4).

These are the only endpoints that cause a write to the history, and they do it
**without being able to write it themselves**: they launch `run.py cycle` as a
subprocess, which opens its own unfenced connection. It is the same separation
the dashboard already had, and with F3.3 it goes from prudent to necessary: the
API cannot touch `orders` or `positions` even if it wanted to (see `guard.py`),
so trading had to leave the process either way.

They are switched off wholesale with `API_CONTROLS=false`. It is not inferred
from the listening address: inside Docker you have to listen on 0.0.0.0 for port
mapping to work, so the host says nothing about who can reach it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from src.db import Database

from .. import queries
from ..deps import ReadDb, find_profile, get_runner
from ..models import ActionResult, CycleControl, CycleRunRequest
from ..runner import DISABLED_STATUS, with_external

router = APIRouter(prefix="/api/cycles", tags=["control"])

Runner = Annotated[Any, Depends(get_runner)]


@router.get("/control/status", response_model=CycleControl)
def control_status(db: ReadDb, runner: Runner):
    """The cycle's state, from **both** sources and not just from this process.

    The runner only knows about the subprocess it spawned itself, and that was a
    lie by omission on screen: a cycle launched by the scheduler —another
    container— left this endpoint answering `running: false`, so the panel said
    "Sin ciclo en marcha" while one was running and the Parar button sat disabled
    with nothing to explain why. Reported, and it is the worst kind of wrong: the
    interface was not broken, it was confident.

    `external` is the missing bit, and it is separate from `running` on purpose
    rather than folded into it. They mean different things to every consumer:
    `running` answers "may I launch one?" and `external` answers "is the one
    running mine?". The shape is assembled in `with_external`, next to the runner,
    because the SSE has to answer exactly the same thing (F4.19).
    """
    if runner is None:
        return DISABLED_STATUS
    return with_external(runner.status(), queries.running_cycle(db))


@router.post("/run", response_model=CycleControl)
def run_cycle(db: ReadDb, runner: Runner, body: CycleRunRequest):
    """Starts a cycle, unless one is already running.

    **Both** possible sources are checked: this process's own subprocess and the
    `cycles` table. The second matters because the scheduler's container may have
    a cycle running that this process knows nothing about, and two cycles at once
    over the same book would step on each other's positions and cash, leaving a
    history with duplicated decisions that cannot be interpreted.
    """
    _require_controls(runner)

    profile_name = None
    if body.profile:
        profile_name = find_profile(db, body.profile)["name"]

    if _cycle_running_elsewhere(db):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Ya hay un ciclo en marcha (probablemente lanzado por el "
            "planificador). Espera a que termine.",
        )

    ok, message = runner.start(profile=profile_name, dry_run=body.dry_run)
    if not ok:
        raise HTTPException(status.HTTP_409_CONFLICT, message)
    return runner.status()


@router.post("/close-experiment", response_model=CycleControl)
def close_experiment(db: ReadDb, runner: Runner, body: CycleRunRequest):
    """Liquidates the book to end an experiment (F5.8).

    It goes out as a subprocess and through the same runner as a cycle, for the
    same two reasons: **the API cannot write to the history** —not even by
    mistake, SQLite forbids it (F3.3)— and only one operation at a time may touch
    a book.

    It is deliberately **not** a `DELETE` nor a status change. Closing means
    selling, with its orders, its exit prices and its reasons; a profile marked
    "closed" with its positions still open would be a lie recorded in the one
    place that is read to judge the experiment.
    """
    _require_controls(runner)

    profile_name = None
    if body.profile:
        profile_name = find_profile(db, body.profile)["name"]

    if _cycle_running_elsewhere(db):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Hay un ciclo en marcha. Espera a que termine antes de cerrar el "
            "experimento: vender mientras el agente decide dejaria las dos "
            "cosas a medias.",
        )

    ok, message = runner.start(profile=profile_name, action="close-experiment")
    if not ok:
        raise HTTPException(status.HTTP_409_CONFLICT, message)
    return runner.status()


@router.post("/stop", response_model=ActionResult)
def stop_cycle(db: ReadDb, runner: Runner):
    """Asks the running cycle to stop, **whoever launched it** (F4.21).

    Until now it could only stop its own: it was a `terminate()` over this
    process's subprocess, so a cycle from the scheduler's container had no button
    at all and the screen sent you to restart the container — which stops the
    container, not the cycle, and leaves the row in 'running' until it ages out.

    The id of the cycle to stop comes **from the table** and not from the runner,
    and that is what makes the button work for both cases with one code path: the
    row is the only thing the two containers share. With it, the request goes out
    through `src/stop_signal.py` and the cycle closes itself down in order.
    """
    _require_controls(runner)
    cycle = queries.running_cycle(db)
    ok, message = runner.stop(cycle_id=cycle["id"] if cycle else None)
    if not ok:
        raise HTTPException(status.HTTP_409_CONFLICT, message)
    return {"ok": True, "message": message}


def _require_controls(runner: Any) -> None:
    if runner is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Los controles estan desactivados (API_CONTROLS=false). Esta API "
            "solo sirve datos.",
        )


def _cycle_running_elsewhere(db: Database) -> bool:
    """Moved to `queries` so the stream can ask the same question (F4.19)."""
    return queries.cycle_running_elsewhere(db)
