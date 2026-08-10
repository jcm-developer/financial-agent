"""Persistence in local SQLite.

One principle governs this module: **the broker is the source of truth for what
is held, the database is the source of truth for why it is held.** Quantities and
prices are always read from the broker; theses, stops and targets live here.

Hence the reconciliation at the start of each cycle: if a failure left an
executed order unrecorded, it is detected by comparing both sources instead of
trusting that the previous write went through.

Implementation notes:
  * `schema.sql` runs when the connection is opened, so adding a table there
    creates it on the next startup with no manual migrations.
  * WAL enabled: it allows opening the database with another client (DB Browser,
    the sqlite3 CLI) to query while the bot writes.
  * `foreign_keys` has to be enabled per connection; SQLite ships it off.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import BrokerPosition, MarketSnapshot, Proposal, RiskVerdict

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"

# Columns added after the schema's first version.
#
# `schema.sql` is idempotent for tables, but `create table if not exists` does
# not add columns to a table that already exists: over a live database, a new
# column in the CREATE TABLE simply does not appear. Those columns go here with a
# definition **identical** to the one in `schema.sql`, and `_add_missing_columns`
# inserts them with ALTER TABLE when they are missing.
#
# On a freshly created database this does nothing: the CREATE TABLE already
# brought them.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "agent_settings": {
        "market": "text not null default 'us' check (market in ('us', 'eu'))",
        "universe_file": "text",
        "screener_min_turnover": "real not null default 20000000",
        "screener_min_price": "real not null default 5",
        "screener_max_volatility_pct": "real not null default 120",
        "lookback_days": "integer not null default 200",
        "skip_when_market_closed": "integer not null default 1",
    },
    "ingest_runs": {
        # Deliberately without a CHECK: SQLite cannot add a constraint with ALTER
        # TABLE, so demanding one here would force recreating the table over a
        # live database. The CHECK is in `schema.sql` for new databases and the
        # only writer is `start_ingest_run`.
        "kind": "text not null default 'tick'",
    },
    "cycles": {
        # F6.9. With `default 0` the cycles predating the column come out as "no
        # calls", which is right: nothing is known about them.
        "analyst_calls": "integer not null default 0",
        "analyst_failures": "integer not null default 0",
    },
    "sim_positions": {
        # The commission paid on opening, which the realized P&L cannot rebuild
        # afterwards. `default 0` is the truth for anything opened before the
        # tariff existed: back then the commission really was zero.
        "entry_commission": "real not null default 0",
    },
    "decisions": {
        # F9.13. Nullable and with no default on purpose: NULL means "the analyst
        # did not ask for a weight", which is exactly what every decision taken
        # before this field existed did. A `default 0` would record them all as
        # having asked for nothing, which is a different claim.
        "suggested_weight_pct": "real",
    },
}

# Columns that changed name after the schema's first version.
#
# **This is not the same problem as `ADDED_COLUMNS`, and treating it as one
# destroys data silently.** Renaming a column in `schema.sql` alone leaves a live
# database with the old column still holding the value; `_add_missing_columns`
# then sees the new name missing and *adds* it, empty and carrying the schema's
# default. Both columns end up there, the code reads the new one, and the
# experiment's setting has quietly reverted to a default nobody chose.
#
# For F8.7 that would have been exactly the failure FE.11 went to the trouble of
# fixing: the European profile's liquidity floor would have jumped from the
# 5.000.000 € it was created with back to the 20 M default, and the symptom is
# not an error — it is 15 of the 89 symbols disappearing from the analysis.
#
# So the rename runs first, with ALTER TABLE ... RENAME COLUMN (SQLite 3.25+),
# and by the time `_add_missing_columns` looks, the new name is already there and
# it does nothing.
RENAMED_COLUMNS: dict[str, dict[str, str]] = {
    "agent_settings": {
        # F8.7: the figure is in the market's currency since D8, so "dollar"
        # lied in every European profile.
        "screener_min_dollar_volume": "screener_min_turnover",
    },
}


class DatabaseError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _text(value: Any) -> str | None:
    """Readable value for the settings history. None is preserved.

    The history is read by eye, so the text is stored and not JSON: what matters
    is seeing "5 -> 8", not '{"v": 8}'.
    """
    return None if value is None else str(value)


class Database:
    def __init__(self, *, path: str | Path, read_only: bool = False) -> None:
        """`read_only=True` opens the database with no write permission.

        The dashboard uses it: that way the web interface cannot alter the
        history even through a coding error, instead of trusting that it only
        does SELECTs.
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
        # The order is not a detail: renaming has to happen before the missing
        # columns are added, or the column that just changed name would be added
        # again under its new name, empty and with the schema's default. See
        # `RENAMED_COLUMNS`.
        self._rename_columns()
        self._add_missing_columns()

    def _rename_columns(self) -> None:
        """Applies the renames of `RENAMED_COLUMNS` over a live database.

        It runs on every startup and normally does nothing: on a freshly created
        database the CREATE TABLE already brought the new name, and on one
        already migrated the old name is gone.

        The three states are told apart on purpose instead of just trying the
        rename, because they mean different things and only one is routine:
        neither name present means the table is not this schema's; both present
        means someone added the new column by hand while the old one still held
        the value, and continuing would pick the empty one.
        """
        for table, renames in RENAMED_COLUMNS.items():
            existing = self._columns(table)
            if not existing:
                continue  # la tabla no existe todavia; el CREATE la traera entera
            for old, new in renames.items():
                if old not in existing:
                    continue  # ya renombrada, o base nueva
                if new in existing:
                    raise DatabaseError(
                        f"{table} tiene a la vez {old} y {new}. La primera es la que "
                        f"lleva el valor bueno y la segunda la que leeria el codigo, "
                        f"asi que seguir usaria un valor que nadie eligio. "
                        f"Copia el valor a mano y borra {old}."
                    )
                self._execute(f"alter table {table} rename column {old} to {new}")
                log.info("Columna %s.%s renombrada a %s.", table, old, new)

    def _add_missing_columns(self) -> None:
        """Adds with ALTER TABLE the columns of `ADDED_COLUMNS` that are missing.

        It runs on every startup and normally does nothing. It is what completes
        the promise that "schema.sql acts as the migration": without it, adding a
        column would work on a new database and fail on the one already running,
        which is the worst possible split.
        """
        for table, columns in ADDED_COLUMNS.items():
            existing = self._columns(table)
            if not existing:
                continue  # la tabla no existe todavia; el CREATE la traera entera
            for column, definition in columns.items():
                if column in existing:
                    continue
                self._execute(f"alter table {table} add column {column} {definition}")
                log.info("Columna %s.%s anadida a una base existente.", table, column)

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
        """Free-form read query. The dashboard and the report command use it."""
        return [dict(row) for row in self._execute(sql, params).fetchall()]

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Free-form write, for tools and maintenance (`tools/`).

        The cycle does not use it: it goes through the named methods, which
        guarantee each row carries its mandatory fields. Returns the affected rows.
        """
        if self.read_only:
            raise DatabaseError("La base esta abierta en solo lectura.")
        return self._execute(sql, params).rowcount

    def _executemany(self, sql: str, rows: list[tuple]) -> int:
        if not rows:
            return 0
        try:
            return self._conn.executemany(sql, rows).rowcount
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Fallo la escritura por lotes ({exc}). SQL: {sql.strip()[:160]}"
            ) from exc

    def _columns(self, table: str) -> set[str]:
        """A table's real columns.

        Used to validate the field names arriving from outside before
        interpolating them into the SQL: the values are parameterised, but column
        names cannot go in a placeholder.
        """
        return {row["name"] for row in self.query(f"pragma table_info({table})")}

    # -- Perfiles / experimentos -------------------------------------------

    def create_profile(
        self, *, name: str, description: str = "", settings: dict[str, Any] | None = None
    ) -> str:
        """Creates a profile with its default settings and its book.

        The three are created together because a profile with no settings or no
        book is unusable, and leaving it half-made would only produce errors
        later and further from their origin.
        """
        name = name.strip()
        if not name:
            raise DatabaseError("El perfil necesita un nombre.")

        existing = self._execute(
            "select id from profiles where name = ? limit 1", (name,)
        ).fetchone()
        if existing is not None:
            raise DatabaseError(f"Ya existe un perfil llamado {name!r}.")

        profile_id = _new_id()
        now = _now()
        self._insert(
            "profiles",
            {
                "id": profile_id,
                "name": name,
                "description": description or None,
                "status": "draft",
                "created_at": now,
                "updated_at": now,
            },
        )
        self._insert("agent_settings", {"profile_id": profile_id, "updated_at": now})

        if settings:
            self.update_settings(profile_id, settings, source="create")

        current = self.get_settings(profile_id)
        # Always paper: the only broker implementation is the simulator.
        self.ensure_portfolio(
            name=name,
            mode="paper",
            initial_budget=float(current["initial_budget"]),
            profile_id=profile_id,
        )
        log.info("Perfil %r creado.", name)
        return profile_id

    def list_profiles(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = (
            "select p.*, pf.id as portfolio_id, pf.initial_budget "
            "from profiles p left join portfolios pf on pf.profile_id = p.id "
        )
        if not include_archived:
            sql += "where p.status != 'archived' "
        sql += "order by p.created_at desc"
        return self.query(sql)

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        rows = self.query(
            "select p.*, pf.id as portfolio_id from profiles p "
            "left join portfolios pf on pf.profile_id = p.id where p.id = ?",
            (profile_id,),
        )
        return rows[0] if rows else None

    def get_profile_by_name(self, name: str) -> dict[str, Any] | None:
        rows = self.query(
            "select p.*, pf.id as portfolio_id from profiles p "
            "left join portfolios pf on pf.profile_id = p.id where p.name = ?",
            (name.strip(),),
        )
        return rows[0] if rows else None

    def update_profile(
        self, profile_id: str, *, name: str | None = None,
        description: str | None = None,
    ) -> list[str]:
        """Renames or redescribes a profile. Returns what changed.

        It exists so the API does not have to build the UPDATE by hand: the fenced
        connection of `api/guard.py` denies free-form SQL on purpose, so every
        write goes through a named method where what it touches can be seen.
        """
        payload: dict[str, Any] = {}
        if name is not None:
            name = name.strip()
            if not name:
                raise DatabaseError("El perfil necesita un nombre.")
            payload["name"] = name
        if description is not None:
            payload["description"] = description or None
        if not payload:
            return []
        payload["updated_at"] = _now()
        self._update("profiles", profile_id, payload)
        return sorted(key for key in payload if key != "updated_at")

    def set_profile_status(self, profile_id: str, status: str) -> None:
        valid = {"draft", "active", "paused", "archived"}
        if status not in valid:
            raise DatabaseError(f"Estado invalido: {status!r}. Validos: {sorted(valid)}.")
        payload: dict[str, Any] = {"status": status, "updated_at": _now()}
        if status == "archived":
            payload["archived_at"] = _now()
        self._update("profiles", profile_id, payload)

    def delete_profile(self, profile_id: str) -> None:
        """Deletes the profile and, in cascade, its book and all of its history.

        The simulated broker's ledger has to be deleted by hand: `sim_accounts.id`
        **is** the portfolio_id, but without a `references`, so `portfolios`'s
        cascade does not reach it. Without this line, deleting a profile left
        behind its cash, its simulated positions and its fills —`sim_positions`
        and `sim_fills` do hang off `sim_accounts`— and the file grew with the
        bookkeeping of experiments that no longer exist.
        """
        self._execute(
            "delete from sim_accounts where id in "
            "(select id from portfolios where profile_id = ?)",
            (profile_id,),
        )
        self._execute("delete from profiles where id = ?", (profile_id,))

    # -- Parametros del agente ---------------------------------------------

    def get_settings(self, profile_id: str) -> dict[str, Any]:
        rows = self.query(
            "select * from agent_settings where profile_id = ?", (profile_id,)
        )
        if not rows:
            raise DatabaseError(f"El perfil {profile_id} no tiene parametros.")
        return rows[0]

    def update_settings(
        self, profile_id: str, changes: dict[str, Any], *, source: str = "api"
    ) -> list[str]:
        """Updates settings and leaves a record of each change.

        Returns the fields that genuinely changed. The ones arriving with the same
        value they already had are not recorded: the history is there to explain a
        change of behaviour, and a row that changes nothing only gets in the way.
        """
        if not changes:
            return []

        allowed = self._columns("agent_settings") - {"profile_id", "updated_at"}
        unknown = set(changes) - allowed
        if unknown:
            raise DatabaseError(
                f"Parametros desconocidos: {', '.join(sorted(unknown))}. "
                f"Validos: {', '.join(sorted(allowed))}."
            )

        current = self.get_settings(profile_id)
        applied: dict[str, Any] = {}
        for field, value in changes.items():
            if current.get(field) != value:
                applied[field] = value

        if not applied:
            return []

        now = _now()
        payload = dict(applied)
        payload["updated_at"] = now
        assignments = ", ".join(f"{field} = :{field}" for field in payload)
        params = dict(payload)
        params["__pid"] = profile_id
        self._execute(
            f"update agent_settings set {assignments} where profile_id = :__pid", params
        )

        self._executemany(
            "insert into agent_settings_history "
            "(profile_id, field, old_value, new_value, source, changed_at) "
            "values (?, ?, ?, ?, ?, ?)",
            [
                (profile_id, field, _text(current.get(field)), _text(value), source, now)
                for field, value in applied.items()
            ],
        )
        log.info(
            "Perfil %s: %d parametro(s) actualizado(s) (%s).",
            profile_id, len(applied), ", ".join(sorted(applied)),
        )
        return sorted(applied)

    def settings_history(
        self, profile_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self.query(
            "select * from agent_settings_history where profile_id = ? "
            "order by changed_at desc, id desc limit ?",
            (profile_id, limit),
        )

    # -- Universo por perfil -----------------------------------------------

    def set_profile_universe(self, profile_id: str, symbols: list[str]) -> None:
        clean = sorted({s.strip().upper() for s in symbols if s.strip()})
        self._execute("delete from profile_universe where profile_id = ?", (profile_id,))
        now = _now()
        self._executemany(
            "insert into profile_universe (profile_id, symbol, added_at) values (?, ?, ?)",
            [(profile_id, symbol, now) for symbol in clean],
        )

    def get_profile_universe(self, profile_id: str) -> list[str]:
        return [
            row["symbol"]
            for row in self.query(
                "select symbol from profile_universe where profile_id = ? order by symbol",
                (profile_id,),
            )
        ]

    def active_universe(self) -> list[str]:
        """Symbols the ingestor must follow every minute.

        It is the union of the active profiles' universes and of the symbols with
        an open position. The second matters: a position does not stop needing a
        price because its symbol drops out of the screener's universe.
        """
        rows = self.query(
            "select symbol from profile_universe u "
            "  join profiles p on p.id = u.profile_id "
            " where p.status = 'active' "
            "union "
            "select symbol from positions where status = 'open'"
        )
        return sorted({row["symbol"] for row in rows})

    def active_universe_by_market(self) -> dict[str, list[str]]:
        """The same as `active_universe`, but split by exchange.

        It is what the ingestor needs now that the market is a parameter of the
        profile: asking for a European symbol at 16:00 CET makes sense and asking
        for it at 22:00 does not, and the answer depends on the symbol, not on the
        process's clock.

        Symbols of open positions whose book has no profile
        (`portfolios.profile_id` is born NULL in books predating F1.4) are
        classified by their Yahoo suffix. It is guesswork, yes; the alternative
        was discarding them, and an open position with no price is worse than one
        classified too generously.
        """
        from . import market_calendar

        por_mercado: dict[str, set[str]] = {}

        for row in self.query(
            "select s.market as market, u.symbol as symbol "
            "  from profile_universe u "
            "  join profiles p on p.id = u.profile_id "
            "  join agent_settings s on s.profile_id = u.profile_id "
            " where p.status = 'active'"
        ):
            por_mercado.setdefault(row["market"], set()).add(row["symbol"])

        for row in self.query(
            "select s.market as market, pos.symbol as symbol "
            "  from positions pos "
            "  join portfolios pf on pf.id = pos.portfolio_id "
            "  left join agent_settings s on s.profile_id = pf.profile_id "
            " where pos.status = 'open'"
        ):
            code = row["market"]
            if not code:
                code = next(
                    (
                        m.code
                        for m in market_calendar.MARKETS.values()
                        if m.symbol_suffixes and m.owns_symbol(row["symbol"])
                    ),
                    market_calendar.DEFAULT_MARKET,
                )
            por_mercado.setdefault(code, set()).add(row["symbol"])

        return {code: sorted(symbols) for code, symbols in sorted(por_mercado.items())}

    # -- Carteras ----------------------------------------------------------

    def ensure_portfolio(
        self, *, name: str, mode: str, initial_budget: float,
        profile_id: str | None = None,
    ) -> str:
        """Returns the book's id, creating it the first time."""
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
                "profile_id": profile_id,
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
        settings: dict[str, Any] | None = None,
    ) -> str:
        """`settings` is the copy of the parameters the cycle runs with.

        Optional in the signature so as not to burden the tests that do not look
        at it, but the real cycle always sends it: it is what allows an old
        decision to be read with the configuration that produced it and not with
        today's (F6.3).
        """
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
                "settings_json": _dumps(settings) if settings is not None else None,
            },
        )
        return cycle_id

    def find_running_cycle(self, portfolio_id: str) -> dict[str, Any] | None:
        """This book's cycle in 'running' state, if there is one.

        It serves to stop two cycles trading at once over the same book: they
        would step on each other's positions and cash, and leave a history with
        duplicated decisions that cannot be interpreted.
        """
        rows = self._execute(
            "select id, started_at, llm_model from cycles "
            "where portfolio_id = ? and status = 'running' "
            "order by started_at asc limit 1",
            (portfolio_id,),
        ).fetchall()
        return dict(rows[0]) if rows else None

    def abandon_cycle(self, cycle_id: str, reason: str) -> None:
        """Marks as failed a cycle left hanging in 'running'.

        A process that dies mid-run (container restarted, Docker down) leaves its
        row in 'running' forever. Without this, that corpse would block every
        later cycle.
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
        analyst_calls: int | None = None,
        analyst_failures: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"status": status, "finished_at": _now()}
        if equity_end is not None:
            payload["equity_end"] = round(equity_end, 2)
        if error:
            payload["error"] = error[:4000]
        # They are written even when they are 0: "it was asked 20 times and
        # answered every one" is information, and telling it apart from "nothing
        # is known" is precisely the point of F6.9.
        if analyst_calls is not None:
            payload["analyst_calls"] = int(analyst_calls)
        if analyst_failures is not None:
            payload["analyst_failures"] = int(analyst_failures)
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
                "suggested_weight_pct": proposal.suggested_weight_pct,
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
        """Open positions indexed by symbol, with their thesis and levels."""
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
        """Aligns quantity and average price with what the broker says."""
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

    # -- Datos de mercado en vivo (ingestor) -------------------------------

    def upsert_quotes(self, quotes: list[dict[str, Any]]) -> int:
        """Last price of each symbol. One row per symbol, replaced."""
        now = _now()
        return self._executemany(
            "insert or replace into quotes_live "
            "(symbol, price, prev_close, change_pct, volume, as_of, updated_at) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    q["symbol"], float(q["price"]),
                    q.get("prev_close"), q.get("change_pct"), q.get("volume"),
                    q["as_of"], now,
                )
                for q in quotes
            ],
        )

    def latest_quotes(self, symbols: list[str] | None = None) -> dict[str, dict[str, Any]]:
        if symbols:
            marks = ", ".join("?" for _ in symbols)
            rows = self.query(
                f"select * from quotes_live where symbol in ({marks})", tuple(symbols)
            )
        else:
            rows = self.query("select * from quotes_live")
        return {row["symbol"]: row for row in rows}

    def upsert_bars_1m(self, bars: list[dict[str, Any]]) -> int:
        """One-minute bars.

        `insert or replace` and not `insert or ignore`: the current minute's bar
        changes while the market is still open, so it has to be overwritable.
        """
        return self._executemany(
            "insert or replace into bars_1m "
            "(symbol, ts, open, high, low, close, volume) values (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    b["symbol"], b["ts"], float(b["open"]), float(b["high"]),
                    float(b["low"]), float(b["close"]), float(b.get("volume") or 0.0),
                )
                for b in bars
            ],
        )

    def bars_1m_timestamps(self, symbol: str, *, since: str) -> set[str]:
        """Timestamps already stored for a symbol since `since`.

        It exists for the gap backfill (F2.10): the provider returns whole days
        and without this they would all have to be rewritten. With the
        `bars_1m (symbol, ts desc)` index it is a few thousand rows per symbol,
        and it is asked for one at a time on purpose: an `in (...)` of 89 symbols
        over 7 days would bring half a million rows into memory at once.
        """
        return {
            row["ts"]
            for row in self.query(
                "select ts from bars_1m where symbol = ? and ts >= ?", (symbol, since)
            )
        }

    def start_ingest_run(self, *, symbols_requested: int, kind: str = "tick") -> int:
        cursor = self._insert(
            "ingest_runs",
            {
                "started_at": _now(),
                "symbols_requested": symbols_requested,
                "kind": kind,
            },
        )
        return int(cursor.lastrowid or 0)

    def finish_ingest_run(
        self, run_id: int, *, symbols_ok: int, symbols_failed: int,
        latency_ms: int, rate_limited: bool = False, error: str | None = None,
    ) -> None:
        self._execute(
            "update ingest_runs set finished_at = ?, symbols_ok = ?, symbols_failed = ?, "
            "latency_ms = ?, rate_limited = ?, error = ? where id = ?",
            (_now(), symbols_ok, symbols_failed, latency_ms,
             int(rate_limited), error, run_id),
        )

    def ingest_health(
        self, *, limit: int = 60, kind: str | None = None
    ) -> list[dict[str, Any]]:
        """The ingestor's latest runs, to paint the state in the interface.

        `kind='tick'` leaves the gap backfills out (F2.10). They are worth
        filtering when looking at latencies: a backfill downloads several days at
        once, so a single one of its rows shifts any one-minute average. By
        default it does not filter, so nothing goes invisible by oversight.
        """
        if kind is None:
            return self.query(
                "select * from ingest_runs order by started_at desc limit ?", (limit,)
            )
        return self.query(
            "select * from ingest_runs where kind = ? order by started_at desc limit ?",
            (kind, limit),
        )

    def prune_bars_1m(self, *, keep_days: int) -> int:
        """Deletes one-minute bars older than `keep_days`.

        There is no daily consolidation because none is needed: the daily bars are
        already maintained by `bar_cache`, which is where the agent computes
        indicators from. This only stops the file growing without end.
        """
        if keep_days < 1:
            raise DatabaseError("keep_days debe ser al menos 1.")
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=keep_days)
        ).isoformat()
        return self._execute("delete from bars_1m where ts < ?", (cutoff,)).rowcount

    def reconcile(
        self,
        *,
        portfolio_id: str,
        broker_positions: dict[str, BrokerPosition],
    ) -> ReconcileReport:
        """Aligns the record with the broker's reality.

        Three cases:
          * Recorded open but absent at the broker -> it was closed outside the
            bot (by hand, or a close whose record failed). It is marked closed.
          * At the broker with no open record -> orphan. It is recorded so it goes
            back under watch; the caller assigns it a stop.
          * In both with a different quantity or price -> the broker's datum is
            copied, being the authoritative one.
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
