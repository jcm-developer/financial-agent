"""Trading history: positions, decisions, orders, cycles, analytics.

All read-only, and not by convention: these endpoints receive `ReadDb`, which is
SQLite opened in `ro` mode. Even if someone wrote an UPDATE here by mistake, the
engine would refuse it.

`/api/dashboard` used to live here, returning the assembly of twelve queries of
`src/dashboard.py` whole and without a Pydantic model. It was retired in F4.11
along with the old dashboard it served: the frontend never came to use it —it is
built from the typed endpoints below, which was the decision of F4— and keeping
it would have meant maintaining a second way of counting the same experiment,
untyped and with nobody exercising it. `build_dashboard` stays where it was:
`run.py report` uses it.
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
    """The five series behind the charts (F4.6).

    Three come from views that already exist in `schema.sql`, so the console and
    the web cannot end up telling different stories about the same experiment.
    It is the same rule `/api/dashboard` came from: one single definition of
    each number, in the place where it already was.
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
    """The rejections are the evidence that the risk barrier works.

    That is why they get their own endpoint and not just a column in
    `decisions`: which limit the model hits most often is one of the
    experiment's questions, not a detail of a row.
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
    """One cycle, with the copy of the settings it ran under (F6.3).

    `settings` comes back as None for cycles predating that task. That is
    missing information, not a zero: whoever compares experiments needs to tell
    "it ran with these settings" from "we do not know which settings it ran
    with".
    """
    row = queries.cycle_detail(db, cycle_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe el ciclo {cycle_id}.")
    return row
