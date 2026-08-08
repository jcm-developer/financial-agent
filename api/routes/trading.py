"""Historico de operativa: dashboard, posiciones, decisiones, ordenes, ciclos.

Todo de solo lectura, y no por convencion: estos endpoints reciben `ReadDb`, que
es SQLite abierto en modo `ro`. Aunque alguien escribiera aqui un UPDATE por
error, el motor lo rechazaria.

`/api/dashboard` es la excepcion de forma: devuelve el payload de
`src/dashboard.py` tal cual, el mismo que consume `run.py report`. Se sirve
entero y sin modelo Pydantic a proposito —ver la cabecera de `models.py`—:
volver a describir aqui un ensamblado de doce consultas seria tener dos
definiciones de lo mismo condenadas a discrepar.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from src.dashboard import build_dashboard

from .. import queries
from ..deps import ProfileQuery, ReadDb, resolve_portfolio
from ..models import (
    CycleDetail,
    CycleRow,
    DecisionRow,
    OrderRow,
    Page,
    PositionRow,
    RiskEventRow,
)

router = APIRouter(prefix="/api", tags=["operativa"])


@router.get("/dashboard")
def dashboard(db: ReadDb, profile: ProfileQuery) -> dict[str, Any]:
    """El payload completo de una cartera, en un solo viaje.

    Reutiliza `build_dashboard`, el mismo ensamblado que la consola: asi la web
    y `run.py report` no pueden llegar a contar cosas distintas del mismo
    experimento.
    """
    profile_row, _ = resolve_portfolio(db, profile)
    payload = build_dashboard(db, portfolio_name=profile_row["name"])
    payload["profile"] = {
        "id": profile_row["id"],
        "name": profile_row["name"],
        "status": profile_row["status"],
    }
    # La divisa acompaña siempre a las cifras: un presupuesto europeo escrito
    # con '$' invita a compararlo con el de otro perfil como si fuera la misma
    # unidad, y con dos experimentos en paralelo eso pasa solo (FE.8).
    settings = db.get_settings(profile_row["id"])
    payload["market"] = queries.market_info(settings["market"])
    return payload


@router.get("/positions", response_model=Page[PositionRow])
def positions(
    db: ReadDb, profile: ProfileQuery,
    status_filter: str = Query("", alias="status", pattern="^(open|closed)?$"),
    symbol: str = "",
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
):
    _, portfolio_id = resolve_portfolio(db, profile)
    rows, total = queries.positions(
        db, portfolio_id, status=status_filter, symbol=symbol,
        limit=limit, offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/decisions", response_model=Page[DecisionRow])
def decisions(
    db: ReadDb, profile: ProfileQuery,
    symbol: str = "", action: str = Query("", pattern="^(buy|sell|hold)?$"),
    verdict: str = Query("", pattern="^(approved|rejected)?$"),
    cycle_id: str = "",
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
):
    _, portfolio_id = resolve_portfolio(db, profile)
    rows, total = queries.decisions(
        db, portfolio_id, symbol=symbol, action=action, verdict=verdict,
        cycle_id=cycle_id, limit=limit, offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/orders", response_model=Page[OrderRow])
def orders(
    db: ReadDb, profile: ProfileQuery,
    symbol: str = "", status_filter: str = Query("", alias="status"),
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
):
    _, portfolio_id = resolve_portfolio(db, profile)
    rows, total = queries.orders(
        db, portfolio_id, symbol=symbol, status=status_filter,
        limit=limit, offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/risk-events", response_model=Page[RiskEventRow])
def risk_events(
    db: ReadDb, profile: ProfileQuery,
    verdict: str = Query("", pattern="^(approved|rejected)?$"),
    rule: str = "", symbol: str = "",
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
):
    """Los rechazos son la evidencia de que la barrera de riesgo funciona.

    Por eso tienen endpoint propio y no solo una columna en `decisions`: contra
    que limite choca el modelo mas a menudo es una de las preguntas del
    experimento, no un detalle de una fila.
    """
    _, portfolio_id = resolve_portfolio(db, profile)
    rows, total = queries.risk_events(
        db, portfolio_id, verdict=verdict, rule=rule, symbol=symbol,
        limit=limit, offset=offset,
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/cycles", response_model=Page[CycleRow])
def cycles(
    db: ReadDb, profile: ProfileQuery,
    status_filter: str = Query("", alias="status"),
    limit: int = Query(60, ge=1, le=500), offset: int = Query(0, ge=0),
):
    _, portfolio_id = resolve_portfolio(db, profile)
    rows, total = queries.cycles(
        db, portfolio_id, status=status_filter, limit=limit, offset=offset
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/cycles/{cycle_id}", response_model=CycleDetail)
def cycle_detail(db: ReadDb, cycle_id: str):
    """Un ciclo con la copia de los parametros con los que corrio (F6.3).

    `settings` viene a None en los ciclos anteriores a esa tarea. Es informacion
    que falta, no un cero: quien compare experimentos necesita distinguir
    "corrio con estos ajustes" de "no se sabe con que ajustes corrio".
    """
    row = queries.cycle_detail(db, cycle_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe el ciclo {cycle_id}.")
    return row
