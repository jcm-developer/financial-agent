"""La conexion de escritura de la API, limitada a las tablas de configuracion.

Este modulo existe para pagar la deuda que D5 dejo apuntada. Hasta F3, el
dashboard abria SQLite en **solo lectura**, y eso no era una promesa: era una
imposibilidad. La interfaz no podia corromper el historico ni con un error de
programacion, porque el motor no se lo permitia.

Con perfiles editables desde la UI (F3.3) esa puerta hay que abrirla. La
tentacion es abrirla del todo y confiar en que los endpoints solo hagan lo que
deben; el problema de esa version es que la garantia pasa a depender de que
nadie escriba nunca un `UPDATE` de mas, y eso no es una garantia, es una
costumbre. Un `where` mal puesto en un endpoint de configuracion podria borrar
posiciones, y el sintoma —un historico corrupto— aparece semanas despues,
cuando ya no hay forma de saber que lo hizo.

Asi que la puerta se abre solo para las tablas que la interfaz tiene que
escribir, y quien lo impide vuelve a ser el motor: SQLite tiene un
**autorizador** (`sqlite3.Connection.set_authorizer`) que se consulta al
compilar cada sentencia. Un `insert into decisions` no falla por convencion,
falla con "not authorized" antes de ejecutarse.

Dos detalles que costaron pensarlo:

  * **El autorizador tambien se dispara en los borrados en cascada.** Comprobado:
    al borrar un perfil, SQLite pide permiso para cada `delete` que la cascada
    provoca en `cycles`, `decisions`, `positions`… Con la lista de tablas a
    secas, `DELETE /api/profiles` fallaria. De ahi `_cascading`: una ventana que
    abre **un solo metodo** (`delete_profile`) para **una sola sentencia**, y que
    se cierra en un `finally`. Borrar un perfil arrastra su historico a
    proposito; lo que no se admite es llegar a ese historico por cualquier otra
    via.

  * **`portfolios` se puede insertar y borrar, pero no actualizar.** Crear un
    perfil crea su cartera y borrarlo la borra, pero nada tiene por que
    *modificarla*: la unica columna interesante es `initial_budget`, y cambiarla
    con la curva de capital ya empezada reescribiria en silencio la referencia
    contra la que se mide todo el experimento.

Lo que este modulo **no** es: seguridad frente a un atacante. La API escucha en
loopback y sin autenticacion (F3.8); quien llegue al proceso llega al fichero.
Es seguridad frente a nuestros propios errores, que es de lo que trata R5.
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
    """Se ha intentado escribir en una tabla que la API no puede tocar."""


#: Que puede hacer la API en cada tabla. Lo que no aparece aqui es de solo
#: lectura para ella: `cycles`, `decisions`, `orders`, `positions`,
#: `risk_events`, `equity_snapshots`, `market_snapshots`, las tablas del broker
#: simulado, `bars_1m`, `quotes_live`, `bar_cache` e `ingest_runs`.
#:
#: Esas las escriben el ciclo y el ingestor, cada uno en su proceso y con su
#: propia conexion sin autorizador. La API nunca es quien opera.
WRITABLE: MappingProxyType[str, frozenset[str]] = MappingProxyType({
    "profiles": frozenset({"insert", "update", "delete"}),
    "agent_settings": frozenset({"insert", "update", "delete"}),
    # El historial de parametros se añade y se borra en cascada, nunca se
    # reescribe: una fila que se pudiera editar dejaria de ser un historial.
    "agent_settings_history": frozenset({"insert", "delete"}),
    # El universo de un perfil se reemplaza entero (`set_profile_universe`
    # borra y vuelve a insertar), asi que no hace falta update.
    "profile_universe": frozenset({"insert", "delete"}),
    # Ver la cabecera: se crea con el perfil y se borra con el perfil.
    "portfolios": frozenset({"insert", "delete"}),
})

#: Pragmas que la capa de datos necesita. `table_info` lo usa
#: `Database._columns` para validar los nombres de campo que llegan de fuera
#: antes de interpolarlos en el SQL, asi que sin el no hay `update_settings`.
#: El resto los fija `Database.__init__`. Es lista blanca y no negra porque
#: `pragma writable_schema = on` deja sin efecto todo lo demas de este modulo.
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

#: Acciones que no escriben nada y por tanto no hace falta filtrar. Leer el
#: historico entero esta permitido: lo que se acota es cambiarlo.
_HARMLESS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
    sqlite3.SQLITE_RECURSIVE,
})


class ConfigDatabase(Database):
    """`Database` que solo puede escribir en las tablas de `WRITABLE`.

    Se usa exactamente igual que la de siempre —hereda todos sus metodos— pero
    cualquier escritura fuera de la lista muere con `HistoryIsReadOnly` antes de
    tocar el fichero.
    """

    def __init__(self, *, path: str) -> None:
        # Los dos atributos van **antes** de `super().__init__`: al aplicar el
        # esquema, la clase base llama a `_execute`, que aqui esta sobrescrito y
        # los consulta si algo falla. Sin esto, un fallo al abrir la base daria
        # un AttributeError en lugar del error de verdad.
        self._cascading = False
        self._last_denial: tuple[str, str] | None = None
        # El esquema se aplica en `super().__init__`, o sea antes de instalar el
        # autorizador. Tiene que ser asi: `schema.sql` crea tablas y vistas, y
        # con el autorizador puesto no podria. Es una ventana de un solo uso, al
        # abrir la conexion, con SQL que sale del repositorio y no de nadie mas.
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
            # CREATE, DROP, ALTER, ATTACH, REINDEX... La API no hace nada de eso:
            # el esquema lo gobierna `schema.sql` y lo aplica quien abre la base.
            return self._deny("esquema", arg1 or f"accion {action}")

        table = arg1 or ""
        if verb in WRITABLE.get(table, frozenset()):
            return sqlite3.SQLITE_OK
        return self._deny(verb, table)

    def _deny(self, verb: str, target: str) -> int:
        # Se recuerda para poder dar un mensaje util: SQLite solo dice "not
        # authorized", sin decir a que tabla ni con que verbo.
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
        """Deja pasar el borrado en cascada de un perfil.

        Es privado y lo abre un solo metodo. El `finally` no es adorno: si una
        excepcion dejara la ventana abierta, la conexion seguiria sirviendo
        peticiones sin ninguna restriccion durante el resto de su vida.
        """
        self._cascading = True
        try:
            yield
        finally:
            self._cascading = False

    def delete_profile(self, profile_id: str) -> None:
        """Borra el perfil y, con el, su cartera y todo su historico.

        Es destructivo a proposito y es el unico camino por el que la API llega
        al historico. Quien lo llama tiene que haber confirmado antes: la API
        exige que el cuerpo repita el nombre del perfil (F5.4).
        """
        with self._cascade():
            super().delete_profile(profile_id)
        log.info("Perfil %s borrado desde la API, con su historico.", profile_id)

    # -- Puertas que no se usan -------------------------------------------

    def execute(self, sql: str, params: tuple = ()) -> int:
        """SQL libre: no, ni siquiera acotado por el autorizador.

        `Database.execute` esta para las herramientas de `tools/`. Que la API
        pudiera construir SQL a mano convertiria el autorizador en la unica
        barrera; con esto, para tocar la base hay que pasar por un metodo con
        nombre, que es donde se ve lo que hace.
        """
        raise HistoryIsReadOnly(
            "La API no ejecuta SQL libre. Usa los metodos con nombre de Database."
        )


def open_config_db(path: str) -> ConfigDatabase:
    """Abre la conexion de escritura acotada. Falla igual que `Database`."""
    return ConfigDatabase(path=path)


def history_tables(db: Database) -> list[str]:
    """Tablas reales que la API **no** puede escribir.

    La usa el test de F3.3 para no depender de una lista escrita a mano: si
    manana alguien añade una tabla al esquema, entra sola en la comprobacion.
    """
    rows = db.query(
        "select name from sqlite_master where type = 'table' "
        "and name not like 'sqlite_%' order by name"
    )
    return [row["name"] for row in rows if row["name"] not in WRITABLE]
