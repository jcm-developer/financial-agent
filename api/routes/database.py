"""The database's own shape: tables, columns, relations and weight.

Read-only like every other reading endpoint, and for the same reason: it opens
SQLite in `ro` mode, so looking at the schema cannot change it. It exists so the
tables and what hangs off what can be read on screen instead of by opening the
file with a SQLite browser — which is also the one way of reading it that
bypasses `api/guard.py`.
"""

from __future__ import annotations

from fastapi import APIRouter

from .. import queries
from ..deps import ReadDb
from ..models import DatabaseSchema

router = APIRouter(prefix="/api/database", tags=["base de datos"])


@router.get("/schema", response_model=DatabaseSchema)
def schema(db: ReadDb):
    return queries.database_schema(db)
