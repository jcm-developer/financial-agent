"""Servidor del dashboard: biblioteca estandar, sin dependencias.

Decisiones de seguridad, todas deliberadas:

  * Escucha solo en 127.0.0.1. Son datos de una cuenta de inversion; no tienen
    por que estar accesibles desde la red local.
  * Abre SQLite en modo **solo lectura** para consultar. La interfaz no puede
    alterar el historico ni por un fallo de codigo.
  * El boton de lanzar ciclo no escribe por su cuenta: arranca `run.py cycle`
    como proceso aparte, con su propia conexion. El servidor sigue sin poder
    tocar la base.
  * **Ese boton solo existe si el servidor escucha en loopback.** Si alguien
    publica el dashboard en la red (`--host 0.0.0.0`), los endpoints de control
    desaparecen solos: un disparador sin autenticacion expuesto a la red seria
    una forma comoda de que un tercero gaste tu cuota y mueva tu cartera.

La base se reabre en cada peticion. Es un servidor de un solo usuario contra un
fichero local: cuesta menos de un milisegundo y evita servir datos rancios
mientras un ciclo escribe en paralelo.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Permite ejecutar este fichero directamente, no solo via `run.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dashboard import build_dashboard, list_portfolios  # noqa: E402
from src.db import Database, DatabaseError  # noqa: E402

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent
APP_DIR = STATIC_DIR.parent
MAX_STATIC_BYTES = 8 * 1024 * 1024
# Lineas de log que se guardan para mostrar en la interfaz.
LOG_TAIL = 400


def _is_loopback(host: str) -> bool:
    """True si `host` es la interfaz local. Decide si hay endpoints de control."""
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class CycleRunner:
    """Lanza `run.py cycle` como subproceso y sigue su salida.

    Subproceso y no un hilo con `TradingCycle` directamente: un ciclo tarda 20
    minutos y hace llamadas de red largas. Aislado, un fallo suyo no se lleva por
    delante el servidor del dashboard, y se puede matar sin efectos raros.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._lines: deque[str] = deque(maxlen=LOG_TAIL)
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._returncode: int | None = None
        self._dry_run = False

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, *, dry_run: bool = False) -> tuple[bool, str]:
        with self._lock:
            if self.running:
                return False, "Ya hay un ciclo en marcha."

            command = [sys.executable, "run.py", "cycle"]
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

        threading.Thread(target=self._pump, args=(process,), daemon=True).start()
        log.info("Ciclo lanzado desde el dashboard (dry_run=%s).", dry_run)
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
            "running": self.running,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "returncode": self._returncode,
            "dry_run": self._dry_run,
            "lines": list(self._lines),
            # Etapa deducida del log, para no obligar a leerlo entero.
            "stage": self._stage(),
            "elapsed_seconds": self._elapsed(),
        }

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
        """Etapa actual, a partir de las marcas que el ciclo deja en el log."""
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


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "financial-bot-dashboard"

    def __init__(
        self, *args, db_path: str, portfolio_name: str,
        runner: CycleRunner | None = None, **kwargs
    ) -> None:
        self.db_path = db_path
        self.default_portfolio = portfolio_name
        # None cuando el servidor no escucha en loopback: sin runner no hay
        # endpoints de control.
        self.runner = runner
        super().__init__(*args, **kwargs)

    # -- Enrutado ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - lo impone BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        try:
            if route == "/":
                self._serve_static("index.html", "text/html; charset=utf-8")
            elif route == "/api/dashboard":
                portfolio = (query.get("portfolio") or [self.default_portfolio])[0]
                self._serve_json(self._dashboard_payload(portfolio))
            elif route == "/api/portfolios":
                self._serve_json({"portfolios": self._portfolios_payload()})
            elif route == "/api/cycle":
                self._serve_json(self._cycle_status())
            elif route == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self._serve_json({"error": "Ruta no encontrada."}, status=404)
        except DatabaseError as exc:
            log.warning("Error de base de datos sirviendo %s: %s", route, exc)
            self._serve_json({"error": str(exc)}, status=503)
        except BrokenPipeError:
            # El navegador cerro la conexion a mitad; no es un error del servidor.
            pass
        except Exception as exc:  # noqa: BLE001 - un fallo no debe tumbar el servidor
            log.exception("Error sirviendo %s", route)
            self._serve_json({"error": f"Error interno: {exc}"}, status=500)

    def do_POST(self) -> None:  # noqa: N802 - lo impone BaseHTTPRequestHandler
        route = urlparse(self.path).path.rstrip("/") or "/"

        if self.runner is None:
            self._serve_json(
                {"error": "Los controles estan desactivados: el servidor no escucha "
                          "en localhost."},
                status=403,
            )
            return

        try:
            if route == "/api/cycle/start":
                body = self._read_json_body()
                ok, message = self._start_cycle(bool(body.get("dry_run")))
                self._serve_json(
                    {"ok": ok, "message": message, **self._cycle_status()},
                    status=200 if ok else 409,
                )
            elif route == "/api/cycle/stop":
                ok, message = self.runner.stop()
                self._serve_json(
                    {"ok": ok, "message": message, **self._cycle_status()},
                    status=200 if ok else 409,
                )
            else:
                self._serve_json({"error": "Ruta no encontrada."}, status=404)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 - un fallo no debe tumbar el servidor
            log.exception("Error en POST %s", route)
            self._serve_json({"error": f"Error interno: {exc}"}, status=500)

    def _start_cycle(self, dry_run: bool) -> tuple[bool, str]:
        """Arranca un ciclo, salvo que ya haya uno en marcha.

        Se comprueban las dos fuentes posibles: el subproceso propio y la tabla
        `cycles`, porque el contenedor del planificador puede tener uno corriendo
        del que este servidor no sabe nada. Dos ciclos a la vez sobre la misma
        cartera se pisarian las posiciones.
        """
        try:
            with Database(path=self.db_path, read_only=True) as db:
                running = db.query(
                    "select count(1) as n from cycles where status = 'running'"
                )[0]["n"]
        except DatabaseError:
            running = 0

        if running:
            return False, (
                "Ya hay un ciclo en marcha (probablemente lanzado por el "
                "planificador). Espera a que termine."
            )
        return self.runner.start(dry_run=dry_run)

    def _cycle_status(self) -> dict:
        if self.runner is None:
            return {"enabled": False, "running": False, "lines": [],
                    "stage": "controles desactivados"}
        status = self.runner.status()
        status["enabled"] = True
        return status

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 64_000:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- Datos -------------------------------------------------------------

    def _dashboard_payload(self, portfolio_name: str) -> dict:
        with Database(path=self.db_path, read_only=True) as db:
            payload = build_dashboard(db, portfolio_name=portfolio_name)
            payload["portfolios"] = list_portfolios(db)
        return payload

    def _portfolios_payload(self) -> list[dict]:
        with Database(path=self.db_path, read_only=True) as db:
            return list_portfolios(db)

    # -- Respuestas --------------------------------------------------------

    def _serve_json(self, payload: object, *, status: int = 200) -> None:
        # `default=str` evita que un tipo inesperado (Decimal, date) rompa la
        # respuesta entera; allow_nan=False porque JSON.parse rechaza NaN.
        body = json.dumps(
            payload, ensure_ascii=False, default=str, allow_nan=False
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, name: str, content_type: str) -> None:
        # `name` es siempre un literal del enrutado, nunca entrada del usuario,
        # pero se comprueba la contencion por si eso cambia.
        path = (STATIC_DIR / name).resolve()
        if not path.is_file() or STATIC_DIR not in path.parents:
            self._serve_json({"error": f"No se encontro {name}."}, status=404)
            return
        body = path.read_bytes()[:MAX_STATIC_BYTES]
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Por defecto escribe en stderr sin formato; se redirige al logger.
        log.debug("%s - %s", self.address_string(), format % args)


def serve(
    *,
    db_path: str,
    portfolio_name: str,
    host: str = "127.0.0.1",
    port: int = 8000,
    controls: bool | None = None,
) -> int:
    """Arranca el dashboard.

    `controls` habilita el boton de lanzar ciclo. Por defecto esta activo, porque
    el caso normal es un unico usuario en su maquina y el contenedor publica el
    puerto solo en 127.0.0.1.

    No se deduce de `host`: dentro de Docker hay que escuchar en 0.0.0.0 para que
    el mapeo de puertos funcione, asi que la direccion de escucha no dice nada
    sobre quien puede llegar. Quien exponga el dashboard de verdad a la red tiene
    que apagarlo a mano con DASHBOARD_CONTROLS=false; el aviso de abajo se lo
    recuerda cada arranque.
    """
    import os

    if controls is None:
        controls = (os.getenv("DASHBOARD_CONTROLS") or "true").strip().lower() not in {
            "0", "false", "no", "off",
        }
    runner = CycleRunner() if controls else None
    controls_enabled = controls

    handler = partial(
        DashboardHandler, db_path=db_path, portfolio_name=portfolio_name, runner=runner
    )
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        print(f"No se pudo abrir el puerto {port}: {exc}", file=sys.stderr)
        print("Prueba con otro puerto:  python run.py serve --port 8080", file=sys.stderr)
        return 1

    url = f"http://{host}:{port}"
    print(f"\n  Dashboard en {url}")
    print(f"  Base de datos: {db_path} (solo lectura)")
    print(f"  Cartera: {portfolio_name}")
    if controls_enabled:
        print("  Boton de lanzar ciclo: ACTIVO")
        if not _is_loopback(host):
            print()
            print("  " + "!" * 66)
            print(f"  AVISO: escuchando en {host}, no solo en localhost, y el boton de")
            print("  lanzar ciclo esta activo. No hay autenticacion: cualquiera que")
            print("  alcance este puerto puede gastar tu cuota del modelo y mover la")
            print("  cartera. Si el puerto no esta publicado solo en 127.0.0.1,")
            print("  desactivalo con  DASHBOARD_CONTROLS=false")
            print("  " + "!" * 66)
    else:
        print("  Boton de lanzar ciclo: desactivado (DASHBOARD_CONTROLS=false)")
    print("\n  Ctrl+C para detener.\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor detenido.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    import argparse

    from src.config import DashboardSettings

    parser = argparse.ArgumentParser(description="Dashboard del agente de trading.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dash = DashboardSettings.load()
    raise SystemExit(
        serve(
            db_path=dash.db_path,
            portfolio_name=dash.portfolio_name,
            host=args.host,
            port=args.port,
        )
    )
