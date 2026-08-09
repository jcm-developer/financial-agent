"""The API's write connection, fenced to the configuration tables.

This module exists to pay the debt D5 wrote down. Until F3, the dashboard opened
SQLite **read-only**, and that was not a promise: it was an impossibility. The
interface could not corrupt the history even through a programming error, because
the engine would not allow it.

With profiles editable from the UI (F3.3) that door has to be opened. The
temptation is to open it fully and trust the endpoints to do only what they
should; the problem with that version is that the guarantee comes to depend on
nobody ever writing one `UPDATE` too many, and that is not a guarantee, it is a
habit. A misplaced `where` in a configuration endpoint could delete positions,
and the symptom —a corrupt history— shows up weeks later, when there is no way
left to know what did it.

So the door is opened only for the tables the interface has to write, and what
stops it is again the engine: SQLite has an **authorizer**
(`sqlite3.Connection.set_authorizer`) that is consulted while each statement is
compiled. An `insert into decisions` does not fail by convention, it fails with
"not authorized" before it runs.

Two details that took some thinking:

  * **The authorizer also fires on cascading deletes.** Verified: when deleting a
    profile, SQLite asks permission for every `delete` the cascade causes in
    `cycles`, `decisions`, `positions`… With the plain table list,
    `DELETE /api/profiles` would fail. Hence `_cascading`: a window opened by
    **one single method** (`delete_profile`) for **one single statement**, and
    closed in a `finally`. Deleting a profile drags its history along on purpose;
    what is not admitted is reaching that history by any other route.

  * **`portfolios` can be inserted and deleted, but not updated.** Creating a
    profile creates its book and deleting it deletes the book, but nothing has
    any business *modifying* it: the only interesting column is
    `initial_budget`, and changing it once the equity curve has started would
    silently rewrite the reference the whole experiment is measured against.

What this module is **not**: security against an attacker. The API listens on
loopback and without authentication (F3.8); whoever reaches the process reaches
the file. It is security against our own mistakes, which is what R5 is about.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from types import MappingProxyType

from src.db import Database, DatabaseError

log = logging.getLogger(__name__)


class HistoryIsReadOnly(DatabaseError):
    """An attempt was made to write to a table the API cannot touch."""


#: What the API may do to each table. Anything not listed here is read-only for
#: it: `cycles`, `decisions`, `orders`, `positions`, `risk_events`,
#: `equity_snapshots`, `market_snapshots`, the simulated broker's tables,
#: `bars_1m`, `quotes_live`, `bar_cache` and `ingest_runs`.
#:
#: Those are written by the cycle and the ingestor, each in its own process and
#: with its own connection without an authorizer. The API is never the one trading.
WRITABLE: MappingProxyType[str, frozenset[str]] = MappingProxyType({
    "profiles": frozenset({"insert", "update", "delete"}),
    "agent_settings": frozenset({"insert", "update", "delete"}),
    # The settings history is appended to and cascade-deleted, never rewritten: a
    # row that could be edited would stop being a history.
    "agent_settings_history": frozenset({"insert", "delete"}),
    # A profile's universe is replaced wholesale (`set_profile_universe` deletes
    # and re-inserts), so no update is needed.
    "profile_universe": frozenset({"insert", "delete"}),
    # See the header: created with the profile and deleted with the profile.
    "portfolios": frozenset({"insert", "delete"}),
})

#: Pragmas the data layer needs. `table_info` is used by `Database._columns` to
#: validate the field names arriving from outside before interpolating them into
#: the SQL, so without it there is no `update_settings`. The rest are set by
#: `Database.__init__`. It is an allow list and not a deny list because
#: `pragma writable_schema = on` would void everything else in this module.
ALLOWED_PRAGMAS = frozenset({
    "table_info", "table_xinfo", "busy_timeout", "foreign_keys",
    "journal_mode", "database_list", "index_list", "index_info",
    "foreign_key_list",
})

_ACTION_VERBS = {
    sqlite3.SQLITE_INSERT: "insert",
    sqlite3.SQLITE_UPDATE: "update",
    sqlite3.SQLITE_DELETE: "delete",
}

#: Actions that write nothing and therefore need no filtering. Reading the whole
#: history is allowed: what is fenced is changing it.
_HARMLESS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
    sqlite3.SQLITE_RECURSIVE,
})


class ConfigDatabase(Database):
    """A `Database` that can only write to the tables in `WRITABLE`.

    It is used exactly like the usual one —it inherits every method— but any
    write outside the list dies with `HistoryIsReadOnly` before touching the
    file.
    """

    def __init__(self, *, path: str) -> None:
        # Both attributes go **before** `super().__init__`: while applying the
        # schema the base class calls `_execute`, which is overridden here and
        # consults them if something fails. Without this, a failure to open the
        # database would give an AttributeError instead of the real error.
        self._cascading = False
        self._last_denial: tuple[str, str] | None = None
        # The schema is applied in `super().__init__`, that is, before the
        # authorizer is installed. It has to be that way: `schema.sql` creates
        # tables and views, and it could not with the authorizer in place. It is a
        # single-use window, on opening the connection, with SQL that comes from
        # the repository and from nobody else.
        super().__init__(path=path, read_only=False)
        self._conn.set_authorizer(self._authorize)

    # -- El autorizador ----------------------------------------------------

    def _authorize(
        self, action: int, arg1: str | None, arg2: str | None,
        db_name: str | None, trigger: str | None,
    ) -> int:
        if self._cascading:
            return sqlite3.SQLITE_OK
        if action in _HARMLESS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA:
            return (
                sqlite3.SQLITE_OK if (arg1 or "") in ALLOWED_PRAGMAS
                else self._deny("pragma", arg1 or "?")
            )

        verb = _ACTION_VERBS.get(action)
        if verb is None:
            # CREATE, DROP, ALTER, ATTACH, REINDEX... The API does none of that:
            # the schema is governed by `schema.sql` and applied by whoever opens
            # the database.
            return self._deny("esquema", arg1 or f"accion {action}")

        table = arg1 or ""
        if verb in WRITABLE.get(table, frozenset()):
            return sqlite3.SQLITE_OK
        return self._deny(verb, table)

    def _deny(self, verb: str, target: str) -> int:
        # Remembered so a useful message can be given: SQLite only says "not
        # authorized", without saying which table or with which verb.
        self._last_denial = (verb, target)
        return sqlite3.SQLITE_DENY

    # -- Traduccion del fallo ---------------------------------------------

    def _explain(self, error: DatabaseError) -> DatabaseError:
        denial, self._last_denial = self._last_denial, None
        if denial is None:
            return error
        verb, target = denial
        if verb == "esquema":
            return HistoryIsReadOnly(
                f"La API no puede alterar el esquema ({target}). Las tablas y las "
                "vistas las gobierna schema.sql."
            )
        if verb == "pragma":
            return HistoryIsReadOnly(f"La API no puede ejecutar 'pragma {target}'.")
        return HistoryIsReadOnly(
            f"La API no puede hacer {verb.upper()} en {target!r}: solo escribe en "
            f"las tablas de configuracion ({', '.join(sorted(WRITABLE))}).\n"
            "  El historico de operaciones lo escriben el ciclo y el ingestor, "
            "cada uno en su proceso."
        )

    def _execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        try:
            return super()._execute(sql, params)
        except DatabaseError as exc:
            raise self._explain(exc) from exc

    def _executemany(self, sql: str, rows: list[tuple]) -> int:
        try:
            return super()._executemany(sql, rows)
        except DatabaseError as exc:
            raise self._explain(exc) from exc

    # -- La unica excepcion ------------------------------------------------

    @contextmanager
    def _cascade(self) -> Iterator[None]:
        """Lets a profile's cascading delete through.

        It is private and one single method opens it. The `finally` is not
        decoration: if an exception left the window open, the connection would go
        on serving requests with no restriction at all for the rest of its life.
        """
        self._cascading = True
        try:
            yield
        finally:
            self._cascading = False

    def delete_profile(self, profile_id: str) -> None:
        """Deletes the profile and, with it, its book and all of its history.

        It is destructive on purpose and it is the only path by which the API
        reaches the history. The caller must have confirmed beforehand: the API
        demands that the body repeat the profile's name (F5.4).
        """
        with self._cascade():
            super().delete_profile(profile_id)
        log.info("Perfil %s borrado desde la API, con su historico.", profile_id)

    # -- Puertas que no se usan -------------------------------------------

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Free-form SQL: no, not even fenced by the authorizer.

        `Database.execute` is there for the tools in `tools/`. Letting the API
        build SQL by hand would make the authorizer the only barrier; with this,
        touching the database means going through a named method, which is where
        what it does can be seen.
        """
        raise HistoryIsReadOnly(
            "La API no ejecuta SQL libre. Usa los metodos con nombre de Database."
        )


def open_config_db(path: str) -> ConfigDatabase:
    """Opens the fenced write connection. It fails just like `Database`."""
    return ConfigDatabase(path=path)


def history_tables(db: Database) -> list[str]:
    """Real tables the API **cannot** write.

    The F3.3 test uses it so as not to depend on a hand-written list: if someone
    adds a table to the schema tomorrow, it enters the check on its own.
    """
    rows = db.query(
        "select name from sqlite_master where type = 'table' "
        "and name not like 'sqlite_%' order by name"
    )
    return [row["name"] for row in rows if row["name"] not in WRITABLE]
