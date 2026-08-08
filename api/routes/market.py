"""Datos de mercado en vivo y salud del ingestor.

Son los dos endpoints que hablan de lo que esta pasando ahora, no de lo que paso:
`/api/quotes` sirve lo que el ingestor escribio en el ultimo minuto y
`/api/ingest-status` dice si ese ultimo minuto existe.

`/api/markets` no estaba en la lista de F3.2 y se añade aqui porque sin el la
interfaz tendria que deducir la divisa, el horario y el tamaño del universo por
su cuenta —o peor, cablearlos—, y eso contradice D8: el mercado es un dato del
perfil y sus propiedades viven en `src/market_calendar.py`.
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
    """Ultimo precio conocido de cada simbolo, con su antiguedad.

    `age_seconds` no es adorno: es la medida que responde a la pregunta abierta
    de F2.1c. "Cada minuto" solo vale si el dato es de hace un minuto, y Yahoo
    suele servir las bolsas europeas con unos 15 minutos de desfase. Que el
    numero este a la vista evita construir una pantalla en vivo sobre datos que
    no lo son.
    """
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return queries.quotes(db, symbols=wanted or None)


@router.get("/ingest-status", response_model=IngestStatus)
def ingest_status(db: ReadDb, recent: int = Query(20, ge=0, le=60)):
    return queries.ingest_status(db, recent=recent)
