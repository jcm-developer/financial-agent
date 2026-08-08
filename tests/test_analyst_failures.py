"""F6.9: un ciclo sin modelo no se puede parecer a un dia tranquilo.

`Analyst` se traga los `LLMError` a proposito —un 429 en un simbolo no debe
tumbar el ciclo entero— y hasta F6.9 eso tenia un efecto secundario caro: con la
cuota agotada, las 33 llamadas fallaban seguidas y el ciclo terminaba en
'completed' con cero propuestas, exactamente igual que una sesion en la que el
modelo no vio ninguna oportunidad. En un experimento de dos semanas eso son diez
sesiones perdidas sin que el historico lo diga.

Lo que se fija aqui es la distincion: cuantas veces se pregunto, cuantas se
quedaron sin respuesta, y cuando eso degrada el estado del ciclo.
"""

from __future__ import annotations

from helpers import (
    BUY,
    HOLD_EXIT,
    WATCHLIST,
    StubLLM,
    StubMarketData,
    make_cycle,
    make_settings,
    rising,
)
from src.config import RiskLimits
from src.db import Database
from src.llm import LLMError


class BrokenLLM:
    """Falla como falla la cuota agotada: en todas las llamadas.

    No hereda de `StubLLM` para que quede claro que no responde nunca; el
    contador de llamadas se conserva porque es lo que se compara.
    """

    def __init__(self, *, fail_after: int = 0) -> None:
        #: Cuantas llamadas responde antes de empezar a fallar. 0 = ninguna.
        self.fail_after = fail_after
        self.calls: list[str] = []
        self._ok = StubLLM(entry=BUY, exit_=HOLD_EXIT)

    def complete_json(self, *, system: str, user: str, max_tokens: int = 1600):
        self.calls.append(system[:20])
        if len(self.calls) > self.fail_after:
            raise LLMError("429 Too Many Requests (simulado)")
        return self._ok.complete_json(system=system, user=user, max_tokens=max_tokens)


def _cycle_row(db: Database) -> dict:
    return db.query("select * from cycles order by started_at desc limit 1")[0]


# ----------------------------------------------------------------------
# Fallo total: el caso que motiva la tarea
# ----------------------------------------------------------------------

def test_a_cycle_where_every_call_fails_is_not_recorded_as_completed(db):
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, BrokenLLM(), market).run()

    # Lo que pasaba antes de F6.9: 'completed' y cero propuestas.
    assert report.status == "failed"
    assert report.proposals_buy == 0
    assert report.analyst_calls == 2
    assert report.analyst_failures == 2


def test_the_total_failure_is_visible_in_the_history_not_just_in_the_log(db):
    """El log se pierde; la fila es lo que se mira dos semanas despues."""
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    make_cycle(db, settings, BrokenLLM(), market).run()

    row = _cycle_row(db)
    assert row["status"] == "failed"
    assert row["analyst_calls"] == 2
    assert row["analyst_failures"] == 2
    assert "no ha analizado nada" in (row["error"] or "")


def test_the_summary_names_the_failures(db):
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, BrokenLLM(), market).run()

    assert "2 de 2 llamadas sin respuesta" in report.summary()


def test_a_healthy_cycle_says_nothing_about_the_analyst(db):
    """Un aviso que sale siempre acaba sin leerse."""
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    assert "sin respuesta" not in report.summary()


# ----------------------------------------------------------------------
# Fallo parcial: el ciclo sigue valiendo
# ----------------------------------------------------------------------

def test_a_partial_failure_keeps_the_cycle_valid(db):
    """Con 1 fallo de 2 el ciclo si analizo y si pudo operar. Marcarlo 'failed'
    mentiria en la otra direccion: pareceria que no se opero nada."""
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, BrokenLLM(fail_after=1), market).run()

    assert report.status == "completed"
    assert report.analyst_calls == 2
    assert report.analyst_failures == 1
    # Y el que si se analizo llego hasta la orden.
    assert report.orders_submitted == 1


def test_a_partial_failure_still_leaves_a_note_in_the_row(db):
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    make_cycle(db, settings, BrokenLLM(fail_after=1), market).run()

    row = _cycle_row(db)
    assert row["status"] == "completed"
    assert row["analyst_failures"] == 1
    assert "1 de 2" in (row["error"] or "")


# ----------------------------------------------------------------------
# Cuando NO hay que degradar
# ----------------------------------------------------------------------

def test_counters_are_written_even_when_nothing_failed(db):
    """0 fallos de 20 llamadas es informacion; distinguirlo de "no se sabe" es
    el objetivo de la tarea, asi que el 0 se escribe."""
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    row = _cycle_row(db)
    assert row["status"] == "completed"
    assert row["analyst_calls"] == 2
    assert row["analyst_failures"] == 0


def test_the_kill_switch_keeps_the_headline_of_its_cycle(db):
    """Un ciclo detenido por perdida diaria no evalua entradas por definicion,
    asi que sus pocas llamadas no son representativas: convertir 'halted' en
    'failed' taparia el motivo de verdad."""
    settings = make_settings(
        watchlist=("AAPL",),
        risk=RiskLimits(min_conviction=65, max_daily_loss_pct=3.0),
    )
    closes = rising()
    make_cycle(
        db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), StubMarketData({"AAPL": closes})
    ).run()

    db.execute("update sim_accounts set last_equity = 20000")

    report = make_cycle(db, settings, BrokenLLM(), StubMarketData({"AAPL": closes})).run()

    assert report.status == "halted"
    assert report.analyst_failures == report.analyst_calls
    assert report.analyst_failures > 0


def test_a_cycle_that_asked_nothing_is_not_a_failure(db):
    """Sin candidatos no hay llamadas, y 0 de 0 no es un fallo. Sin esta
    distincion, un dia en que el screener no selecciona nada se marcaria como
    ciclo roto."""
    settings = make_settings(watchlist=())
    report = make_cycle(db, settings, BrokenLLM(), StubMarketData({})).run()

    assert report.analyst_calls == 0
    assert report.analyst_failures == 0
    assert report.status == "completed"


# ----------------------------------------------------------------------
# Migracion
# ----------------------------------------------------------------------

def test_the_columns_reach_a_database_created_before_them(tmp_path):
    """`create table if not exists` no anade columnas a una tabla que ya existe.
    Sin `ADDED_COLUMNS`, F6.9 funcionaria en una base nueva y fallaria justo en
    la que lleva el experimento en marcha."""
    path = tmp_path / "vieja.db"
    with Database(path=path) as database:
        database.execute("alter table cycles drop column analyst_calls")
        database.execute("alter table cycles drop column analyst_failures")
        columnas = {c["name"] for c in database.query("pragma table_info(cycles)")}
        assert "analyst_calls" not in columnas

    # Al reabrir, `_add_missing_columns` las repone.
    with Database(path=path) as database:
        columnas = {c["name"] for c in database.query("pragma table_info(cycles)")}
        assert {"analyst_calls", "analyst_failures"} <= columnas
