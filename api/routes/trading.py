"""Historico de operativa: posiciones, decisiones, ordenes, ciclos, analitica.

Todo de solo lectura, y no por convencion: estos endpoints reciben `ReadDb`, que
es SQLite abierto en modo `ro`. Aunque alguien escribiera aqui un UPDATE por
error, el motor lo rechazaria.

Aqui vivia `/api/dashboard`, que devolvia el ensamblado de doce consultas de
`src/dashboard.py` entero y sin modelo Pydantic. Se retiro en F4.11 con el
dashboard viejo al que servia: el frontend nunca llego a usarlo —se arma con los
endpoints tipados de abajo, que era la decision de F4— y dejarlo habria sido
mantener una segunda forma de contar el mismo experimento, sin tipos y sin nadie
que la ejercitara. `build_dashboard` sigue donde estaba: lo usa `run.py report`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from .. import queries
from ..deps import ProfileQuery, ReadDb, resolve_portfolio
from ..models import (
    Analytics,
    CycleDetail,
    CycleRow,
    DecisionRow,
    OrderRow,
    Page,
    PositionRow,
    RiskEventRow,
)

router = APIRouter(prefix="/api", tags=["operativa"])


@router.get("/analytics", response_model=Analytics)
def analytics(db: ReadDb, profile: ProfileQuery):
    """Las cinco series de las graficas (F4.6).

    Tres salen de vistas que ya existen en `schema.sql`, asi que la consola y la
    web no pueden acabar contando cosas distintas del mismo experimento. Es la
    regla de la que salio tambien `/api/dashboard`: una sola definicion de cada
    numero, en el sitio donde ya estaba.
    """
    _, portfolio_id = resolve_portfolio(db, profile)
    return queries.analytics(db, portfolio_id)


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
