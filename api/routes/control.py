"""Disparar y parar ciclos desde la interfaz (F3.4).

Estos son los unicos endpoints que provocan una escritura en el historico, y lo
hacen **sin poder escribirla ellos**: lanzan `run.py cycle` como subproceso, que
abre su propia conexion sin acotar. Es la misma separacion que ya tenia el
dashboard, y con F3.3 pasa de ser prudente a ser necesaria: la API no puede
tocar `orders` ni `positions` ni queriendo (ver `guard.py`), asi que operar
tenia que salir del proceso de todas formas.

Se desactivan enteros con `API_CONTROLS=false`. No se deduce de la direccion de
escucha: dentro de Docker hay que escuchar en 0.0.0.0 para que el mapeo de
puertos funcione, asi que el host no dice nada sobre quien puede llegar.
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
    """Arranca un ciclo, salvo que ya haya uno en marcha.

    Se comprueban **las dos** fuentes posibles: el subproceso propio y la tabla
    `cycles`. La segunda importa porque el contenedor del planificador puede
    tener un ciclo corriendo del que este proceso no sabe nada, y dos ciclos a la
    vez sobre la misma cartera se pisarian las posiciones y el efectivo, dejando
    un historico con decisiones duplicadas imposible de interpretar.
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
    """Pide la parada del ciclo que lanzo esta API.

    Solo puede parar el suyo: el del planificador corre en otro contenedor. Se
    dice asi en el mensaje en lugar de fingir que no hay ninguno.
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
        # Si no se puede leer, no se bloquea el arranque: el propio ciclo tiene
        # su comprobacion (`find_running_cycle`) y es la que manda de verdad.
        return False
