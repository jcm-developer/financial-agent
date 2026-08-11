"""Experiment profiles: reading (F3.2) and writing (F3.3).

This is the only router that writes, and it does so against `ConfigDb`, the
connection with the authorizer from `guard.py`. That choice is not decorative:
it is what turns "the API only touches the configuration" from an intention into
a property of the system. A misplaced `where` in any of these endpoints cannot
delete a position, because SQLite will not let it.

The exception is `DELETE /api/profiles/{id}`, which drags the experiment's
history along on purpose. That is why it asks for confirmation by repeating the
name: it is the only irreversible operation in the whole API.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from src.config import ConfigError
from src.db import DatabaseError
from src.market_calendar import get_market
from src.profile_settings import (
    UniverseError,
    create_market_profile,
    duplicate_profile,
)

from .. import queries
from ..deps import UNPROCESSABLE, ConfigDb, ReadDb, find_profile
from ..models import (
    ActionResult,
    DerivedLimits,
    Page,
    ProfileCreate,
    ProfileDetail,
    ProfileDuplicate,
    ProfilePatch,
    ProfileSummary,
    SettingsApplied,
    SettingsBundle,
    SettingsHistoryRow,
    SettingsUpdate,
    UniverseUpdate,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profiles", tags=["perfiles"])


# ----------------------------------------------------------------------
# Lectura
# ----------------------------------------------------------------------

@router.get("", response_model=list[ProfileSummary])
def list_profiles(db: ReadDb, include_archived: bool = False):
    return queries.profile_summaries(db, include_archived=include_archived)


# Declared before `/{profile_ref}` so the literal path wins: with the parameter
# route first, `/api/profiles/limits-preview` would arrive as a profile named
# "limits-preview" and answer 404 for a route that exists.
@router.get("/limits-preview", response_model=DerivedLimits)
def limits_preview(
    risk_profile: int = Query(5, ge=1, le=10),
    diversification: int = Query(5, ge=1, le=10),
):
    """The eleven limits these two sliders would give, without writing anything.

    ⚠️ **It answers what the sliders alone give, so it is only the truth when
    advanced mode is off.** The form has to pick between this and the profile's own
    effective limits, and picking wrong was a real bug until 2026-08-11: the panel
    headed "Con estos ajustes" showed these figures over a profile whose overrides
    said something else. See `DerivedLimitsPanel`.

    It exists for F6.8's form, which has to show the derived limits **while the
    slider moves**. The two alternatives were worse in the same way: patching the
    profile on every move would write to the database to answer a question, and
    reimplementing `derive_limits` in TypeScript would be a second formula
    condemned to disagree with the one the Risk Manager applies the day an anchor
    is tweaked — with the screen promising limits the agent does not enforce,
    which is the one lie that form must not tell (F6.5).

    So it goes through `queries.derived_limits`, the same function the profile's
    own endpoint uses, over a settings dict that exists only for this call.
    """
    return queries.derived_limits(
        {
            "risk_profile": risk_profile,
            "diversification": diversification,
            # No overrides: the question being asked is precisely what the
            # sliders alone give.
            "advanced_overrides": 0,
        }
    )


@router.get("/{profile_ref}", response_model=ProfileDetail)
def get_profile(db: ReadDb, profile_ref: str):
    return queries.profile_detail(db, find_profile(db, profile_ref))


@router.get("/{profile_ref}/settings", response_model=SettingsBundle)
def get_settings(db: ReadDb, profile_ref: str) -> dict:
    """The raw settings plus the limits they imply.

    They travel together because the F6.8 form needs both at once: it shows the
    slider and, beside it, what that slider means in numbers.
    """
    profile = find_profile(db, profile_ref)
    settings = db.get_settings(profile["id"])
    return {
        "profile_id": profile["id"],
        "settings": settings,
        "limits": queries.derived_limits(settings),
    }


@router.get("/{profile_ref}/settings/history", response_model=Page[SettingsHistoryRow])
def get_settings_history(
    db: ReadDb, profile_ref: str,
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
):
    profile = find_profile(db, profile_ref)
    total = db.query(
        "select count(1) as n from agent_settings_history where profile_id = ?",
        (profile["id"],),
    )[0]["n"]
    rows = db.query(
        "select id, field, old_value, new_value, source, changed_at "
        "from agent_settings_history where profile_id = ? "
        "order by changed_at desc, id desc limit ? offset ?",
        (profile["id"], limit, offset),
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/{profile_ref}/limits", response_model=DerivedLimits)
def get_limits(db: ReadDb, profile_ref: str):
    """The eleven effective limits, to paint them live while editing."""
    profile = find_profile(db, profile_ref)
    return queries.derived_limits(db.get_settings(profile["id"]))


# ----------------------------------------------------------------------
# Escritura
# ----------------------------------------------------------------------

@router.post("", response_model=ProfileDetail, status_code=status.HTTP_201_CREATED)
def create_profile(db: ConfigDb, body: ProfileCreate):
    """Creates a profile for a market, with its universe and its book.

    It shares the implementation with `run.py new-profile`
    (`profile_settings.create_market_profile`): two copies would diverge, and the
    first rule to do so would be FE.11's —the liquidity floor comes from the
    market— with the symptom that a profile created here would silently discard
    15 stocks that one created from the console does analyse.
    """
    try:
        created = create_market_profile(
            db,
            name=body.name,
            market=body.market,
            watch=body.watch,
            budget=body.budget,
            description=body.description,
        )
    except ConfigError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc
    except UniverseError as exc:
        # The repository's universe file is no good. That is not the caller's
        # fault, so it is not a 4xx.
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    except DatabaseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return queries.profile_detail(db, db.get_profile(created.profile_id))


@router.post(
    "/{profile_ref}/duplicate",
    response_model=ProfileDetail,
    status_code=status.HTTP_201_CREATED,
)
def duplicate(db: ConfigDb, profile_ref: str, body: ProfileDuplicate):
    """Clones settings and universe, not the history.

    It is the experiment's central gesture (F5.4): with the copy you change
    **one** parameter and compare the two curves. Inheriting the original's
    history would be exactly what makes them incomparable.
    """
    source = find_profile(db, profile_ref)
    try:
        profile_id = duplicate_profile(
            db, source["id"], name=body.name, description=body.description
        )
    except ConfigError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc
    except DatabaseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return queries.profile_detail(db, db.get_profile(profile_id))


@router.patch("/{profile_ref}", response_model=ProfileDetail)
def patch_profile(db: ConfigDb, profile_ref: str, body: ProfilePatch):
    """Name, description and status. The settings go through another endpoint."""
    profile = find_profile(db, profile_ref)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return queries.profile_detail(db, profile)

    if "status" in changes:
        try:
            db.set_profile_status(profile["id"], changes.pop("status"))
        except DatabaseError as exc:
            raise HTTPException(UNPROCESSABLE, str(exc)) from exc

    if changes:
        # `name` is unique in the schema and is also how the cycle finds the
        # book (`portfolio_name`), so renaming a profile that has history would
        # disconnect it from its book. Renaming is allowed only for what has not
        # traded yet.
        if "name" in changes and changes["name"] != profile["name"]:
            _refuse_rename_with_history(db, profile)
        try:
            db.update_profile(
                profile["id"],
                name=changes.get("name"),
                description=changes.get("description"),
            )
        except DatabaseError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    return queries.profile_detail(db, db.get_profile(profile["id"]))


def _refuse_rename_with_history(db: ConfigDb, profile: dict) -> None:
    cycles = db.query(
        "select count(1) as n from cycles c join portfolios p on p.id = c.portfolio_id "
        "where p.profile_id = ?",
        (profile["id"],),
    )[0]["n"]
    if cycles:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"El perfil {profile['name']!r} ya tiene {cycles} ciclo(s) y su cartera "
            "se llama igual que el. Renombrarlo ahora dejaria el historico colgando "
            "de un nombre que ya no existe. Duplicalo con el nombre nuevo si lo que "
            "quieres es seguir por otro camino.",
        )


@router.delete("/{profile_ref}", response_model=ActionResult)
def delete_profile(
    db: ConfigDb, profile_ref: str,
    confirm: str = Query(
        "", description="Repite el nombre exacto del perfil para confirmar."
    ),
):
    """Deletes the profile **and all of its history**. It cannot be undone.

    Confirming by name is not ceremony: it is the only API call that destroys
    data that took weeks to generate, and a `DELETE` to the wrong URL is a
    one-second gesture.
    """
    profile = find_profile(db, profile_ref)
    if confirm != profile["name"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Para borrar {profile['name']!r} y su historico, repite el nombre: "
            f"?confirm={profile['name']}",
        )
    db.delete_profile(profile["id"])
    return {"ok": True, "message": f"Perfil {profile['name']!r} borrado."}


@router.patch("/{profile_ref}/settings", response_model=SettingsApplied)
def patch_settings(db: ConfigDb, profile_ref: str, body: SettingsUpdate):
    """Updates the agent's settings and returns what actually changed.

    `exclude_unset` is what makes it possible to tell "do not touch this field"
    from "set it to NULL". On the hard limits that difference is the switch of
    F6.5: NULL means "derive it from the sliders again".
    """
    profile = find_profile(db, profile_ref)
    changes = body.model_dump(exclude_unset=True)

    # Booleans go into INTEGER columns. They are converted here so the history in
    # `agent_settings_history` stores "1" and not "True", which is what the CLI
    # and the cycle already write.
    for field, value in list(changes.items()):
        if isinstance(value, bool):
            changes[field] = int(value)

    _check_universe_matches_market(db, profile, changes)

    try:
        applied = db.update_settings(profile["id"], changes, source="ui")
    except DatabaseError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc

    settings = db.get_settings(profile["id"])
    try:
        limits = queries.derived_limits(settings)
    except ConfigError as exc:
        raise HTTPException(UNPROCESSABLE, str(exc)) from exc
    return {"applied": applied, "limits": limits}


@router.put("/{profile_ref}/universe", response_model=ActionResult)
def put_universe(db: ConfigDb, profile_ref: str, body: UniverseUpdate):
    """Replaces the symbols the ingestor follows minute by minute.

    Mind the trap of FE.7: this is **not** the screener's universe, which comes
    from `universe_file`. They are two different things and the interface has to
    say so.
    """
    profile = find_profile(db, profile_ref)
    settings = db.get_settings(profile["id"])
    symbols = sorted({s.strip().upper() for s in body.symbols if s.strip()})

    market = get_market(settings["market"])
    foreign = market.foreign_symbols(symbols)
    if foreign:
        raise HTTPException(
            UNPROCESSABLE,
            _foreign_message(profile["name"], market, foreign),
        )

    db.set_profile_universe(profile["id"], symbols)
    return {
        "ok": True,
        "message": f"{len(symbols)} simbolo(s) en seguimiento para {profile['name']!r}.",
    }


# ----------------------------------------------------------------------

def _check_universe_matches_market(db: ConfigDb, profile: dict, changes: dict) -> None:
    """Stops the profile being left with a universe from another exchange.

    It is FE.5's rule, applied on edit instead of at cycle start. The check
    exists there because the symptom is silent and expensive: the foreign symbol
    does not blow up, it sits on the previous day's close and the analyst decides
    on stale data. Finding out on save is far cheaper than finding out in the log
    of an eleven-at-night cycle.
    """
    if "market" not in changes:
        return
    market = get_market(str(changes["market"]))
    symbols = db.get_profile_universe(profile["id"])
    foreign = market.foreign_symbols(symbols)
    if foreign:
        raise HTTPException(
            UNPROCESSABLE,
            _foreign_message(profile["name"], market, foreign),
        )


def _foreign_message(name: str, market, foreign: list[str]) -> str:
    muestra = ", ".join(foreign[:8]) + ("..." if len(foreign) > 8 else "")
    return (
        f"El perfil {name!r} quedaria en {market.code} ({market.label}) con "
        f"{len(foreign)} simbolo(s) de otra bolsa: {muestra}\n"
        "  Un perfil cubre un solo mercado: de ahi salen el horario, el calendario "
        "y la divisa, y el proyecto no convierte divisa en ningun sitio."
    )
