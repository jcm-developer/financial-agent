"""Lanzar un ciclo desde la interfaz, como subproceso.

Es el patron que ya estaba resuelto en `web/server.py` y que F3.4 manda
reaprovechar. Se copia en vez de importarse porque `web/server.py` se borra en
F8.2: una dependencia apuntando a un modulo con fecha de caducidad se convierte
en un fallo el dia que se cumpla.

**Subproceso y no un hilo con `TradingCycle` dentro.** Tres razones, y ninguna es
teorica:

  * Un ciclo tarda veinte minutos y hace llamadas de red largas. Aislado, un
    fallo suyo no se lleva por delante el servidor.
  * Se puede matar. Un hilo de Python no.
  * **La API no puede escribir en el historico** (ver `guard.py`), y el ciclo
    tiene que hacerlo. Dentro del proceso serviria de poco acotar la conexion de
    la API si el ciclo escribiera desde el mismo sitio; fuera, quien opera es
    `run.py cycle` con su propia conexion, igual que si lo hubiera lanzado el
    planificador.
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
#: Lineas de log que se guardan para enseñar en la interfaz. Es un buffer
#: circular: un ciclo largo escribe miles y solo interesan las ultimas.
LOG_TAIL = 400


class CycleRunner:
    """Un ciclo a la vez, con su salida disponible para la interfaz."""

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
        self, *, profile: str | None = None, dry_run: bool = False
    ) -> tuple[bool, str]:
        with self._lock:
            if self.running:
                return False, "Ya hay un ciclo en marcha."

            command = [sys.executable, "run.py", "cycle"]
            if profile:
                command += ["--profile", profile]
            if dry_run:
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
                return False, f"No se pudo lanzar el ciclo: {exc}"

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
        """Vuelca la salida del subproceso al buffer circular."""
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
        """Las lineas a partir de `index`, para el SSE.

        Devuelve tambien el indice nuevo. El buffer es circular, asi que si el
        cliente se queda muy atras se le sirve lo que hay: perder lineas viejas
        de un log en vivo es aceptable, bloquear el flujo por conservarlas no.
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
        """Etapa actual, a partir de las marcas que el ciclo deja en el log.

        Se deduce del texto y no de un canal estructurado porque el ciclo ya
        escribe esas lineas para la consola: inventar un protocolo aparte
        obligaria a `cycle.py` a saber que existe una interfaz.
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


#: Estado que se sirve cuando los controles estan apagados (F3.8). La forma es
#: la misma que la de `status()` para que la interfaz no tenga dos caminos.
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
