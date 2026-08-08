"""Precios y estado del ciclo en vivo, por Server-Sent Events (F3.5 / D6).

**Lo que este endpoint hace de verdad, dicho sin adornos: sondea.** El ingestor
corre en otro proceso (D1), asi que no hay forma de que avise a este de que
acaba de escribir un tick; no hay bus de eventos ni se va a montar uno para tres
procesos que comparten un fichero. Lo que hace SSE aqui es **mover el sondeo del
navegador al servidor**: en lugar de N pestañas pidiendo `/api/quotes` cada dos
segundos por HTTP, hay un bucle por conexion mirando un fichero SQLite local, y
solo se manda algo cuando cambia. Esa es la ganancia real, y conviene tenerla
escrita para no acabar creyendo que hay empuje de verdad.

Se eligio SSE y no WebSocket por lo que dice D6: es unidireccional —que es todo
lo que hace falta—, va sobre HTTP normal y **el navegador reconecta solo**. Un
WebSocket obligaria a escribir a mano la reconexion, que es justo el trozo que
siempre se hace mal.

Tres tipos de evento, con nombre, para que el cliente se suscriba a lo que le
interese sin parsear lo que no:

  * `quotes`  — cotizaciones nuevas. Solo cuando cambia `max(updated_at)`.
  * `cycle`   — estado del ciclo en curso, con las lineas de log nuevas.
  * `ingest`  — salud del ingestor, cuando cambia el veredicto.
  * `ping`    — latido cada 15 s, para que un proxy no de la conexion por muerta.
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

#: Cada cuanto se manda un latido aunque no haya novedades.
PING_SECONDS = 15.0


def _event(name: str, payload: Any) -> str:
    body = json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False)
    return f"event: {name}\ndata: {body}\n\n"


def _read_state(db_path: str, symbols: list[str] | None) -> dict[str, Any]:
    """Una foto de lo que puede haber cambiado. Bloquea: va a un hilo aparte.

    Se abre y se cierra la conexion en cada pasada, igual que en el resto de la
    API: una conexion viva durante horas dentro de un generador se quedaria con
    una instantanea de WAL y acabaria sirviendo precios viejos, que es
    exactamente lo contrario de lo que hace este endpoint.
    """
    with Database(path=db_path, read_only=True) as db:
        rows = queries.quotes(db, symbols=symbols)
        marca = max((row["updated_at"] for row in rows), default="")
        salud = queries.ingest_status(db, recent=1)
    return {
        "quotes": rows,
        "mark": marca,
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
        # Estado inicial completo: sin esto, un cliente que se conecta con el
        # mercado parado no veria nada hasta el primer cambio, y no habria forma
        # de distinguirlo de una conexion rota.
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
        cursor = len(estado_ciclo["lines"])
        corriendo = bool(estado_ciclo["running"])
        yield _event("cycle", estado_ciclo)

        vencimiento = time.monotonic() + config.stream_max_seconds
        while True:
            await asyncio.sleep(config.stream_interval)
            if await request.is_disconnected():
                break
            if time.monotonic() >= vencimiento:
                # Se cierra por edad, no por error. `EventSource` reconecta solo
                # —es la razon de elegir SSE (D6)— asi que el cliente no se
                # entera y el servidor recupera el hilo y la conexion.
                yield _event("bye", {"reason": "vencimiento", "reconnect": True})
                break

            novedad = False
            try:
                estado = await run_in_threadpool(_read_state, config.db_path, wanted)
            except DatabaseError as exc:
                # La base puede estar bloqueada un instante mientras el ingestor
                # escribe. Es un tropiezo, no el fin de la conexion.
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

            if runner is not None:
                total, nuevas = runner.lines_since(cursor)
                estado_ciclo = runner.status()
                # Se manda el estado si hay lineas nuevas o si el ciclo acaba de
                # arrancar o de terminar; el resto del tiempo no cambia nada, y
                # repetirlo cada dos segundos solo gasta ancho de banda.
                if nuevas or bool(estado_ciclo["running"]) != corriendo:
                    cursor = total
                    corriendo = bool(estado_ciclo["running"])
                    # `lines` va con solo lo nuevo y `from` dice desde donde:
                    # reenviar las 400 lineas del buffer cada dos segundos
                    # convertiria el "en vivo" en un goteo de megabytes.
                    yield _event(
                        "cycle",
                        {**estado_ciclo, "lines": nuevas, "from": total - len(nuevas)},
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
            # Sin esto, un nginx delante bufferiza el flujo y el "en vivo" llega
            # a rafagas de varios segundos.
            "X-Accel-Buffering": "no",
        },
    )
