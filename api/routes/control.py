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

from src.db import Database, DatabaseError

from ..deps import ReadDb, find_profile, get_runner
from ..models import ActionResult, CycleControl, CycleRunRequest
from ..runner import DISABLED_STATUS

router = APIRouter(prefix="/api/cycles", tags=["control"])

Runner = Annotated[Any, Depends(get_runner)]


@router.get("/control/status", response_model=CycleControl)
def control_status(runner: Runner):
    return runner.status() if runner is not None else DISABLED_STATUS


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


@router.post("/stop", response_model=ActionResult)
def stop_cycle(runner: Runner):
    """Asks the cycle this API launched to stop.

    It can only stop its own: the scheduler's runs in another container. That is
    said in the message rather than pretending there is none.
    """
    _require_controls(runner)
    ok, message = runner.stop()
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
    try:
        return bool(
            db.query("select count(1) as n from cycles where status = 'running'")[0]["n"]
        )
    except DatabaseError:
        # If it cannot be read, the start is not blocked: the cycle has its own
        # check (`find_running_cycle`) and that is the one that really decides.
        return False
