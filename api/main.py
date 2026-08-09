"""The FastAPI application: it mounts the routers and serves the frontend.

It replaced the standard library's `http.server` that used to serve the old
dashboard, deliberately without dependencies. With ~20 endpoints, writes and SSE
that decision stopped paying off: routing by hand, validation by hand and no
async (D5). Inside Docker the dependencies are free. Since F4.11 this is the only
thing serving the interface.

Four things this module decides:

  * **The schema is applied at startup**, opening the database once with the
    normal connection. Without that, a freshly cloned install would give 503 on
    everything until someone ran a cycle, and `docker compose up` has to bring up
    something that works.
  * **The frontend is served from `app/dist`** with a fallback to `index.html`
    for the SPA's routes (F3.7). If that build is missing —`npm run build` never
    run—, the gap is filled with a page that says so, not with a 404 that would
    look like a breakage. In Docker it cannot be missing: stage 1 of the
    Dockerfile compiles it.
  * **The fallback to `index.html` does not swallow the API's 404s.** Any route
    under `/api/` that does not exist answers 404 in JSON. Without that
    exception, a typo in one of the interface's URLs would return the
    application's HTML with a 200, and the symptom would be a `JSON.parse`
    failing in the browser three layers down.
  * **It listens on loopback by default and has no authentication** (F3.8): this
    is data from an investment account on someone's own machine.
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
    """Builds the application. An explicit `config` is what the tests use."""
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
    # None when the controls are switched off. The control endpoints look at
    # this: with no runner there is no way to fire anything.
    app.state.runner = CycleRunner() if settings.controls else None

    app.include_router(profiles.router)
    app.include_router(trading.router)
    app.include_router(market.router)
    app.include_router(control.router)
    app.include_router(stream.router)

    _mount_frontend(app, settings.app_dist)
    return app


def _ensure_database(db_path: str) -> None:
    """Creates the database and applies migrations if needed.

    It is opened with the normal connection —not the fenced one from `guard.py`—
    because applying `schema.sql` means creating tables, and the authorizer
    forbids that. It is a single-use window, at startup, with SQL that comes from
    the repository.
    """
    try:
        with Database(path=db_path):
            pass
    except (DatabaseError, OSError) as exc:
        # Startup is not aborted: the API has to be able to come up in order to
        # say the database is unavailable. Every endpoint will return 503.
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
    """Serves the SPA build, or the placeholder when there is no build yet.

    `StaticFiles(html=True)` mounted at `/` is not used because it swallows the
    routing: any unknown route —including those under `/api`— would end up
    answering with `index.html`. With a wildcard route registered **last**, the
    API's routers still win and only what is not the API's falls through to the
    SPA.
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

        # A real file from the build (JS, CSS, icons) is served as-is; every other
        # route belongs to the SPA's router and gets the index.
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


#: The instance for `uvicorn api.main:app`. It is created on import, which is
#: what uvicorn expects when it is given a module path.
app = create_app()


def serve(*, host: str = "", port: int = 0, db_path: str = "") -> int:
    """Starts uvicorn. This is what `run.py api` calls."""
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
