"""La aplicacion FastAPI: monta los routers y sirve el frontend.

Sustituyo al `http.server` de la biblioteca estandar con el que servia el
dashboard viejo, sin dependencias a proposito. Con ~20 endpoints, escrituras y
SSE esa decision dejo de compensar: enrutado a mano, validacion a mano y sin
async (D5). Dentro de Docker las dependencias son gratis. Desde F4.11 esto es lo
unico que sirve la interfaz.

Cuatro cosas que decide este modulo:

  * **El esquema se aplica al arrancar**, abriendo una vez la base con la
    conexion normal. Sin eso, una instalacion recien clonada daria 503 en todo
    hasta que alguien ejecutara un ciclo, y `docker compose up` tiene que
    levantar algo que funcione.
  * **El frontend se sirve desde `app/dist`** con vuelta a `index.html` para las
    rutas del SPA (F3.7). Si ese build falta —`npm run build` sin ejecutar—, el
    hueco se rellena con una pagina que lo dice, no con un 404 que parezca una
    averia. En Docker no puede faltar: lo compila la etapa 1 del Dockerfile.
  * **La vuelta a `index.html` no se traga los 404 de la API.** Cualquier ruta
    bajo `/api/` que no exista responde 404 en JSON. Sin esa excepcion, una
    errata en una URL de la interfaz devolveria el HTML de la aplicacion con un
    200, y el sintoma seria un `JSON.parse` fallando en el navegador tres capas
    mas abajo.
  * **Escucha en loopback por defecto y no tiene autenticacion** (F3.8): son
    datos de una cuenta de inversion en la maquina de uno.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from src.db import Database, DatabaseError

from .deps import ApiConfig, is_loopback
from .routes import control, market, profiles, stream, trading
from .runner import CycleRunner

log = logging.getLogger(__name__)

DESCRIPTION = """
API local del agente de trading. Sin autenticacion: escucha en loopback.

* Las escrituras se limitan a las tablas de configuracion (`profiles`,
  `agent_settings`, `profile_universe`). El historico de operativa es de solo
  lectura para esta API, y lo garantiza un autorizador de SQLite, no una
  convencion.
* Operar es cosa de `run.py cycle`, que corre como proceso aparte.
"""


def create_app(config: ApiConfig | None = None) -> FastAPI:
    """Construye la aplicacion. `config` explicito es lo que usan los tests."""
    settings = config or ApiConfig.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _ensure_database(settings.db_path)
        yield

    app = FastAPI(
        title="financial-agent",
        description=DESCRIPTION,
        version="3.0",
        lifespan=lifespan,
    )
    app.state.config = settings
    # None cuando los controles estan apagados. Los endpoints de control miran
    # esto: sin runner no hay forma de disparar nada.
    app.state.runner = CycleRunner() if settings.controls else None

    app.include_router(profiles.router)
    app.include_router(trading.router)
    app.include_router(market.router)
    app.include_router(control.router)
    app.include_router(stream.router)

    _mount_frontend(app, settings.app_dist)
    return app


def _ensure_database(db_path: str) -> None:
    """Crea la base y aplica migraciones si hace falta.

    Se abre con la conexion normal —no la acotada de `guard.py`— porque aplicar
    `schema.sql` es crear tablas, y el autorizador lo prohibe. Es una ventana de
    un solo uso, al arrancar, con SQL que sale del repositorio.
    """
    try:
        with Database(path=db_path):
            pass
    except (DatabaseError, OSError) as exc:
        # No se aborta el arranque: la API tiene que poder levantarse para decir
        # que la base no esta disponible. Cada endpoint devolvera 503.
        log.warning("No se pudo preparar la base de datos %s: %s", db_path, exc)


# ----------------------------------------------------------------------
# Frontend
# ----------------------------------------------------------------------

PLACEHOLDER = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>financial-agent &mdash; API</title>
<style>
 body{font:16px/1.6 system-ui,sans-serif;max-width:44rem;margin:4rem auto;padding:0 1.5rem;
      background:#0f1115;color:#d8dee9}
 code{background:#1b1f27;padding:.15rem .4rem;border-radius:4px}
 a{color:#7aa2f7}
</style></head><body>
<h1>La API esta en marcha</h1>
<p>El frontend de React todavia no esta construido: no existe <code>app/dist</code>.
   Es lo que trae F4; hasta entonces esta pagina ocupa su sitio.</p>
<ul>
  <li>Documentacion interactiva: <a href="/docs">/docs</a></li>
  <li>Esquema OpenAPI: <a href="/openapi.json">/openapi.json</a></li>
  <li>Perfiles: <a href="/api/profiles">/api/profiles</a></li>
  <li>Salud del ingestor: <a href="/api/ingest-status">/api/ingest-status</a></li>
</ul>
<p>Para construir el frontend cuando exista:
   <code>cd app &amp;&amp; npm install &amp;&amp; npm run build</code></p>
</body></html>
"""


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Sirve el build del SPA, o el marcador si todavia no hay build.

    No se usa `StaticFiles(html=True)` montado en `/` porque se traga el
    enrutado: cualquier ruta desconocida —incluidas las de `/api`— acabaria
    respondiendo el `index.html`. Con una ruta comodin registrada **la ultima**,
    los routers de la API siguen mandando y solo lo que no es de la API cae en
    el SPA.
    """
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(request: Request, full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No existe el endpoint /{full_path}."
            )

        if not index.is_file():
            return HTMLResponse(PLACEHOLDER)

        # Un fichero real del build (JS, CSS, iconos) se sirve tal cual; el resto
        # de rutas son del router del SPA y les toca el index.
        candidate = (dist / full_path).resolve() if full_path else None
        if (
            candidate is not None
            and candidate.is_file()
            and dist.resolve() in candidate.parents
        ):
            return FileResponse(candidate)
        return FileResponse(index)

    @app.exception_handler(404)
    async def _not_found(request: Request, exc: HTTPException):
        detail = getattr(exc, "detail", "No encontrado.")
        return JSONResponse({"detail": detail}, status_code=404)


#: Instancia para `uvicorn api.main:app`. Se crea al importar, que es lo que
#: espera uvicorn cuando se le pasa la ruta del modulo.
app = create_app()


def serve(*, host: str = "", port: int = 0, db_path: str = "") -> int:
    """Arranca uvicorn. Es lo que llama `run.py api`."""
    import uvicorn

    config = ApiConfig.load(db_path=db_path or None)
    host = host or config.host
    port = port or config.port
    application = create_app(config)

    print(f"\n  API en http://{host}:{port}")
    print(f"  Documentacion: http://{host}:{port}/docs")
    print(f"  Base de datos: {config.db_path}")
    if config.controls:
        print("  Lanzar ciclos: ACTIVO")
        if not is_loopback(host):
            print()
            print("  " + "!" * 66)
            print(f"  AVISO: escuchando en {host}, no solo en localhost, y los")
            print("  controles estan activos. No hay autenticacion: cualquiera que")
            print("  alcance este puerto puede gastar tu cuota del modelo, mover la")
            print("  cartera y borrar perfiles. Si el puerto no esta publicado solo")
            print("  en 127.0.0.1, desactivalos con  API_CONTROLS=false")
            print("  " + "!" * 66)
    else:
        print("  Lanzar ciclos: desactivado (API_CONTROLS=false)")
    print("\n  Ctrl+C para detener.\n")

    uvicorn.run(application, host=host, port=port, log_level="info")
    return 0
