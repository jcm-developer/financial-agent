"""Launching a cycle from the interface, as a subprocess.

It is the pattern already solved in the old dashboard's `web/server.py`, which
F3.4 said to reuse. It was copied rather than imported because that module had an
expiry date: **it was deleted in F4.11**, and a dependency pointing at it would
have been a failure waiting for the appointed day. Copying it cost one file;
importing it would have cost the API's startup.

**A subprocess and not a thread with `TradingCycle` inside.** Three reasons, none
of them theoretical:

  * A cycle takes twenty minutes and makes long network calls. Isolated, a
    failure of its own does not take the server down with it.
  * It can be killed. A Python thread cannot.
  * **The API cannot write to the history** (see `guard.py`), and the cycle has
    to. In-process, fencing the API's connection would be worth little if the
    cycle wrote from the same place; outside, the one trading is `run.py cycle`
    with its own connection, exactly as if the scheduler had launched it.

**What this class does NOT own any more (F4.21/F4.22).** The log and the stop
travel through two files in the shared volume —`src/cycle_log.py` and
`src/stop_signal.py`— and not through the pipe and the signal of its own
subprocess. The reason is the same for both: the scheduler's cycle runs in another
container, so anything that only reaches this process's child leaves the panel
blind and the button useless for half the cycles that run. What is left here is
what really is this process's own: launching one, and knowing whether the one it
launched is still alive.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import cycle_log, stop_signal

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
#: Log lines kept to show in the interface. A long cycle writes thousands and only
#: the last ones are of interest, so only the tail of the file is read.
LOG_TAIL = 400

#: What the panel says about a cycle this API did not launch. Lowercase because it
#: is written after the label —"Ciclo en marcha — En marcha, lanzado por el
#: planificador"— and the interface is the one that capitalises the stage.
EXTERNAL_STAGE = "en marcha, lanzado por el planificador"


class CycleRunner:
    """One cycle at a time, with its output available to the interface."""

    def __init__(self, *, db_path: str) -> None:
        #: Not to read the history —that never happens from here— but to find the
        #: shared directory: the log and the stop request live next to the database
        #: precisely so both containers see the same two files.
        self._db_path = db_path
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._returncode: int | None = None
        self._dry_run = False
        self._profile: str | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(
        self,
        *,
        profile: str | None = None,
        dry_run: bool = False,
        action: str = "cycle",
    ) -> tuple[bool, str]:
        """Launches `run.py <action>` for one profile.

        `action` exists so closing an experiment (F5.8) reuses this runner
        instead of growing a second one: it is the same subprocess, the same
        lock —one operation per book at a time— and the same log on screen. And
        it has to be a subprocess for the same reason the cycle is: the API
        cannot write to the history, not even by mistake (F3.3).
        """
        with self._lock:
            if self.running:
                return False, "Ya hay un ciclo en marcha."

            command = [sys.executable, "run.py", action]
            if profile:
                command += ["--profile", profile]
            if dry_run and action == "cycle":
                command.append("--dry-run")

            # Emptied here even though the child truncates it on its own: between
            # `Popen` and the child's first line there is a second of Python
            # starting up, and without this the panel would show the previous
            # cycle's log as if it were the new one's.
            cycle_log.truncate(self._db_path)

            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(APP_DIR),
                    # No pipe: the child writes the shared log itself, so there is
                    # nothing to drain here and nothing that can fill up and block
                    # it if this process stops reading.
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                return False, f"No se pudo lanzar {action}: {exc}"

            self._process = process
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._finished_at = None
            self._returncode = None
            self._dry_run = dry_run
            self._profile = profile

        threading.Thread(target=self._wait, args=(process,), daemon=True).start()
        log.info("Ciclo lanzado desde la API (perfil=%s, dry_run=%s).", profile, dry_run)
        return True, "Ciclo lanzado."

    def _wait(self, process: subprocess.Popen) -> None:
        """Waits for the child so its exit code can be shown on screen."""
        process.wait()
        self._returncode = process.returncode
        self._finished_at = datetime.now(timezone.utc).isoformat()
        log.info("Ciclo terminado con codigo %s.", process.returncode)

    def stop(self, cycle_id: str | None = None) -> tuple[bool, str]:
        """Asks the running cycle to stop, whoever launched it (F4.21).

        With a `cycle_id` —the row in 'running', which the route reads from the
        database— the request goes out through `stop_signal`, so it also reaches
        the scheduler's container and the cycle closes its own row with a reason
        instead of dying wherever it happened to be.

        Without one there is nothing registered yet: the cycle is still gathering
        data and has written nothing to the history, so our own subprocess can be
        terminated with nothing left half-written. And if it is not ours, there is
        nothing here to stop — which is the honest answer, not a button that
        pretends.
        """
        if cycle_id:
            try:
                stop_signal.request(self._db_path, cycle_id)
            except OSError as exc:
                return False, f"No se pudo pedir la parada: {exc}"
            return True, (
                "Parada pedida. El ciclo se detiene en su siguiente punto de "
                "control, que puede tardar si esta esperando al modelo."
            )

        with self._lock:
            if not self.running or self._process is None:
                return False, "No hay ningun ciclo en marcha."
            self._process.terminate()
        return True, "Se ha pedido la parada del ciclo, que aun no habia empezado a registrar."

    def status(self) -> dict:
        return {
            "enabled": True,
            "running": self.running,
            "profile": self._profile,
            "dry_run": self._dry_run,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "returncode": self._returncode,
            "lines": cycle_log.read_tail(self._db_path, LOG_TAIL),
            "stage": self._stage(),
            "elapsed_seconds": _seconds_since(self._started_at, self._finished_at),
            "stop_requested": stop_signal.pending(self._db_path) is not None,
        }

    def lines_since(self, index: int) -> tuple[int, list[str]]:
        """The lines from `index` on, for the SSE.

        It also returns the new index. Only the tail of the file is read, so a
        client that falls a long way behind gets whatever is there: losing old
        lines of a live log is acceptable, holding the stream up to keep them is
        not.
        """
        lines = cycle_log.read_tail(self._db_path, LOG_TAIL)
        index = max(0, min(index, len(lines)))
        return len(lines), lines[index:]

    def _stage(self) -> str:
        """The current stage, from the marks the cycle leaves in the log.

        It is inferred from the text and not from a structured channel because
        the cycle already writes those lines for the console: inventing a
        separate protocol would force `cycle.py` to know an interface exists.

        With nothing of ours running it answers "inactivo" without looking at the
        log, and that is deliberate: the file may hold the log of a cycle the
        scheduler ran, and calling that one "terminado" would be this process
        talking about a cycle it never saw. The route replaces the stage in that
        case (`with_external`).
        """
        if not self.running:
            return "terminado" if self._process is not None else "inactivo"

        lines = cycle_log.read_tail(self._db_path, 25)
        if not lines:
            return "arrancando"
        recent = " | ".join(lines)
        for needle, label in (
            ("Resumen del ciclo", "terminando"),
            ("RECHAZADA", "analizando candidatos"),
            ("-> buy", "analizando candidatos"),
            ("-> hold", "analizando candidatos"),
            ("Evaluando", "analizando candidatos"),
            ("Bajando barras", "descargando barras del intervalo"),
            ("Screener", "cribando el universo"),
            ("Cache", "descargando barras"),
            ("Universo", "descargando barras"),
        ):
            if needle in recent:
                return label
        return "en curso"


def with_external(state: dict, cycle: dict[str, Any] | None) -> dict[str, Any]:
    """Folds the cycle nobody here launched into the runner's own state (F4.19).

    `CycleRunner` only knows about the subprocess it spawned itself, and that was
    a lie by omission on screen: a cycle launched by the scheduler left the panel
    saying "Sin ciclo en marcha" while one was running. `cycle` is the row in
    'running' read from the database, and it brings **which** experiment and
    **since when** — which the runner cannot know and which the panel had no way
    to show for a scheduler cycle.

    `external` stays a field of its own rather than being folded into `running`
    because they answer different questions: `running` is "may I launch one?" and
    `external` is "is one running that is not mine?". What changed in F4.21 is the
    answer to a third: stopping it **is** possible now, through `stop_signal`, so
    the two states share the button.

    It lives here, next to the runner, and not in a route because **three callers
    need it**: `/control/status`, the SSE —which overwrites that entry of the
    cache with every event— and any test that wants to check the shape once.
    """
    if not state["running"] and cycle is not None:
        state = {
            **state,
            "external": True,
            "stage": EXTERNAL_STAGE,
            "profile": cycle.get("profile") or state.get("profile"),
            "started_at": cycle.get("started_at") or state.get("started_at"),
            # Nothing has finished: what finished is whatever this process ran
            # last, and reporting its timestamp beside a live cycle would read as
            # this one having ended.
            "finished_at": None,
            "returncode": None,
            "elapsed_seconds": _seconds_since(cycle.get("started_at")),
        }
    if not (state["running"] or state.get("external")):
        # A request that arrived after the cycle had already finished leaves the
        # file behind until the next cycle clears it. Reported with nothing
        # running, it would light up "parada solicitada" in the panel for ever.
        state = {**state, "stop_requested": False}
    return state


def _seconds_since(started_at: str | None, finished_at: str | None = None) -> int | None:
    """Seconds between two ISO instants, the second one defaulting to now."""
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = (
            datetime.fromisoformat(finished_at) if finished_at
            else datetime.now(timezone.utc)
        )
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, int((end - start).total_seconds()))


#: The state served when the controls are switched off (F3.8). The shape matches
#: `status()` so the interface does not need two code paths.
DISABLED_STATUS = {
    "enabled": False,
    "running": False,
    "profile": None,
    "dry_run": False,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "lines": [],
    "stage": "controles desactivados",
    "elapsed_seconds": None,
    "stop_requested": False,
}
