"""Configuracion de la API y dependencias que comparten los endpoints.

La regla que gobierna este modulo: **cada peticion abre y cierra su propia
conexion**. Es el mismo criterio que ya seguia `web/server.py`, y por el mismo
motivo: es un servidor de un solo usuario contra un fichero local, abrirlo cuesta
menos de un milisegundo y asi no se sirven datos rancios mientras el ciclo o el
ingestor escriben en paralelo. Una conexion viva de larga duracion tendria que
preocuparse por las instantaneas de WAL; esta no.

Hay **dos** dependencias de base de datos y no una, y la diferencia es el
contrato de D5:

  * `read_db` abre SQLite en modo `ro`. No es una promesa, es una
    imposibilidad: por ahi no se puede escribir aunque el codigo lo intente.
  * `config_db` abre la conexion con autorizador de `guard.py`, que solo deja
    escribir en las tablas de configuracion.

Ningun endpoint recibe una conexion de escritura sin acotar. La unica escritura
sin limites del proyecto la hacen el ciclo y el ingestor, cada uno en su proceso.
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

#: 422. Se escribe el numero y no `status.HTTP_422_...` porque Starlette ha
#: renombrado esa constante (`..._UNPROCESSABLE_ENTITY` -> `..._CONTENT`) y usar
#: cualquiera de los dos nombres ata el proyecto a un rango de versiones a
#: cambio de nada: el codigo HTTP es el mismo desde 1999.
UNPROCESSABLE = 422

REPO_ROOT = Path(__file__).resolve().parent.parent
#: Donde deja Vite el build del frontend (F4.1). Todavia no existe: `main.py`
#: sirve un marcador mientras tanto.
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
    """Lo que la API necesita saber del entorno. Nada de estrategia.

    Igual que `Infra`: si algun dia hiciera falta añadir aqui un parametro del
    experimento, seria señal de que su sitio es `agent_settings` (F6.4).
    """

    db_path: str
    host: str = "127.0.0.1"
    port: int = 8000
    #: Si estan los endpoints que disparan ciclos. Ver `controls_enabled`.
    controls: bool = True
    #: Cada cuanto mira el servidor si hay algo nuevo que empujar por SSE.
    stream_interval: float = 2.0
    #: Cuanto vive como mucho una conexion de SSE antes de cerrarse sola.
    #:
    #: No es una limitacion, es higiene: `EventSource` reconecta solo —esa es la
    #: razon de elegir SSE en D6— asi que cortar cada cierto tiempo devuelve los
    #: recursos del servidor sin que el cliente note nada. Y de paso obliga a
    #: releer la lista de simbolos: un stream eterno seguiria sirviendo el
    #: universo que tenia el perfil cuando alguien abrio la pestaña.
    stream_max_seconds: float = 900.0
    app_dist: Path = APP_DIST

    @classmethod
    def load(cls, *, db_path: str | None = None) -> ApiConfig:
        infra = Infra.load()
        return cls(
            db_path=db_path or infra.db_path,
            host=(os.getenv("API_HOST") or "127.0.0.1").strip(),
            port=int((os.getenv("API_PORT") or "8000").strip()),
            # Por defecto activos, y **no se deduce de `host`**: dentro de Docker
            # hay que escuchar en 0.0.0.0 para que el mapeo de puertos funcione,
            # asi que la direccion de escucha no dice nada sobre quien puede
            # llegar. Quien publique esto de verdad en una red lo apaga a mano.
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
    """La configuracion vive en `app.state`, no en un global.

    Asi los tests pueden levantar varias aplicaciones contra bases distintas en
    el mismo proceso, que es justo lo que hace `tests/test_api.py` con `tmp_path`.
    """
    return request.app.state.config


Config = Annotated[ApiConfig, Depends(get_config)]


def read_db(config: Config) -> Iterator[Database]:
    try:
        db = Database(path=config.db_path, read_only=True)
    except DatabaseError as exc:
        # 503 y no 500: la base todavia no existe o esta bloqueada. Es un estado
        # del sistema, no un fallo del codigo, y la interfaz lo pinta distinto.
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
    """El `CycleRunner` de la aplicacion, o None si los controles estan apagados."""
    return request.app.state.runner


# ----------------------------------------------------------------------
# Resolucion de perfil
# ----------------------------------------------------------------------

def find_profile(db: Database, reference: str) -> dict[str, Any]:
    """Un perfil por id o por nombre.

    Acepta las dos cosas a proposito: los ids son UUID y los nombres son unicos,
    asi que no hay ambiguedad posible, y `/api/positions?profile=europa-01` se
    puede escribir a mano al depurar.

    La interfaz manda **el nombre** (F4 tramo C): el perfil activo va en la URL
    (`/p/europa-01/posiciones`) y con un UUID ahi nadie sabria que experimento
    esta mirando, que era justo el motivo de sacarlo de la memoria de React. Un
    enlace guardado se rompe si el perfil se renombra, y esta bien que se rompa:
    el 404 de aqui abajo dice cuales hay.
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
    """El id de cartera del perfil.

    Un perfil sin cartera no deberia existir —`create_profile` las crea juntas—
    pero si aparece uno a medias, decirlo es mejor que devolver listas vacias
    que se leerian como "todavia no ha operado".
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


#: Parametros de paginacion compartidos por todas las listas.
LimitQuery = Annotated[int, Query(ge=1, le=500, description="Filas por pagina.")]
OffsetQuery = Annotated[int, Query(ge=0, description="Filas que saltar.")]
ProfileQuery = Annotated[
    str, Query(description="Id o nombre del perfil de experimento.")
]
