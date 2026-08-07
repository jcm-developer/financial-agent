"""Ingesta de precios minuto a minuto.

Se prueba contra un proveedor de mentira, sin red: lo que interesa comprobar no
es que yfinance funcione, sino que la escritura sea idempotente, que un fallo no
tumbe el bucle y que un hueco se recupere solo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.indicators import Bar
from src.ingest import (
    IngestError,
    _bars_to_write,
    _es_rate_limit,
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
    """Devuelve lo que se le diga. Puede fallar a voluntad."""

    def __init__(self, datos: dict[str, list[Bar]] | None = None, error: Exception | None = None):
        self.datos = datos or {}
        self.error = error
        self.llamadas: list[list[str]] = []

    def fetch(self, symbols):
        self.llamadas.append(list(symbols))
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


def test_sin_historico_se_escribe_todo():
    bars = barras(5)
    assert _bars_to_write(bars, None) == bars


def test_se_reescribe_la_ultima_barra_conocida():
    """La barra del minuto en curso sigue cambiando: si solo se escribieran las
    posteriores, su cierre quedaria congelado en el primer valor visto."""
    bars = barras(5)
    ultima = bars[2].timestamp.isoformat()

    pendientes = _bars_to_write(bars, ultima)

    assert pendientes[0].timestamp == bars[2].timestamp, "debe incluir la ya conocida"
    assert len(pendientes) == 3


def test_sin_barras_nuevas_se_refresca_un_solape_corto():
    bars = barras(10)
    futuro = (bars[-1].timestamp + timedelta(minutes=5)).isoformat()

    pendientes = _bars_to_write(bars, futuro)

    assert len(pendientes) == 3, "solape corto, no la sesion entera"
    assert pendientes[-1] is bars[-1]


# -- Deteccion de rate limit -------------------------------------------------


@pytest.mark.parametrize("mensaje", [
    "429 Too Many Requests", "Rate limit exceeded", "HTTP Error 429",
])
def test_se_reconoce_el_rate_limit(mensaje):
    assert _es_rate_limit(RuntimeError(mensaje))


def test_un_error_normal_no_es_rate_limit():
    assert not _es_rate_limit(RuntimeError("connection reset by peer"))


# -- Tick completo -----------------------------------------------------------


def test_tick_escribe_barras_y_cotizacion(db, perfil):
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
    """El mismo minuto entra una y otra vez: la clave primaria debe absorberlo."""
    proveedor = ProveedorFalso({"AAPL": barras(3)})

    ingest_once(db, proveedor, ["AAPL"])
    ingest_once(db, proveedor, ["AAPL"])
    ingest_once(db, proveedor, ["AAPL"])

    assert db.query("select count(1) n from bars_1m")[0]["n"] == 3


def test_la_barra_en_curso_se_actualiza(db, perfil):
    """Primero llega a medias, luego cerrada. Debe quedar la version final."""
    ingest_once(db, ProveedorFalso({"AAPL": barras(2)}), ["AAPL"])

    cerradas = barras(2)
    cerradas[-1] = Bar(
        timestamp=cerradas[-1].timestamp, open=101, high=120,
        low=99, close=118, volume=99_999,
    )
    ingest_once(db, ProveedorFalso({"AAPL": cerradas}), ["AAPL"])

    filas = db.query("select * from bars_1m order by ts")
    assert len(filas) == 2
    assert filas[-1]["close"] == 118
    assert filas[-1]["volume"] == 99_999


def test_simbolo_sin_datos_se_anota_pero_no_rompe(db, perfil):
    proveedor = ProveedorFalso({"AAPL": barras(2)})

    resultado = ingest_once(db, proveedor, ["AAPL", "FANTASMA"])

    assert resultado.ok
    assert resultado.vacios == ["FANTASMA"]
    assert db.ingest_health(limit=1)[0]["symbols_failed"] == 1


def test_fallo_de_red_no_lanza_y_queda_registrado(db, perfil):
    """El bucle que llama a esto debe seguir vivo: un minuto perdido es un hueco,
    no una averia."""
    proveedor = ProveedorFalso(error=RuntimeError("connection reset"))

    resultado = ingest_once(db, proveedor, ["AAPL"])

    assert not resultado.ok
    assert "connection reset" in resultado.error
    assert not resultado.rate_limited

    run = db.ingest_health(limit=1)[0]
    assert run["symbols_failed"] == 1
    assert run["finished_at"] is not None


def test_rate_limit_queda_marcado_aparte(db, perfil):
    """Distinguirlo importa: un 429 sostenido cambia la decision (bajar simbolos
    o cambiar de fuente), un error de red puntual no."""
    proveedor = ProveedorFalso(error=RuntimeError("429 Too Many Requests"))

    resultado = ingest_once(db, proveedor, ["AAPL"])

    assert resultado.rate_limited
    assert db.ingest_health(limit=1)[0]["rate_limited"] == 1


def test_lista_vacia_no_toca_la_base(db):
    proveedor = ProveedorFalso()

    resultado = ingest_once(db, proveedor, [])

    assert resultado.pedidos == 0
    assert proveedor.llamadas == []
    assert db.query("select count(1) n from ingest_runs")[0]["n"] == 0


def test_cada_tick_deja_una_fila_de_salud(db, perfil):
    proveedor = ProveedorFalso({"AAPL": barras(1)})

    for _ in range(3):
        ingest_once(db, proveedor, ["AAPL"])

    assert db.query("select count(1) n from ingest_runs")[0]["n"] == 3
    assert all(r["finished_at"] for r in db.ingest_health())


# -- Continuidad entre arranques --------------------------------------------


def test_al_reiniciar_se_retoma_donde_se_dejo(db, perfil):
    """Sin esto, cada arranque reescribiria la sesion entera cada minuto."""
    ingest_once(db, ProveedorFalso({"AAPL": barras(5)}), ["AAPL"])

    recuperado = load_last_timestamps(db)

    assert recuperado["AAPL"] == barras(5)[-1].timestamp.isoformat()


def test_un_hueco_se_rellena_solo(db, perfil):
    """El ingestor estuvo caido varios minutos: al volver debe traerse lo perdido,
    no solo lo ultimo."""
    ingest_once(db, ProveedorFalso({"AAPL": barras(2)}), ["AAPL"])
    last_ts = load_last_timestamps(db)

    ingest_once(db, ProveedorFalso({"AAPL": barras(10)}), ["AAPL"], last_ts=last_ts)

    assert db.query("select count(1) n from bars_1m")[0]["n"] == 10


# -- Referencia del porcentaje del dia --------------------------------------


def test_sin_bar_cache_la_referencia_es_la_apertura(db, perfil):
    """Degrada con gracia: en una base recien creada `bar_cache` esta vacio y aun
    asi tiene que salir un porcentaje."""
    ingest_once(db, ProveedorFalso({"AAPL": barras(3)}), ["AAPL"])

    cotizacion = db.latest_quotes()["AAPL"]
    assert cotizacion["prev_close"] == barras(3)[0].open
    assert cotizacion["change_pct"] is not None


def test_con_bar_cache_se_usa_el_cierre_anterior(db, perfil):
    db.execute(
        "insert into bar_cache (symbol, interval, ts, open, high, low, close, volume) "
        "values ('AAPL', '1d', '2026-08-06T00:00:00+00:00', 90, 95, 89, 90, 1000)"
    )

    ingest_once(db, ProveedorFalso({"AAPL": barras(3)}), ["AAPL"])

    cotizacion = db.latest_quotes()["AAPL"]
    assert cotizacion["prev_close"] == 90.0
    esperado = round((barras(3)[-1].close / 90.0 - 1) * 100, 4)
    assert cotizacion["change_pct"] == esperado


def test_marcas_de_tiempo_siempre_en_utc(db, perfil):
    """Mezclar husos en la base seria una fuente silenciosa de huecos y duplicados."""
    ny = timezone(timedelta(hours=-4))
    bars = [Bar(timestamp=datetime(2026, 8, 7, 9, 30, tzinfo=ny),
                open=1, high=2, low=1, close=1.5, volume=10)]

    ingest_once(db, ProveedorFalso({"AAPL": bars}), ["AAPL"])

    assert db.query("select ts from bars_1m")[0]["ts"] == "2026-08-07T13:30:00+00:00"


def test_yahooquotes_requiere_yfinance():
    """Si falta el paquete, el mensaje debe decir que instalar."""
    from src.ingest import YahooQuotes

    try:
        YahooQuotes()
    except IngestError as exc:  # pragma: no cover - solo si yfinance no esta
        assert "pip install yfinance" in str(exc)


# -- Aviso de contencion (F2.9) ---------------------------------------------


def test_carga_inicial_grande_no_dispara_el_aviso(db, perfil, caplog):
    """El primer tick escribe la sesion entera y tarda segundos sin que nadie lo
    bloquee. Un aviso que salta ahi cria lobos y se acaba ignorando."""
    import logging

    with caplog.at_level(logging.WARNING, logger="src.ingest"):
        ingest_once(db, ProveedorFalso({"AAPL": barras(400)}), ["AAPL"])

    assert "contencion" not in caplog.text.lower()


def test_escritura_lenta_para_pocas_filas_si_avisa(db, perfil, caplog, monkeypatch):
    """Lo que delata una espera por busy_timeout: mucho tiempo, pocas filas."""
    import logging

    import src.ingest as ingest_mod

    reloj = iter([0.0, 0.0, 0.0, 3.0])
    monkeypatch.setattr(ingest_mod.time, "monotonic", lambda: next(reloj))

    with caplog.at_level(logging.WARNING, logger="src.ingest"):
        ingest_once(db, ProveedorFalso({"AAPL": barras(2)}), ["AAPL"])

    assert "contencion" in caplog.text.lower()
