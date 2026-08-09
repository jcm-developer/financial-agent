"""Live market data and ingestor health.

These are the two endpoints that talk about what is happening now, not about
what happened: `/api/quotes` serves what the ingestor wrote in the last minute
and `/api/ingest-status` says whether that last minute exists at all.

`/api/markets` was not on the F3.2 list and is added here because without it the
interface would have to work out the currency, the hours and the size of the
universe on its own —or worse, hardcode them— and that contradicts D8: the
market is a datum of the profile and its properties live in
`src/market_calendar.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from src.market_calendar import UnknownMarket

from .. import queries
from ..deps import ReadDb
from ..models import IngestStatus, MarketInfo, QuoteRow

router = APIRouter(prefix="/api", tags=["mercado"])


@router.get("/markets", response_model=list[MarketInfo])
def markets():
    return queries.all_markets()


@router.get("/markets/{code}", response_model=MarketInfo)
def market(code: str):
    try:
        return queries.market_info(code)
    except UnknownMarket as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/quotes", response_model=list[QuoteRow])
def quotes(
    db: ReadDb,
    symbols: str = Query(
        "", description="Lista separada por comas. Vacio = todas las conocidas."
    ),
):
    """Last known price of each symbol, with its age.

    `age_seconds` is not decoration: it is the measurement that answers the open
    question of F2.1c. "Every minute" only holds if the datum is a minute old,
    and Yahoo tends to serve the European exchanges some 15 minutes behind.
    Having the number in plain sight stops a live screen being built on data
    that is not live.
    """
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return queries.quotes(db, symbols=wanted or None)


@router.get("/ingest-status", response_model=IngestStatus)
def ingest_status(db: ReadDb, recent: int = Query(20, ge=0, le=60)):
    return queries.ingest_status(db, recent=recent)
