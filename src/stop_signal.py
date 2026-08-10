"""Asking a running cycle to stop, from a process that cannot signal it.

`Parar` on the Ciclos screen could only stop **the cycle the API launched
itself**: it was a `terminate()` over its own subprocess. The scheduler's cycle
runs in another container, so there was no process to signal and the button came
up disabled with a `title` sending you to `docker compose restart scheduler` —
which stops the container, not the cycle, and leaves its row in 'running' until
the 90 minutes of `STALE_CYCLE_MINUTES` age it out.

So the request travels the way the log does: **a file next to the database**, in
the volume the four services share. The API writes the id of the cycle to stop;
the cycle looks for it at its checkpoints and shuts itself down in order — its
equity snapshot saved and its row finished with a reason, which is precisely what
a signal cannot give.

**The id, and not a bare flag.** A request that arrives a second too late would
otherwise stop the *next* cycle, which nobody asked for and which would look like
the scheduler skipping a session. Naming the cycle makes a leftover file
harmless, and the cycle deletes any it finds when it registers, because at that
instant no request can be for it.

**Cooperative for both cases, not only the remote one.** A SIGTERM cuts the
process wherever it happens to be —possibly between sending an order and
recording it— so the same mechanism is used for the API's own subprocess: one
concept, one behaviour, one thing to explain on screen. The price is that the
stop is not instant: it lands at the next checkpoint, which while a slow model
call is in flight can be a minute away. The interface says that instead of
pretending the click did everything.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: Name of the file, next to the database. See `src/cycle_log.py` for why the
#: shared directory is derived from `db_path` and not configured on its own.
REQUEST_NAME = "stop.request"


def path_for(db_path: str | Path) -> Path:
    return Path(db_path).resolve().parent / REQUEST_NAME


def request(db_path: str | Path, cycle_id: str) -> None:
    """Asks for `cycle_id` to stop. Raises `OSError` if it cannot be written.

    The error is not swallowed: whoever presses the button has to be told that
    nothing was requested. A stop that silently fails is the same bug F4.19 came
    to fix, only in the other direction.
    """
    path = path_for(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cycle_id, encoding="utf-8")
    log.info("Parada pedida para el ciclo %s.", cycle_id)


def pending(db_path: str | Path) -> str | None:
    """Which cycle has a stop pending, if any.

    The API reads it to say so on screen: without that, pressing Parar changed
    nothing visible until the cycle reached a checkpoint, and a button that looks
    like it did nothing gets pressed again.
    """
    try:
        cycle_id = path_for(db_path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return cycle_id or None


def requested_for(db_path: str | Path, cycle_id: str) -> bool:
    """Whether the pending request —if there is one— names this cycle."""
    return pending(db_path) == cycle_id


def clear(db_path: str | Path) -> None:
    """Removes the request, without demanding that it exist.

    Called by the cycle in two places: when it registers, so a stale request does
    not stop the wrong cycle, and when it honours one, so it is not honoured
    twice.
    """
    try:
        path_for(db_path).unlink(missing_ok=True)
    except OSError as exc:
        # If it cannot be deleted, the next cycle would find it and ignore it by
        # name anyway. Worth a line in the log, not worth an exception.
        log.warning("No se pudo borrar la peticion de parada: %s", exc)
