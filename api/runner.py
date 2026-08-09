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
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
#: Log lines kept to show in the interface. It is a ring buffer: a long cycle
#: writes thousands and only the last ones are of interest.
LOG_TAIL = 400


class CycleRunner:
    """One cycle at a time, with its output available to the interface."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._lines: deque[str] = deque(maxlen=LOG_TAIL)
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

            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(APP_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError as exc:
                return False, f"No se pudo lanzar {action}: {exc}"

            self._process = process
            self._lines.clear()
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._finished_at = None
            self._returncode = None
            self._dry_run = dry_run
            self._profile = profile

        threading.Thread(target=self._pump, args=(process,), daemon=True).start()
        log.info("Ciclo lanzado desde la API (perfil=%s, dry_run=%s).", profile, dry_run)
        return True, "Ciclo lanzado."

    def _pump(self, process: subprocess.Popen) -> None:
        """Drains the subprocess's output into the ring buffer."""
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    self._lines.append(line.rstrip())
        finally:
            process.wait()
            self._returncode = process.returncode
            self._finished_at = datetime.now(timezone.utc).isoformat()
            log.info("Ciclo terminado con codigo %s.", process.returncode)

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if not self.running or self._process is None:
                return False, "No hay ningun ciclo en marcha."
            self._process.terminate()
        return True, "Se ha pedido la parada del ciclo."

    def status(self) -> dict:
        return {
            "enabled": True,
            "running": self.running,
            "profile": self._profile,
            "dry_run": self._dry_run,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "returncode": self._returncode,
            "lines": list(self._lines),
            "stage": self._stage(),
            "elapsed_seconds": self._elapsed(),
        }

    def lines_since(self, index: int) -> tuple[int, list[str]]:
        """The lines from `index` on, for the SSE.

        It also returns the new index. The buffer is circular, so a client that
        falls a long way behind gets whatever is there: losing old lines of a
        live log is acceptable, blocking the stream to keep them is not.
        """
        lines = list(self._lines)
        index = max(0, min(index, len(lines)))
        return len(lines), lines[index:]

    def _elapsed(self) -> int | None:
        if not self._started_at:
            return None
        start = datetime.fromisoformat(self._started_at)
        end = (
            datetime.fromisoformat(self._finished_at) if self._finished_at
            else datetime.now(timezone.utc)
        )
        return int((end - start).total_seconds())

    def _stage(self) -> str:
        """The current stage, from the marks the cycle leaves in the log.

        It is inferred from the text and not from a structured channel because
        the cycle already writes those lines for the console: inventing a
        separate protocol would force `cycle.py` to know an interface exists.
        """
        if not self._lines:
            return "arrancando" if self.running else "inactivo"
        recent = " | ".join(list(self._lines)[-25:])
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
        return "en curso" if self.running else "terminado"


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
}
