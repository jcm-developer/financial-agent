"""Minute-by-minute price ingestion.

It is tested against a fake provider, without network: what matters to check is
not that yfinance works, but that the writing is idempotent, that a failure does
not take the loop down and that a gap recovers by itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.indicators import Bar
from src.ingest import (
    BACKFILL_DIAS_MAX,
    IngestError,
    _bars_to_write,
    _es_rate_limit,
    backfill_gaps,
    ingest_once,
    load_last_timestamps,
)

BASE = datetime(2026, 8, 7, 13, 30, tzinfo=timezone.utc)


def barras(n: int, *, desde: datetime = BASE, precio: float = 100.0) -> list[Bar]:
    return [
        Bar(
            timestamp=desde + timedelta(minutes=i),
            open=precio + i, high=precio + i + 1, low=precio + i - 1,
            close=precio + i + 0.5, volume=1000 + i,
        )
        for i in range(n)
    ]


class ProveedorFalso:
    """Returns whatever it is told to. It can fail on demand."""

    def __init__(self, datos: dict[str, list[Bar]] | None = None, error: Exception | None = None):
        self.datos = datos or {}
        self.error = error
        self.llamadas: list[list[str]] = []
        self.dias_pedidos: list[int] = []

    def fetch(self, symbols, *, days=1):
        self.llamadas.append(list(symbols))
        self.dias_pedidos.append(days)
        if self.error:
            raise self.error
        return {s: b for s, b in self.datos.items() if s in symbols}


@pytest.fixture
def perfil(db):
    pid = db.create_profile(name="p")
    db.set_profile_universe(pid, ["AAPL"])
    db.set_profile_status(pid, "active")
    return pid


# -- Seleccion de barras a escribir -----------------------------------------


def test_with_no_history_everything_is_written():
    bars = barras(5)
    assert _bars_to_write(bars, None) == bars


def test_the_last_known_bar_is_rewritten():
    """The current minute's bar keeps changing: if only the later ones were
    written, its close would stay frozen at the first value seen."""
    bars = barras(5)
    ultima = bars[2].timestamp.isoformat()

    pendientes = _bars_to_write(bars, ultima)

    assert pendientes[0].timestamp == bars[2].timestamp, "debe incluir la ya conocida"
    assert len(pendientes) == 3


def test_with_no_new_bars_a_short_overlap_is_refreshed():
    bars = barras(10)
    futuro = (bars[-1].timestamp + timedelta(minutes=5)).isoformat()

    pendientes = _bars_to_write(bars, futuro)

    assert len(pendientes) == 3, "solape corto, no la sesion entera"
    assert pendientes[-1] is bars[-1]


# -- Deteccion de rate limit -------------------------------------------------


@pytest.mark.parametrize("mensaje", [
    "429 Too Many Requests", "Rate limit exceeded", "HTTP Error 429",
])
def test_the_rate_limit_is_recognised(mensaje):
    assert _es_rate_limit(RuntimeError(mensaje))


def test_an_ordinary_error_is_not_a_rate_limit():
    assert not _es_rate_limit(RuntimeError("connection reset by peer"))


# -- Tick completo -----------------------------------------------------------


def test_a_tick_writes_bars_and_a_quote(db, perfil):
    proveedor = ProveedorFalso({"AAPL": barras(3)})

    resultado = ingest_once(db, proveedor, ["AAPL"])

    assert resultado.ok
    assert resultado.con_datos == 1
    assert resultado.barras_escritas == 3
    assert db.query("select count(1) n from bars_1m")[0]["n"] == 3

    cotizacion = db.latest_quotes()["AAPL"]
    assert cotizacion["price"] == barras(3)[-1].close
    assert cotizacion["as_of"].startswith("2026-08-07T13:32")


def test_tick_repetido_no_duplica(db, perfil):
    """The same minute arrives again and again: the primary key must absorb it."""
    proveedor = ProveedorFalso({"AAPL": barras(3)})

    ingest_once(db, proveedor, ["AAPL"])
    ingest_once(db, proveedor, ["AAPL"])
    ingest_once(db, proveedor, ["AAPL"])

    assert db.query("select count(1) n from bars_1m")[0]["n"] == 3


def test_the_bar_in_progress_is_updated(db, perfil):
    """Primero llega a medias, luego cerrada. Debe quedar la version final."""
    ingest_once(db, ProveedorFalso({"AAPL": barras(2)}), ["AAPL"])

    cerradas = barras(2)
    cerradas[-1] = Bar(
        timestamp=cerradas[-1].timestamp, open=101, high=120,
        low=99, close=118, volume=99_999,
    )
    ingest_once(db, ProveedorFalso({"AAPL": cerradas}), ["AAPL"])

    rows = db.query("select * from bars_1m order by ts")
    assert len(rows) == 2
    assert rows[-1]["close"] == 118
    assert rows[-1]["volume"] == 99_999


def test_a_symbol_with_no_data_is_noted_but_does_not_break(db, perfil):
    proveedor = ProveedorFalso({"AAPL": barras(2)})

    resultado = ingest_once(db, proveedor, ["AAPL", "FANTASMA"])

    assert resultado.ok
    assert resultado.empty == ["FANTASMA"]
    assert db.ingest_health(limit=1)[0]["symbols_failed"] == 1


def test_a_network_failure_does_not_raise_and_is_recorded(db, perfil):
    """The loop calling this must stay alive: a lost minute is a gap, not a
    breakage."""
    proveedor = ProveedorFalso(error=RuntimeError("connection reset"))

    resultado = ingest_once(db, proveedor, ["AAPL"])

    assert not resultado.ok
    assert "connection reset" in resultado.error
    assert not resultado.rate_limited

    run = db.ingest_health(limit=1)[0]
    assert run["symbols_failed"] == 1
    assert run["finished_at"] is not None


def test_rate_limit_queda_marcado_aparte(db, perfil):
    """Telling them apart matters: a sustained 429 changes the decision (fewer
    symbols, or another source), a one-off network error does not."""
    proveedor = ProveedorFalso(error=RuntimeError("429 Too Many Requests"))

    resultado = ingest_once(db, proveedor, ["AAPL"])

    assert resultado.rate_limited
    assert db.ingest_health(limit=1)[0]["rate_limited"] == 1


def test_an_empty_list_does_not_touch_the_database(db):
    proveedor = ProveedorFalso()

    resultado = ingest_once(db, proveedor, [])

    assert resultado.pedidos == 0
    assert proveedor.llamadas == []
    assert db.query("select count(1) n from ingest_runs")[0]["n"] == 0


def test_each_tick_leaves_a_health_row(db, perfil):
    proveedor = ProveedorFalso({"AAPL": barras(1)})

    for _ in range(3):
        ingest_once(db, proveedor, ["AAPL"])

    assert db.query("select count(1) n from ingest_runs")[0]["n"] == 3
    assert all(r["finished_at"] for r in db.ingest_health())


# -- Continuidad entre arranques --------------------------------------------


def test_on_restart_it_resumes_where_it_left_off(db, perfil):
    """Without this, every startup would rewrite the whole session every minute."""
    ingest_once(db, ProveedorFalso({"AAPL": barras(5)}), ["AAPL"])

    recuperado = load_last_timestamps(db)

    assert recuperado["AAPL"] == barras(5)[-1].timestamp.isoformat()


def test_a_gap_fills_itself_in(db, perfil):
    """The ingestor was down for several minutes: on returning it must bring back
    what was lost, not just the latest."""
    ingest_once(db, ProveedorFalso({"AAPL": barras(2)}), ["AAPL"])
    last_ts = load_last_timestamps(db)

    ingest_once(db, ProveedorFalso({"AAPL": barras(10)}), ["AAPL"], last_ts=last_ts)

    assert db.query("select count(1) n from bars_1m")[0]["n"] == 10


# -- Referencia del porcentaje del dia --------------------------------------


def test_with_no_bar_cache_the_reference_is_the_open(db, perfil):
    """It degrades gracefully: on a freshly created database `bar_cache` is empty
    and a percentage still has to come out."""
    ingest_once(db, ProveedorFalso({"AAPL": barras(3)}), ["AAPL"])

    cotizacion = db.latest_quotes()["AAPL"]
    assert cotizacion["prev_close"] == barras(3)[0].open
    assert cotizacion["change_pct"] is not None


def test_with_bar_cache_the_previous_close_is_used(db, perfil):
    db.execute(
        "insert into bar_cache (symbol, interval, ts, open, high, low, close, volume) "
        "values ('AAPL', '1d', '2026-08-06T00:00:00+00:00', 90, 95, 89, 90, 1000)"
    )

    ingest_once(db, ProveedorFalso({"AAPL": barras(3)}), ["AAPL"])

    cotizacion = db.latest_quotes()["AAPL"]
    assert cotizacion["prev_close"] == 90.0
    esperado = round((barras(3)[-1].close / 90.0 - 1) * 100, 4)
    assert cotizacion["change_pct"] == esperado


def test_timestamps_are_always_in_utc(db, perfil):
    """Mixing zones in the database would be a silent source of gaps and duplicates."""
    ny = timezone(timedelta(hours=-4))
    bars = [Bar(timestamp=datetime(2026, 8, 7, 9, 30, tzinfo=ny),
                open=1, high=2, low=1, close=1.5, volume=10)]

    ingest_once(db, ProveedorFalso({"AAPL": bars}), ["AAPL"])

    assert db.query("select ts from bars_1m")[0]["ts"] == "2026-08-07T13:30:00+00:00"


def test_yahooquotes_requiere_yfinance():
    """If the package is missing, the message has to say what to install."""
    from src.ingest import YahooQuotes

    try:
        YahooQuotes()
    except IngestError as exc:  # pragma: no cover - only if yfinance is absent
        assert "pip install yfinance" in str(exc)


# -- Gap backfill (F2.10) ---------------------------------------------------
#
# The bars are referred to `now` and not to BASE because the backfill only looks
# at the last few days: with a fixed date, the suite would start failing on its
# own as time passed, and that is not a failure of the code.


def ahora_en_minutos() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def barras_recientes(n: int, *, dias_atras: int = 0, precio: float = 100.0) -> list[Bar]:
    inicio = ahora_en_minutos() - timedelta(days=dias_atras, minutes=n)
    return barras(n, desde=inicio, precio=precio)


def test_backfill_recovers_a_whole_lost_session(db, perfil):
    """The case the ticks do NOT heal (F2.10). A gap within the session fills
    itself in, because each tick asks for the complete day; but if the process
    died on Friday afternoon, no Monday tick ever looks back at Friday."""
    ayer = barras_recientes(5, dias_atras=2)
    hoy = barras_recientes(5)
    # Only the first two bars of the session two days ago were captured.
    ingest_once(db, ProveedorFalso({"AAPL": ayer[:2]}), ["AAPL"])

    resultado = backfill_gaps(
        db, ProveedorFalso({"AAPL": ayer + hoy}), ["AAPL"], days=5
    )

    assert resultado.ok
    assert resultado.gaps == {"AAPL": 8}, "3 de la sesion perdida + las 5 de hoy"
    assert db.query("select count(1) n from bars_1m")[0]["n"] == 10


def test_backfill_does_not_rewrite_what_is_already_there(db, perfil):
    """Five days of the European universe are ~225,000 rows. Rewriting them every
    afternoon would not fail: it would show up only as a task taking longer and longer."""
    bars = barras_recientes(10)
    ingest_once(db, ProveedorFalso({"AAPL": bars}), ["AAPL"])

    resultado = backfill_gaps(db, ProveedorFalso({"AAPL": bars}), ["AAPL"], days=5)

    assert resultado.gaps == {}
    # Only the last one, which is refreshed on purpose (see the next test).
    assert resultado.barras_escritas == 1
    assert db.query("select count(1) n from bars_1m")[0]["n"] == 10


def test_backfill_refreshes_the_last_bar_even_if_it_was_there(db, perfil):
    """For the same reason the tick rewrites its own: the stored version may have
    been captured with the minute half done, and in the United States (drain=0)
    that half-done version is precisely the session close."""
    bars = barras_recientes(3)
    ingest_once(db, ProveedorFalso({"AAPL": bars}), ["AAPL"])

    cerradas = list(bars)
    cerradas[-1] = Bar(
        timestamp=bars[-1].timestamp, open=101, high=130, low=99,
        close=128, volume=88_888,
    )
    backfill_gaps(db, ProveedorFalso({"AAPL": cerradas}), ["AAPL"], days=5)

    rows = db.query("select * from bars_1m order by ts")
    assert len(rows) == 3
    assert rows[-1]["close"] == 128
    assert rows[-1]["volume"] == 88_888


def test_backfill_does_not_count_that_refresh_as_a_gap(db, perfil):
    """If it counted, every symbol would have 'one gap' every afternoon and the
    figure would stop being useful for deciding whether there was an outage."""
    bars = barras_recientes(4)
    ingest_once(db, ProveedorFalso({"AAPL": bars}), ["AAPL"])

    resultado = backfill_gaps(db, ProveedorFalso({"AAPL": bars}), ["AAPL"], days=5)

    assert "AAPL" not in resultado.gaps


def test_backfill_ignores_what_falls_outside_the_window(db, perfil):
    """Yahoo serves 30 days at 1m, but the backfill only compares against what is
    there from its cut-off: writing further back would be writing blind."""
    viejas = barras_recientes(5, dias_atras=20)

    resultado = backfill_gaps(db, ProveedorFalso({"AAPL": viejas}), ["AAPL"], days=2)

    assert resultado.barras_escritas == 0
    assert db.query("select count(1) n from bars_1m")[0]["n"] == 0


def test_backfill_clamps_the_days_to_yahoos_maximum(db, perfil):
    """Asking for more than 7 days at 1m does not error: it returns an empty
    frame, which is the worst way to fail."""
    proveedor = ProveedorFalso({"AAPL": barras_recientes(2)})

    resultado = backfill_gaps(db, proveedor, ["AAPL"], days=30)

    assert proveedor.dias_pedidos == [BACKFILL_DIAS_MAX]
    assert resultado.dias == BACKFILL_DIAS_MAX


def test_the_tick_still_asks_for_a_single_day(db, perfil):
    """`days` is the only thing separating a tick from a backfill, so it is worth
    pinning down that the tick does not eat Yahoo's quota."""
    proveedor = ProveedorFalso({"AAPL": barras(2)})

    ingest_once(db, proveedor, ["AAPL"])

    assert proveedor.dias_pedidos == [1]


def test_the_backfill_is_recorded_apart_from_the_ticks(db, perfil):
    """A backfill downloads several days at once: mixed in with the ticks, a
    single one of its rows shifts any latency average."""
    ingest_once(db, ProveedorFalso({"AAPL": barras(2)}), ["AAPL"])
    backfill_gaps(db, ProveedorFalso({"AAPL": barras_recientes(2)}), ["AAPL"])

    assert [r["kind"] for r in db.ingest_health()] == ["backfill", "tick"]
    assert [r["kind"] for r in db.ingest_health(kind="tick")] == ["tick"]
    assert len(db.ingest_health(kind="backfill")) == 1


def test_a_failed_backfill_does_not_raise_and_is_recorded(db, perfil):
    """The ingestor's loop calls it right before sleeping: if it raised, the
    process would die at closing time every single day."""
    resultado = backfill_gaps(
        db, ProveedorFalso(error=RuntimeError("429 Too Many Requests")), ["AAPL"]
    )

    assert not resultado.ok
    assert resultado.rate_limited
    run = db.ingest_health(kind="backfill")[0]
    assert run["symbols_failed"] == 1
    assert run["finished_at"] is not None


def test_a_backfill_with_no_symbols_does_not_touch_the_database(db):
    proveedor = ProveedorFalso()

    resultado = backfill_gaps(db, proveedor, [])

    assert proveedor.llamadas == []
    assert resultado.barras_escritas == 0
    assert db.query("select count(1) n from ingest_runs")[0]["n"] == 0


def test_the_backfill_is_switched_off_with_zero_days(db, perfil):
    """`INGEST_BACKFILL_DAYS=0` has to be a real off switch, not one day."""
    proveedor = ProveedorFalso({"AAPL": barras_recientes(3)})

    resultado = backfill_gaps(db, proveedor, ["AAPL"], days=0)

    assert proveedor.llamadas == []
    assert resultado.barras_escritas == 0


def test_the_backfill_can_be_abandoned_between_symbols(db, perfil):
    """With 89 symbols that is ~4-5 minutes of downloading (measured 2026-08-08).
    Without being able to abandon, a `docker stop` at maintenance time would wait
    it all out and end in SIGKILL."""
    datos = {s: barras_recientes(4) for s in ("AAA", "BBB", "CCC")}
    llamadas = []

    def parar():
        llamadas.append(1)
        return len(llamadas) > 1      # deja pasar el primer simbolo

    resultado = backfill_gaps(
        db, ProveedorFalso(datos), sorted(datos), should_stop=parar
    )

    assert resultado.interrumpido
    assert resultado.revisados == ["AAA"]
    # What was done stays written: that is why the writes go symbol by symbol and
    # not in a single batch at the end.
    assert db.query("select count(1) n from bars_1m")[0]["n"] == 4
    assert "interrumpido" in resultado.error


def test_a_partial_pass_is_not_recorded_as_complete(db, perfil):
    """If `symbols_ok` counted what the provider returned, an abandoned pass would
    sit in the database as if it had reviewed the whole window."""
    datos = {s: barras_recientes(2) for s in ("AAA", "BBB", "CCC")}

    backfill_gaps(
        db, ProveedorFalso(datos), sorted(datos), should_stop=lambda: True
    )

    run = db.ingest_health(kind="backfill")[0]
    assert run["symbols_ok"] == 0
    assert run["symbols_failed"] == 3
    assert "interrumpido" in run["error"]


def test_without_should_stop_every_symbol_is_reviewed(db, perfil):
    datos = {s: barras_recientes(2) for s in ("AAA", "BBB", "CCC")}

    resultado = backfill_gaps(db, ProveedorFalso(datos), sorted(datos))

    assert not resultado.interrumpido
    assert resultado.revisados == ["AAA", "BBB", "CCC"]
    assert db.ingest_health(kind="backfill")[0]["symbols_ok"] == 3


def test_kind_reaches_a_database_that_already_existed(tmp_path):
    """The same lesson as F6.4: `create table if not exists` does not add columns
    to a table that already exists, so without the migration this would work on a
    new database and be missing from the one running. The earlier rows stay as
    'tick', which is what they were."""
    import sqlite3

    from src.db import Database

    ruta = tmp_path / "vieja.db"
    with Database(path=ruta) as database:
        database.start_ingest_run(symbols_requested=3)

    plana = sqlite3.connect(ruta)
    plana.execute("alter table ingest_runs drop column kind")
    plana.commit()
    plana.close()

    with Database(path=ruta) as database:
        assert database.ingest_health()[0]["kind"] == "tick"


def test_a_symbol_with_no_data_does_not_count_as_a_gap(db, perfil):
    resultado = backfill_gaps(
        db, ProveedorFalso({"AAPL": barras_recientes(2)}), ["AAPL", "FANTASMA"]
    )

    assert resultado.con_datos == 1
    assert "FANTASMA" not in resultado.gaps
    assert db.ingest_health(kind="backfill")[0]["symbols_failed"] == 1


# -- Aviso de contencion (F2.9) ---------------------------------------------


def test_a_large_initial_load_does_not_fire_the_warning(db, perfil, caplog, monkeypatch):
    """The first tick writes the whole session and takes seconds with nobody
    blocking it. A warning that fires there cries wolf and ends up ignored.

    The clock is fake on purpose, as in the test below. The real disk used to be
    measured, and that made the test tell the truth only on the machine it was
    written on: it passed on the host (~1.1 ms/row) and failed inside the
    container (~3.9), which is exactly where the code runs. A test that breaks in
    the target environment and passes in the development one is the worst possible
    split.

    The 1.5 s for 400 rows is not invented: it is what was measured in the container.
    """
    import logging

    import src.ingest as ingest_mod

    reloj = iter([0.0, 0.0, 0.0, 1.5])
    monkeypatch.setattr(ingest_mod.time, "monotonic", lambda: next(reloj))

    with caplog.at_level(logging.WARNING, logger="src.ingest"):
        ingest_once(db, ProveedorFalso({"AAPL": barras(400)}), ["AAPL"])

    assert "contencion" not in caplog.text.lower()


def test_a_slow_write_for_few_rows_does_warn(db, perfil, caplog, monkeypatch):
    """What gives away a wait on busy_timeout: a lot of time, few rows."""
    import logging

    import src.ingest as ingest_mod

    reloj = iter([0.0, 0.0, 0.0, 3.0])
    monkeypatch.setattr(ingest_mod.time, "monotonic", lambda: next(reloj))

    with caplog.at_level(logging.WARNING, logger="src.ingest"):
        ingest_once(db, ProveedorFalso({"AAPL": barras(2)}), ["AAPL"])

    assert "contencion" in caplog.text.lower()
