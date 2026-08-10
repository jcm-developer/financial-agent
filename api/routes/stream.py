"""Live prices and cycle state, over Server-Sent Events (F3.5 / D6).

**What this endpoint really does, said without dressing it up: it polls.** The
ingestor runs in another process (D1), so there is no way for it to tell this one
that it has just written a tick; there is no event bus and none is going to be
built for three processes sharing a file. What SSE does here is **move the
polling from the browser to the server**: instead of N tabs asking for
`/api/quotes` every two seconds over HTTP, there is one loop per connection
looking at a local SQLite file, and something is only sent when it changes. That
is the real gain, and it is worth writing down so nobody ends up believing there
is real push.

SSE was chosen over WebSocket for what D6 says: it is one-directional —which is
all that is needed—, it travels over ordinary HTTP and **the browser reconnects
by itself**. A WebSocket would force the reconnection to be written by hand,
which is precisely the part that always gets done wrong.

Three kinds of event, named, so the client can subscribe to what it cares about
without parsing what it does not:

  * `quotes`  — new quotes. Only when `max(updated_at)` changes.
  * `cycle`   — state of the running cycle, with the new log lines.
  * `ingest`  — ingestor health, when the verdict changes.
  * `ping`    — a heartbeat every 15 s, so a proxy does not call the connection dead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from src.db import Database, DatabaseError

from .. import queries
from ..deps import ApiConfig, get_config, get_runner
from ..runner import DISABLED_STATUS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["stream"])

Runner = Annotated[Any, Depends(get_runner)]
Config = Annotated[ApiConfig, Depends(get_config)]

#: How often a heartbeat is sent even when there is no news.
PING_SECONDS = 15.0


def _event(name: str, payload: Any) -> str:
    body = json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False)
    return f"event: {name}\ndata: {body}\n\n"


def _with_external(state: dict[str, Any], external: bool) -> dict[str, Any]:
    """Adds the scheduler's cycle to the runner's own state.

    The stage text is replaced too: `inactivo` is what the runner says about
    itself, and it is what read as "nothing is happening" while a cycle ran in
    another container.
    """
    if not external or state["running"]:
        return state
    return {
        **state,
        "external": True,
        "stage": "en marcha, lanzado por el planificador",
    }


def _read_state(db_path: str, symbols: list[str] | None) -> dict[str, Any]:
    """A snapshot of what may have changed. It blocks: it goes to its own thread.

    The connection is opened and closed on each pass, as everywhere else in the
    API: a connection left alive for hours inside a generator would hold on to a
    WAL snapshot and end up serving stale prices, which is exactly the opposite
    of what this endpoint is for.
    """
    with Database(path=db_path, read_only=True) as db:
        rows = queries.quotes(db, symbols=symbols)
        marca = max((row["updated_at"] for row in rows), default="")
        salud = queries.ingest_status(db, recent=1)
        # Asked here and not only in `/control/status`, because this event
        # overwrites that endpoint's answer in the cache every time it is sent: a
        # scheduler cycle detected by the query would be un-detected by the next
        # `cycle` event, and the symptom would be a panel that tells the truth for
        # two seconds after a reload (F4.19).
        externo = queries.cycle_running_elsewhere(db)
    return {
        "quotes": rows,
        "mark": marca,
        "cycle_external": externo,
        "ingest": {
            "healthy": salud["healthy"],
            "message": salud["message"],
            "last_tick_at": salud["last_tick_at"],
            "seconds_since_last_tick": salud["seconds_since_last_tick"],
            "consecutive_failures": salud["consecutive_failures"],
        },
    }


@router.get("/stream")
async def stream(
    request: Request,
    config: Config,
    runner: Runner,
    symbols: str = Query(
        "", description="Lista separada por comas. Vacio = todas las conocidas."
    ),
) -> StreamingResponse:
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()] or None

    async def emit() -> AsyncIterator[str]:
        # Full initial state: without it, a client connecting while the market is
        # stopped would see nothing until the first change, and there would be no
        # way to tell that apart from a broken connection.
        marca = ""
        ingest_msg = None
        cursor = 0
        silencio = 0.0

        try:
            estado = await run_in_threadpool(_read_state, config.db_path, wanted)
        except DatabaseError as exc:
            yield _event("error", {"message": str(exc)})
            return

        marca = estado["mark"]
        ingest_msg = estado["ingest"]["message"]
        yield _event("quotes", {"quotes": estado["quotes"], "mark": marca})
        yield _event("ingest", estado["ingest"])
        estado_ciclo = runner.status() if runner is not None else DISABLED_STATUS
        externo = bool(estado.get("cycle_external"))
        cursor = len(estado_ciclo["lines"])
        corriendo = bool(estado_ciclo["running"])
        yield _event("cycle", _with_external(estado_ciclo, externo))

        vencimiento = time.monotonic() + config.stream_max_seconds
        while True:
            await asyncio.sleep(config.stream_interval)
            if await request.is_disconnected():
                break
            if time.monotonic() >= vencimiento:
                # Closed by age, not by error. `EventSource` reconnects on its
                # own —the reason for choosing SSE (D6)— so the client never
                # notices and the server gets the thread and the connection back.
                yield _event("bye", {"reason": "vencimiento", "reconnect": True})
                break

            novedad = False
            try:
                estado = await run_in_threadpool(_read_state, config.db_path, wanted)
            except DatabaseError as exc:
                # The database can be locked for an instant while the ingestor
                # writes. That is a stumble, not the end of the connection.
                log.debug("Lectura del stream fallida: %s", exc)
                estado = None

            if estado is not None:
                if estado["mark"] and estado["mark"] != marca:
                    marca = estado["mark"]
                    yield _event("quotes", {"quotes": estado["quotes"], "mark": marca})
                    novedad = True
                if estado["ingest"]["message"] != ingest_msg:
                    ingest_msg = estado["ingest"]["message"]
                    yield _event("ingest", estado["ingest"])
                    novedad = True

            if estado is not None:
                nuevo_externo = bool(estado.get("cycle_external"))
                if nuevo_externo != externo:
                    externo = nuevo_externo
                    novedad = True

            if runner is not None:
                total, nuevas = runner.lines_since(cursor)
                estado_ciclo = runner.status()
                # The state is sent when there are new lines or when the cycle
                # has just started or finished; the rest of the time nothing
                # changes, and repeating it every two seconds only burns bandwidth.
                if nuevas or bool(estado_ciclo["running"]) != corriendo or novedad:
                    cursor = total
                    corriendo = bool(estado_ciclo["running"])
                    # `lines` carries only what is new and `from` says where it
                    # starts: resending the 400 lines of the buffer every two
                    # seconds would turn "live" into a trickle of megabytes.
                    yield _event(
                        "cycle",
                        {
                            **_with_external(estado_ciclo, externo),
                            "lines": nuevas,
                            "from": total - len(nuevas),
                        },
                    )
                    novedad = True

            silencio = 0.0 if novedad else silencio + config.stream_interval
            if silencio >= PING_SECONDS:
                silencio = 0.0
                yield ": ping\n\n"

    return StreamingResponse(
        emit(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            # Without this, an nginx in front buffers the stream and "live"
            # arrives in bursts several seconds apart.
            "X-Accel-Buffering": "no",
        },
    )
