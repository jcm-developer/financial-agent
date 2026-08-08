"""Perfiles de experimento: lectura (F3.2) y escritura (F3.3).

Es el unico router que escribe, y lo hace contra `ConfigDb`, la conexion con
autorizador de `guard.py`. Esa eleccion no es decorativa: es lo que convierte
"la API solo toca la configuracion" de una intencion en una propiedad del
sistema. Un `where` mal puesto en cualquiera de estos endpoints no puede borrar
una posicion, porque SQLite no le deja.

La excepcion es `DELETE /api/profiles/{id}`, que arrastra el historico del
experimento a proposito. Por eso pide confirmar repitiendo el nombre: es la
unica operacion irreversible de toda la API.
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


@router.get("/{profile_ref}", response_model=ProfileDetail)
def get_profile(db: ReadDb, profile_ref: str):
    return queries.profile_detail(db, find_profile(db, profile_ref))


@router.get("/{profile_ref}/settings")
def get_settings(db: ReadDb, profile_ref: str) -> dict:
    """Los parametros crudos mas los limites que implican.

    Van juntos porque el formulario de F6.8 los necesita a la vez: enseña el
    deslizador y, al lado, lo que ese deslizador significa en numeros.
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
    """Los nueve limites efectivos, para pintarlos en vivo mientras se edita."""
    profile = find_profile(db, profile_ref)
    return queries.derived_limits(db.get_settings(profile["id"]))


# ----------------------------------------------------------------------
# Escritura
# ----------------------------------------------------------------------

@router.post("", response_model=ProfileDetail, status_code=status.HTTP_201_CREATED)
def create_profile(db: ConfigDb, body: ProfileCreate):
    """Crea un perfil para un mercado, con su universo y su cartera.

    Comparte implementacion con `run.py new-profile`
    (`profile_settings.create_market_profile`): dos copias divergirian, y la
    primera regla en hacerlo seria la de FE.11 —el suelo de liquidez sale del
    mercado—, con el sintoma de que un perfil creado desde aqui descartaria en
    silencio 15 valores que el creado desde la consola si analiza.
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
        # El fichero de universo del repositorio no sirve. No es culpa de quien
        # hace la peticion, asi que no es un 4xx.
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
    """Clona parametros y universo, no el historico.

    Es el gesto central del experimento (F5.4): con la copia se cambia **un**
    parametro y se comparan las dos curvas. Heredar el historico del original
    haria justamente que no se pudieran comparar.
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
    """Nombre, descripcion y estado. Los parametros van por otro endpoint."""
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
        # `name` es unique en el esquema y ademas es como el ciclo encuentra la
        # cartera (`portfolio_name`), asi que renombrar un perfil con historico
        # lo desconectaria de su cartera. Se deja renombrar solo lo que todavia
        # no ha operado.
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
    """Borra el perfil **y todo su historico**. No se deshace.

    La confirmacion por nombre no es ceremonia: es la unica llamada de la API
    que destruye datos que costo semanas generar, y un `DELETE` a la URL
    equivocada es un gesto de un segundo.
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
    """Actualiza parametros del agente y devuelve lo que cambio de verdad.

    `exclude_unset` es lo que permite distinguir "no toques este campo" de
    "ponlo a NULL". En los limites duros esa diferencia es el interruptor de
    F6.5: NULL significa "vuelve a derivarlo de los deslizadores".
    """
    profile = find_profile(db, profile_ref)
    changes = body.model_dump(exclude_unset=True)

    # Los booleanos van a columnas INTEGER. Se convierten aqui para que el
    # historial de `agent_settings_history` guarde "1" y no "True", que es lo que
    # ya escriben la CLI y el ciclo.
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
    """Reemplaza los simbolos que el ingestor sigue minuto a minuto.

    Ojo con la trampa de FE.7: esto **no** es el universo del screener, que sale
    de `universe_file`. Son dos cosas distintas y la interfaz tiene que decirlo.
    """
    profile = find_profile(db, profile_ref)
    settings = db.get_settings(profile["id"])
    symbols = sorted({s.strip().upper() for s in body.symbols if s.strip()})

    mercado = get_market(settings["market"])
    foreign = mercado.foreign_symbols(symbols)
    if foreign:
        raise HTTPException(
            UNPROCESSABLE,
            _foreign_message(profile["name"], mercado, foreign),
        )

    db.set_profile_universe(profile["id"], symbols)
    return {
        "ok": True,
        "message": f"{len(symbols)} simbolo(s) en seguimiento para {profile['name']!r}.",
    }


# ----------------------------------------------------------------------

def _check_universe_matches_market(db: ConfigDb, profile: dict, changes: dict) -> None:
    """Impide dejar el perfil con un universo de otra bolsa.

    Es la regla de FE.5, aplicada al editar en lugar de al arrancar el ciclo. La
    comprobacion existe alli porque el sintoma es silencioso y caro: el simbolo
    forastero no revienta, se queda con el cierre del dia anterior y el analista
    decide sobre datos rancios. Descubrirlo al guardar es mucho mas barato que
    descubrirlo en el log de un ciclo de las once de la noche.
    """
    if "market" not in changes:
        return
    mercado = get_market(str(changes["market"]))
    symbols = db.get_profile_universe(profile["id"])
    foreign = mercado.foreign_symbols(symbols)
    if foreign:
        raise HTTPException(
            UNPROCESSABLE,
            _foreign_message(profile["name"], mercado, foreign),
        )


def _foreign_message(name: str, mercado, foreign: list[str]) -> str:
    muestra = ", ".join(foreign[:8]) + ("..." if len(foreign) > 8 else "")
    return (
        f"El perfil {name!r} quedaria en {mercado.code} ({mercado.label}) con "
        f"{len(foreign)} simbolo(s) de otra bolsa: {muestra}\n"
        "  Un perfil cubre un solo mercado: de ahi salen el horario, el calendario "
        "y la divisa, y el proyecto no convierte divisa en ningun sitio."
    )
