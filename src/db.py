"""Persistencia en SQLite local.

Un principio gobierna este modulo: **el broker es la fuente de verdad de lo que
se posee, la base de datos es la fuente de verdad de por que se posee.**
Cantidades y precios se leen siempre del broker; tesis, stops y objetivos viven
aqui.

De ahi la reconciliacion al inicio de cada ciclo: si un fallo dejo una orden
ejecutada sin registrar, se detecta comparando ambas fuentes en lugar de confiar
en que la escritura anterior salio bien.

Notas de implementacion:
  * `schema.sql` se ejecuta al abrir la conexion, asi que anadir una tabla alli
    la crea en el siguiente arranque sin migraciones manuales.
  * WAL activado: permite abrir la base con otro cliente (DB Browser, la CLI de
    sqlite3) para consultar mientras el bot escribe.
  * `foreign_keys` hay que activarlo por conexion; SQLite lo trae apagado.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import BrokerPosition, MarketSnapshot, Proposal, RiskVerdict

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


class DatabaseError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Database:
    def __init__(self, *, path: str | Path, read_only: bool = False) -> None:
        """`read_only=True` abre la base sin permiso de escritura.

        Lo usa el dashboard: asi la interfaz web no puede alterar el historico ni
        por un fallo de codigo, en lugar de confiar en que solo hace SELECT.
        """
        self.path = Path(path).expanduser().resolve()
        self.read_only = read_only

        if read_only:
            if not self.path.exists():
                raise DatabaseError(
                    f"La base de datos {self.path} no existe todavia. "
                    "Ejecuta primero: python run.py cycle"
                )
            target: Any = f"file:{self.path.as_posix()}?mode=ro"
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            target = str(self.path)

        try:
            self._conn = sqlite3.connect(
                target,
                uri=read_only,
                timeout=30.0,
                isolation_level=None,      # autocommit; las escrituras son atomicas
                check_same_thread=False,
            )
        except sqlite3.Error as exc:
            raise DatabaseError(f"No se pudo abrir la base de datos {self.path}: {exc}") from exc

        self._conn.row_factory = sqlite3.Row
        self._conn.execute("pragma busy_timeout = 30000")
        if not read_only:
            self._conn.execute("pragma journal_mode = wal")
            self._conn.execute("pragma foreign_keys = on")
            self._apply_schema()
        log.debug("Base de datos abierta en %s (read_only=%s)", self.path, read_only)

    def _apply_schema(self) -> None:
        try:
            sql = SCHEMA_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            raise DatabaseError(f"No se pudo leer {SCHEMA_PATH}: {exc}") from exc
        try:
            self._conn.executescript(sql)
        except sqlite3.Error as exc:
            raise DatabaseError(f"Fallo al aplicar schema.sql: {exc}") from exc

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- Helpers -----------------------------------------------------------

    def _execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        try:
            return self._conn.execute(sql, params)
        except sqlite3.Error as exc:
            raise DatabaseError(f"Fallo la consulta SQL ({exc}). SQL: {sql.strip()[:160]}") from exc

    def _insert(self, table: str, payload: dict[str, Any]) -> sqlite3.Cursor:
        columns = ", ".join(payload)
        placeholders = ", ".join(f":{key}" for key in payload)
        return self._execute(
            f"insert into {table} ({columns}) values ({placeholders})", payload
        )

    def _update(self, table: str, row_id: Any, payload: dict[str, Any]) -> None:
        if not payload:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in payload)
        params = dict(payload)
        params["__id"] = row_id
        self._execute(f"update {table} set {assignments} where id = :__id", params)

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Consulta libre de lectura. La usan el dashboard y el comando report."""
        return [dict(row) for row in self._execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Escritura libre, para herramientas y mantenimiento (`tools/`).

        El ciclo no la usa: pasa por los metodos con nombre, que garantizan que
        cada fila lleva sus campos obligatorios. Devuelve las filas afectadas.
        """
        if self.read_only:
            raise DatabaseError("La base esta abierta en solo lectura.")
        return self._execute(sql, params).rowcount

    # -- Carteras ----------------------------------------------------------

    def ensure_portfolio(self, *, name: str, mode: str, initial_budget: float) -> str:
        """Devuelve el id de la cartera, creandola la primera vez."""
        row = self._execute(
            "select id, mode from portfolios where name = ? limit 1", (name,)
        ).fetchone()

        if row is not None:
            if row["mode"] != mode:
                # Mezclar paper y dinero real en la misma cartera haria
                # incomparables los historicos.
                raise DatabaseError(
                    f"La cartera {name!r} se creo en modo {row['mode']!r} y ahora se "
                    f"pide {mode!r}. Usa un PORTFOLIO_NAME distinto para no mezclar "
                    f"resultados de paper y de dinero real."
                )
            return str(row["id"])

        portfolio_id = _new_id()
        self._insert(
            "portfolios",
            {
                "id": portfolio_id,
                "name": name,
                "mode": mode,
                "initial_budget": round(initial_budget, 2),
                "created_at": _now(),
            },
        )
        log.info("Cartera %r creada (modo %s).", name, mode)
        return portfolio_id

    # -- Ciclos ------------------------------------------------------------

    def start_cycle(
        self,
        *,
        portfolio_id: str,
        equity_start: float,
        cash_start: float,
        market_open: bool,
        symbols: list[str],
        llm_model: str,
    ) -> str:
        cycle_id = _new_id()
        self._insert(
            "cycles",
            {
                "id": cycle_id,
                "portfolio_id": portfolio_id,
                "status": "running",
                "started_at": _now(),
                "equity_start": round(equity_start, 2),
                "cash_start": round(cash_start, 2),
                "market_open": int(market_open),
                "symbols_scanned_json": _dumps(symbols),
                "llm_model": llm_model,
            },
        )
        return cycle_id

    def find_running_cycle(self, portfolio_id: str) -> dict[str, Any] | None:
        """Ciclo en estado 'running' de esta cartera, si lo hay.

        Sirve para impedir que dos ciclos operen a la vez sobre la misma cartera:
        se pisarian las posiciones y el efectivo, y dejarian un historico con
        decisiones duplicadas imposible de interpretar.
        """
        rows = self._execute(
            "select id, started_at, llm_model from cycles "
            "where portfolio_id = ? and status = 'running' "
            "order by started_at asc limit 1",
            (portfolio_id,),
        ).fetchall()
        return dict(rows[0]) if rows else None

    def abandon_cycle(self, cycle_id: str, reason: str) -> None:
        """Marca como fallido un ciclo que quedo colgado en 'running'.

        Un proceso que muere a media ejecucion (contenedor reiniciado, Docker
        caido) deja su fila en 'running' para siempre. Sin esto, ese cadaver
        bloquearia todos los ciclos posteriores.
        """
        self._update(
            "cycles",
            cycle_id,
            {"status": "failed", "finished_at": _now(), "error": reason[:4000]},
        )
        log.warning("Ciclo %s marcado como abandonado: %s", cycle_id, reason)

    def finish_cycle(
        self,
        cycle_id: str,
        *,
        status: str,
        equity_end: float | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"status": status, "finished_at": _now()}
        if equity_end is not None:
            payload["equity_end"] = round(equity_end, 2)
        if error:
            payload["error"] = error[:4000]
        self._update("cycles", cycle_id, payload)

    # -- Snapshots y decisiones -------------------------------------------

    def save_snapshot(self, *, cycle_id: str, snapshot: MarketSnapshot) -> int:
        cursor = self._insert(
            "market_snapshots",
            {
                "cycle_id": cycle_id,
                "symbol": snapshot.symbol,
                "as_of": snapshot.as_of.isoformat(),
                "price": round(snapshot.price, 4),
                "indicators_json": _dumps(snapshot.indicators),
                "created_at": _now(),
            },
        )
        return int(cursor.lastrowid)

    def save_decision(
        self,
        *,
        cycle_id: str,
        portfolio_id: str,
        proposal: Proposal,
        snapshot_id: int | None = None,
    ) -> str:
        decision_id = _new_id()
        self._insert(
            "decisions",
            {
                "id": decision_id,
                "cycle_id": cycle_id,
                "portfolio_id": portfolio_id,
                "snapshot_id": snapshot_id,
                "symbol": proposal.symbol,
                "kind": proposal.kind,
                "action": proposal.action,
                "conviction": proposal.conviction,
                "thesis": proposal.thesis,
                "risks": proposal.risks,
                "horizon_days": proposal.horizon_days,
                "suggested_stop": proposal.suggested_stop,
                "suggested_target": proposal.suggested_target,
                "reference_price": round(proposal.reference_price, 4),
                "llm_model": proposal.model,
                "latency_ms": proposal.latency_ms,
                "prompt_tokens": proposal.prompt_tokens,
                "completion_tokens": proposal.completion_tokens,
                "raw_response_json": _dumps(proposal.raw_response),
                "created_at": _now(),
            },
        )
        return decision_id

    def save_risk_event(
        self,
        *,
        cycle_id: str,
        portfolio_id: str,
        symbol: str | None,
        verdict: RiskVerdict,
        decision_id: str | None = None,
    ) -> str:
        event_id = _new_id()
        self._insert(
            "risk_events",
            {
                "id": event_id,
                "cycle_id": cycle_id,
                "portfolio_id": portfolio_id,
                "decision_id": decision_id,
                "symbol": symbol,
                "verdict": "approved" if verdict.approved else "rejected",
                "rule": verdict.rule,
                "reason": verdict.reason[:2000],
                "approved_qty": verdict.qty or None,
                "approved_notional": verdict.notional or None,
                "stop_price": verdict.stop_price,
                "target_price": verdict.target_price,
                "details_json": _dumps(verdict.details),
                "created_at": _now(),
            },
        )
        return event_id

    # -- Ordenes -----------------------------------------------------------

    def save_order(
        self,
        *,
        cycle_id: str,
        portfolio_id: str,
        symbol: str,
        side: str,
        qty: float,
        status: str,
        decision_id: str | None = None,
        risk_event_id: str | None = None,
        broker_order_id: str | None = None,
        filled_qty: float | None = None,
        filled_avg_price: float | None = None,
        stop_price: float | None = None,
        target_price: float | None = None,
        error: str | None = None,
    ) -> str:
        order_id = _new_id()
        timestamp = _now()
        self._insert(
            "orders",
            {
                "id": order_id,
                "cycle_id": cycle_id,
                "portfolio_id": portfolio_id,
                "decision_id": decision_id,
                "risk_event_id": risk_event_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "order_type": "market",
                "status": status,
                "broker_order_id": broker_order_id or None,
                "filled_qty": filled_qty,
                "filled_avg_price": filled_avg_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "error": error[:2000] if error else None,
                "submitted_at": timestamp,
                "updated_at": timestamp,
            },
        )
        return order_id

    def update_order_fill(
        self,
        order_id: str,
        *,
        status: str,
        filled_qty: float | None = None,
        filled_avg_price: float | None = None,
    ) -> None:
        payload: dict[str, Any] = {"status": status, "updated_at": _now()}
        if filled_qty is not None:
            payload["filled_qty"] = filled_qty
        if filled_avg_price is not None:
            payload["filled_avg_price"] = filled_avg_price
        self._update("orders", order_id, payload)

    # -- Posiciones --------------------------------------------------------

    def get_open_positions(self, portfolio_id: str) -> dict[str, dict[str, Any]]:
        """Posiciones abiertas indexadas por simbolo, con su tesis y niveles."""
        rows = self._execute(
            "select * from positions where portfolio_id = ? and status = 'open'",
            (portfolio_id,),
        ).fetchall()
        return {str(row["symbol"]): dict(row) for row in rows}

    def open_position(
        self,
        *,
        portfolio_id: str,
        symbol: str,
        qty: float,
        entry_price: float,
        stop_price: float | None,
        target_price: float | None,
        thesis: str | None,
        horizon_days: int | None = None,
        entry_order_id: str | None = None,
    ) -> str:
        position_id = _new_id()
        self._insert(
            "positions",
            {
                "id": position_id,
                "portfolio_id": portfolio_id,
                "symbol": symbol,
                "status": "open",
                "qty": qty,
                "entry_price": round(entry_price, 4),
                "stop_price": stop_price,
                "target_price": target_price,
                "thesis": thesis,
                "horizon_days": horizon_days,
                "entry_order_id": entry_order_id,
                "opened_at": _now(),
            },
        )
        return position_id

    def close_position(
        self,
        position_id: str,
        *,
        exit_price: float,
        realized_pnl: float,
        exit_reason: str,
        exit_order_id: str | None = None,
    ) -> None:
        self._update(
            "positions",
            position_id,
            {
                "status": "closed",
                "exit_price": round(exit_price, 4),
                "realized_pnl": round(realized_pnl, 2),
                "exit_reason": exit_reason[:500],
                "exit_order_id": exit_order_id,
                "closed_at": _now(),
            },
        )

    def update_position_levels(
        self,
        position_id: str,
        *,
        stop_price: float | None = None,
        target_price: float | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if stop_price is not None:
            payload["stop_price"] = stop_price
        if target_price is not None:
            payload["target_price"] = target_price
        self._update("positions", position_id, payload)

    def sync_position_from_broker(
        self, position_id: str, *, qty: float, entry_price: float
    ) -> None:
        """Alinea cantidad y precio medio con lo que dice el broker."""
        self._update(
            "positions",
            position_id,
            {"qty": qty, "entry_price": round(entry_price, 4)},
        )

    # -- Curva de capital --------------------------------------------------

    def save_equity_snapshot(
        self,
        *,
        portfolio_id: str,
        cycle_id: str | None,
        equity: float,
        cash: float,
        positions_value: float,
        open_positions: int,
        day_pnl: float,
        day_pnl_pct: float,
    ) -> None:
        self._insert(
            "equity_snapshots",
            {
                "portfolio_id": portfolio_id,
                "cycle_id": cycle_id,
                "as_of": _now(),
                "equity": round(equity, 2),
                "cash": round(cash, 2),
                "positions_value": round(positions_value, 2),
                "open_positions": open_positions,
                "day_pnl": round(day_pnl, 2),
                "day_pnl_pct": round(day_pnl_pct, 4),
            },
        )

    # -- Reconciliacion ----------------------------------------------------

    def reconcile(
        self,
        *,
        portfolio_id: str,
        broker_positions: dict[str, BrokerPosition],
    ) -> ReconcileReport:
        """Alinea el registro con la realidad del broker.

        Tres casos:
          * Registrada abierta pero ausente en el broker -> se cerro fuera del
            bot (a mano, o un cierre cuyo registro fallo). Se marca cerrada.
          * En el broker sin registro abierto -> huerfana. Se registra para que
            vuelva a estar vigilada; el llamante le asigna un stop.
          * En ambos con cantidad o precio distintos -> se copia el dato del
            broker, que es el autoritativo.
        """
        tracked = self.get_open_positions(portfolio_id)
        report = ReconcileReport()

        for symbol, row in tracked.items():
            position = broker_positions.get(symbol)

            if position is None:
                log.warning(
                    "%s figuraba abierta en la base de datos pero no existe en el "
                    "broker; se marca cerrada por reconciliacion.", symbol,
                )
                self.close_position(
                    str(row["id"]),
                    exit_price=float(row.get("entry_price") or 0.0),
                    realized_pnl=0.0,
                    exit_reason="reconciliacion: la posicion no existe en el broker",
                )
                report.closed_missing.append(symbol)
                continue

            tracked_qty = float(row.get("qty") or 0.0)
            tracked_entry = float(row.get("entry_price") or 0.0)
            qty_differs = abs(tracked_qty - position.qty) > 1e-6
            price_differs = abs(tracked_entry - position.avg_entry_price) > 0.005
            if qty_differs or price_differs:
                log.info(
                    "%s: se ajusta el registro al broker (cantidad %g -> %g, "
                    "entrada %.2f -> %.2f).",
                    symbol, tracked_qty, position.qty,
                    tracked_entry, position.avg_entry_price,
                )
                self.sync_position_from_broker(
                    str(row["id"]),
                    qty=position.qty,
                    entry_price=position.avg_entry_price,
                )
                report.resynced.append(symbol)

        for symbol, position in broker_positions.items():
            if symbol in tracked:
                continue
            log.warning(
                "%s existe en el broker sin registro abierto; se adopta y queda sin "
                "stop hasta que se le asigne uno.", symbol,
            )
            position_id = self.open_position(
                portfolio_id=portfolio_id,
                symbol=symbol,
                qty=position.qty,
                entry_price=position.avg_entry_price,
                stop_price=None,
                target_price=None,
                thesis="Posicion huerfana detectada por reconciliacion.",
            )
            report.adopted_orphans.append((symbol, position_id))

        return report


class ReconcileReport:
    def __init__(self) -> None:
        self.closed_missing: list[str] = []
        self.adopted_orphans: list[tuple[str, str]] = []
        self.resynced: list[str] = []

    @property
    def had_discrepancies(self) -> bool:
        return bool(self.closed_missing or self.adopted_orphans or self.resynced)
