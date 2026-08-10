"""The running cycle's log, in a file both containers can reach.

The Ciclos screen shows the log of the cycle in flight, and until now it could
only show **the one the API launched itself**: `api/runner.py` read its own
subprocess's pipe, and a cycle fired by the scheduler runs in another container
where that pipe does not exist. So the panel went blank for exactly the cycles
nobody is watching from a terminal, which are the ones the screen is for.

The log therefore goes where the processes already meet: **a file next to the
database**, inside the volume the four services share. It is written by whoever
runs the cycle —`run.py`, no matter whether the scheduler, the API or a
hand-typed command launched it— and read by the API. One writer at a time,
because only one cycle per book may run at a time (`Database.find_running_cycle`).

**It is written by the entry point and not by `cycle.py`.** The lines are the ones
the cycle already prints for the console; the alternative was a second, structured
channel, and that would force `cycle.py` to know an interface exists. Here
`run.py` mirrors its own stdout and the cycle stays unaware, which is the same
reason `api/runner.py` deduces the stage from the text instead of asking for it.

**Not the database**, which was the other candidate. A row per line is a write per
line competing with the ingestor for the lock, plus a table to prune. The log of a
cycle in flight is worth exactly as long as the cycle lasts, and a file truncated
at every start says so by itself.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

log = logging.getLogger(__name__)

#: Name of the file, next to the database. Singular on purpose: there is never
#: more than one cycle running, so a file per cycle would only add names to prune.
LOG_NAME = "cycle.log"

#: How much of the tail is read at most. A cycle in DEBUG can write megabytes and
#: this is read on every pass of the stream, so the end of the file is seeked to
#: rather than loading whatever happens to be there. Way above the 400 lines the
#: interface keeps.
MAX_TAIL_BYTES = 2 * 1024 * 1024


def path_for(db_path: str | Path) -> Path:
    """Where the log lives for a given database.

    Derived from `db_path` and not configured separately because the point is
    that it lands in the **same volume**: in Docker `/app/data/trading.db` and
    `/app/data/cycle.log`, so the API reads what the scheduler wrote. A second
    environment variable could have them pointing at different places, and the
    symptom would be an empty panel with nothing wrong in sight.
    """
    return Path(db_path).resolve().parent / LOG_NAME


def truncate(db_path: str | Path) -> None:
    """Empties the log, without demanding that it exist.

    `api/runner.py` calls it just before launching: between `Popen` and the
    child's first line there is a second of Python starting up, and without this
    the panel would show the previous cycle's log as if it were the new one's.
    """
    try:
        path_for(db_path).write_text("", encoding="utf-8")
    except OSError as exc:
        log.debug("No se pudo vaciar el log del ciclo: %s", exc)


def read_tail(db_path: str | Path, limit: int) -> list[str]:
    """The last `limit` lines, or an empty list if there is no log yet.

    A missing file is not an error: it is what "no cycle has run since this
    volume was created" looks like, and the panel already knows how to show
    nothing.
    """
    path = path_for(db_path)
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(0, size - MAX_TAIL_BYTES))
            raw = handle.read()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace")
    return text.splitlines()[-limit:] if limit > 0 else []


@contextmanager
def capture(db_path: str | Path) -> Iterator[Path | None]:
    """Mirrors this process's output into the log while the block runs.

    **It has to be entered before `logging.basicConfig`**, and that order is the
    whole trick: `basicConfig(stream=sys.stdout)` keeps the object it is handed,
    so a stdout replaced afterwards would leave every log line out of the file
    and only the `print`s in it.

    Yields the file's path, or None when it could not be opened: the log is the
    interface's copy of the cycle, and no cycle is going to be given up over it.
    """
    path = path_for(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        mirror = path.open("w", encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("No se pudo abrir %s para el log del ciclo: %s", path, exc)
        yield None
        return

    console_out, console_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(console_out, mirror)  # type: ignore[assignment]
    sys.stderr = _Tee(console_err, mirror)  # type: ignore[assignment]
    try:
        yield path
    finally:
        sys.stdout, sys.stderr = console_out, console_err
        try:
            mirror.close()
        except OSError:  # pragma: no cover - cerrar no falla en la practica
            pass


class _Tee:
    """Writes to the console and to the file, flushing the file on every write.

    **The flush is the point.** With stdout redirected, Python block-buffers, so
    `print` would land in the file in 8 KB lumps and "en vivo" would be a lie —
    `logging` flushes on each record, but the cycle's summary goes out with
    `print`. The cost is one flush per line, a few hundred times per cycle.

    A failure writing to the mirror is swallowed on purpose. The file is the
    interface's copy; the cycle is the operation. Losing the tail of a log is a
    nuisance, taking down a cycle that is holding positions is not acceptable.
    """

    def __init__(self, console: TextIO, mirror: TextIO) -> None:
        self.console = console
        self.mirror = mirror

    def write(self, text: str) -> int:
        written = self.console.write(text)
        try:
            self.mirror.write(text)
            self.mirror.flush()
        except (OSError, ValueError):
            pass
        return written

    def flush(self) -> None:
        self.console.flush()
        try:
            self.mirror.flush()
        except (OSError, ValueError):
            pass

    def writable(self) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        # `isatty`, `encoding`, `fileno` and the rest of the text-stream surface.
        # Whoever asks is asking about the console —a mirrored file is nobody's
        # terminal— so everything not written above is delegated rather than
        # reimplemented one method at a time.
        return getattr(self.console, name)
