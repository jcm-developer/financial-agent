"""API configuration and the dependencies the endpoints share.

The rule that governs this module: **each request opens and closes its own
connection**. It is the same criterion the old dashboard already followed, and
for the same reason: it is a single-user server against a local file, opening it
costs under a millisecond, and this way no stale data is served while the cycle
or the ingestor write in parallel. A long-lived connection would have to worry
about WAL snapshots; this one does not.

There are **two** database dependencies and not one, and the difference is the
contract of D5:

  * `read_db` opens SQLite in `ro` mode. It is not a promise, it is an
    impossibility: nothing can be written through it even if the code tries.
  * `config_db` opens the connection with the authorizer from `guard.py`, which
    only allows writing to the configuration tables.

No endpoint receives an unfenced write connection. The project's only unfenced
writes are made by the cycle and the ingestor, each in its own process.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Query, Request, status

from src.config import Infra
from src.db import Database, DatabaseError

from .guard import ConfigDatabase

log = logging.getLogger(__name__)

#: 422. The number is written out and not `status.HTTP_422_...` because Starlette
#: renamed that constant (`..._UNPROCESSABLE_ENTITY` -> `..._CONTENT`) and using
#: either name ties the project to a version range in exchange for nothing: the
#: HTTP code has been the same since 1999.
UNPROCESSABLE = 422

REPO_ROOT = Path(__file__).resolve().parent.parent
#: Where Vite leaves the frontend build (F4.1). It does not exist yet: `main.py`
#: serves a placeholder in the meantime.
APP_DIST = REPO_ROOT / "app" / "dist"


def _env_bool(key: str, default: bool) -> bool:
    raw = (os.getenv(key) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "n"}


def _env_float(key: str, default: float) -> float:
    raw = (os.getenv(key) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        log.warning("%s=%r no es un numero; se usa %s.", key, raw, default)
        return default


@dataclass(frozen=True)
class ApiConfig:
    """What the API needs to know about its environment. No strategy.

    Same as `Infra`: if one day a parameter of the experiment needed adding here,
    that would be the sign its place is `agent_settings` (F6.4).
    """

    db_path: str
    host: str = "127.0.0.1"
    port: int = 8000
    #: Whether the cycle-firing endpoints exist. See `controls_enabled`.
    controls: bool = True
    #: How often the server checks whether there is anything new to push over SSE.
    stream_interval: float = 2.0
    #: How long an SSE connection lives at most before closing itself.
    #:
    #: It is not a limitation, it is hygiene: `EventSource` reconnects by itself
    #: —that is the reason for choosing SSE in D6— so cutting every so often
    #: returns the server's resources without the client noticing a thing. And it
    #: forces the symbol list to be re-read along the way: an eternal stream
    #: would keep serving the universe the profile had when someone opened the tab.
    stream_max_seconds: float = 900.0
    app_dist: Path = APP_DIST

    @classmethod
    def load(cls, *, db_path: str | None = None) -> ApiConfig:
        infra = Infra.load()
        return cls(
            db_path=db_path or infra.db_path,
            host=(os.getenv("API_HOST") or "127.0.0.1").strip(),
            port=int((os.getenv("API_PORT") or "8000").strip()),
            # On by default, and **not inferred from `host`**: inside Docker you
            # have to listen on 0.0.0.0 for port mapping to work, so the listening
            # address says nothing about who can reach it. Whoever really
            # publishes this on a network switches it off by hand.
            controls=_env_bool("API_CONTROLS", True),
            stream_interval=max(0.5, _env_float("API_STREAM_INTERVAL", 2.0)),
            stream_max_seconds=max(
                5.0, _env_float("API_STREAM_MAX_SECONDS", 900.0)
            ),
        )


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# ----------------------------------------------------------------------
# Dependencias
# ----------------------------------------------------------------------

def get_config(request: Request) -> ApiConfig:
    """The configuration lives in `app.state`, not in a global.

    That way the tests can bring up several applications against different
    databases in the same process, which is exactly what `tests/test_api.py` does
    with `tmp_path`.
    """
    return request.app.state.config


Config = Annotated[ApiConfig, Depends(get_config)]


def read_db(config: Config) -> Iterator[Database]:
    try:
        db = Database(path=config.db_path, read_only=True)
    except DatabaseError as exc:
        # 503 and not 500: the database does not exist yet or is locked. It is a
        # state of the system, not a bug, and the interface paints it differently.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    try:
        yield db
    finally:
        db.close()


def config_db(config: Config) -> Iterator[ConfigDatabase]:
    try:
        db = ConfigDatabase(path=config.db_path)
    except DatabaseError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    try:
        yield db
    finally:
        db.close()


ReadDb = Annotated[Database, Depends(read_db)]
ConfigDb = Annotated[ConfigDatabase, Depends(config_db)]


def get_runner(request: Request) -> Any:
    """The application's `CycleRunner`, or None when the controls are off."""
    return request.app.state.runner


# ----------------------------------------------------------------------
# Resolucion de perfil
# ----------------------------------------------------------------------

def find_profile(db: Database, reference: str) -> dict[str, Any]:
    """A profile by id or by name.

    It accepts both on purpose: ids are UUIDs and names are unique, so no
    ambiguity is possible, and `/api/positions?profile=europa-01` can be typed by
    hand while debugging.

    The interface sends **the name** (F4, stretch C): the active profile travels
    in the URL (`/p/europa-01/positions`) and with a UUID there nobody would know
    which experiment they were looking at, which was precisely the reason for
    taking it out of React's memory. A saved link breaks if the profile is
    renamed, and it is right that it breaks: the 404 below says which ones exist.
    """
    reference = (reference or "").strip()
    if not reference:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail="Falta el perfil: pasa ?profile=<id o nombre>.",
        )
    profile = db.get_profile(reference) or db.get_profile_by_name(reference)
    if profile is None:
        disponibles = ", ".join(
            row["name"] for row in db.list_profiles(include_archived=True)
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No existe ningun perfil {reference!r}."
                + (f" Hay: {disponibles}." if disponibles else "")
            ),
        )
    return profile


def portfolio_of(profile: dict[str, Any]) -> str:
    """The profile's book id.

    A profile with no book should not exist —`create_profile` creates them
    together— but if a half-made one turns up, saying so beats returning empty
    lists that would read as "it has not traded yet".
    """
    portfolio_id = profile.get("portfolio_id")
    if not portfolio_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"El perfil {profile['name']!r} no tiene cartera asociada, asi que "
                "no tiene historico que enseñar."
            ),
        )
    return str(portfolio_id)


def resolve_portfolio(db: Database, reference: str) -> tuple[dict[str, Any], str]:
    profile = find_profile(db, reference)
    return profile, portfolio_of(profile)


#: Pagination parameters shared by every list.
LimitQuery = Annotated[int, Query(ge=1, le=500, description="Filas por pagina.")]
OffsetQuery = Annotated[int, Query(ge=0, description="Filas que saltar.")]
ProfileQuery = Annotated[
    str, Query(description="Id o nombre del perfil de experimento.")
]
