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
    """Devuelve lo que se le diga. Puede fallar a voluntad."""

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


# -- Relleno de huecos (F2.10) ----------------------------------------------
#
# Las barras van referidas a `ahora` y no a BASE porque el relleno solo mira los
# ultimos dias: con una fecha fija, la suite empezaria a fallar sola cuando pasara
# el tiempo, y eso no es un fallo del codigo.


def ahora_en_minutos() -> datetime:
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)


def barras_recientes(n: int, *, dias_atras: int = 0, precio: float = 100.0) -> list[Bar]:
    inicio = ahora_en_minutos() - timedelta(days=dias_atras, minutes=n)
    return barras(n, desde=inicio, precio=precio)


def test_backfill_recupera_una_sesion_perdida_entera(db, perfil):
    """El caso que los ticks NO curan (F2.10). Un hueco dentro de la sesion se
    rellena solo, porque cada tick pide el dia completo; pero si el proceso murio
    el viernes por la tarde, ningun tick del lunes vuelve a mirar el viernes."""
    ayer = barras_recientes(5, dias_atras=2)
    hoy = barras_recientes(5)
    # Solo se capturaron las dos primeras barras de la sesion de hace dos dias.
    ingest_once(db, ProveedorFalso({"AAPL": ayer[:2]}), ["AAPL"])

    resultado = backfill_gaps(
        db, ProveedorFalso({"AAPL": ayer + hoy}), ["AAPL"], days=5
    )

    assert resultado.ok
    assert resultado.huecos == {"AAPL": 8}, "3 de la sesion perdida + las 5 de hoy"
    assert db.query("select count(1) n from bars_1m")[0]["n"] == 10


def test_backfill_no_reescribe_lo_que_ya_esta(db, perfil):
    """Cinco dias del universo europeo son ~225.000 filas. Reescribirlas cada
    tarde no fallaria: se notaria solo como una tarea que tarda cada vez mas."""
    bars = barras_recientes(10)
    ingest_once(db, ProveedorFalso({"AAPL": bars}), ["AAPL"])

    resultado = backfill_gaps(db, ProveedorFalso({"AAPL": bars}), ["AAPL"], days=5)

    assert resultado.huecos == {}
    # Solo la ultima, que se refresca a proposito (ver el test siguiente).
    assert resultado.barras_escritas == 1
    assert db.query("select count(1) n from bars_1m")[0]["n"] == 10


def test_backfill_refresca_la_ultima_barra_aunque_ya_estuviera(db, perfil):
    """Por lo mismo que el tick reescribe la suya: la version guardada pudo
    capturarse con el minuto a medias, y en Estados Unidos (drain=0) esa version
    a medias es justo la del cierre de sesion."""
    bars = barras_recientes(3)
    ingest_once(db, ProveedorFalso({"AAPL": bars}), ["AAPL"])

    cerradas = list(bars)
    cerradas[-1] = Bar(
        timestamp=bars[-1].timestamp, open=101, high=130, low=99,
        close=128, volume=88_888,
    )
    backfill_gaps(db, ProveedorFalso({"AAPL": cerradas}), ["AAPL"], days=5)

    filas = db.query("select * from bars_1m order by ts")
    assert len(filas) == 3
    assert filas[-1]["close"] == 128
    assert filas[-1]["volume"] == 88_888


def test_backfill_no_cuenta_ese_refresco_como_hueco(db, perfil):
    """Si contara, cada simbolo tendria 'un hueco' todas las tardes y la cifra
    dejaria de servir para decidir si hubo una caida."""
    bars = barras_recientes(4)
    ingest_once(db, ProveedorFalso({"AAPL": bars}), ["AAPL"])

    resultado = backfill_gaps(db, ProveedorFalso({"AAPL": bars}), ["AAPL"], days=5)

    assert "AAPL" not in resultado.huecos


def test_backfill_ignora_lo_que_cae_fuera_de_la_ventana(db, perfil):
    """Yahoo sirve 30 dias en 1m, pero el relleno solo compara contra lo que hay
    desde su corte: escribir mas atras seria escribir a ciegas."""
    viejas = barras_recientes(5, dias_atras=20)

    resultado = backfill_gaps(db, ProveedorFalso({"AAPL": viejas}), ["AAPL"], days=2)

    assert resultado.barras_escritas == 0
    assert db.query("select count(1) n from bars_1m")[0]["n"] == 0


def test_backfill_recorta_los_dias_al_maximo_de_yahoo(db, perfil):
    """Pedir mas de 7 dias en 1m no da error: devuelve un marco vacio, que es la
    peor forma de fallar."""
    proveedor = ProveedorFalso({"AAPL": barras_recientes(2)})

    resultado = backfill_gaps(db, proveedor, ["AAPL"], days=30)

    assert proveedor.dias_pedidos == [BACKFILL_DIAS_MAX]
    assert resultado.dias == BACKFILL_DIAS_MAX


def test_el_tick_sigue_pidiendo_un_solo_dia(db, perfil):
    """`days` es lo unico que separa un tick de un relleno, asi que conviene
    fijar que el tick no se lleve por delante la cuota de Yahoo."""
    proveedor = ProveedorFalso({"AAPL": barras(2)})

    ingest_once(db, proveedor, ["AAPL"])

    assert proveedor.dias_pedidos == [1]


def test_el_relleno_se_registra_aparte_de_los_ticks(db, perfil):
    """Un backfill descarga varios dias de golpe: mezclado con los ticks, una
    sola de sus filas desplaza cualquier media de latencia."""
    ingest_once(db, ProveedorFalso({"AAPL": barras(2)}), ["AAPL"])
    backfill_gaps(db, ProveedorFalso({"AAPL": barras_recientes(2)}), ["AAPL"])

    assert [r["kind"] for r in db.ingest_health()] == ["backfill", "tick"]
    assert [r["kind"] for r in db.ingest_health(kind="tick")] == ["tick"]
    assert len(db.ingest_health(kind="backfill")) == 1


def test_un_relleno_fallido_no_lanza_y_queda_registrado(db, perfil):
    """Lo llama el bucle del ingestor justo antes de dormir: si lanzara, el
    proceso moriria a la hora del cierre todos los dias."""
    resultado = backfill_gaps(
        db, ProveedorFalso(error=RuntimeError("429 Too Many Requests")), ["AAPL"]
    )

    assert not resultado.ok
    assert resultado.rate_limited
    run = db.ingest_health(kind="backfill")[0]
    assert run["symbols_failed"] == 1
    assert run["finished_at"] is not None


def test_relleno_sin_simbolos_no_toca_la_base(db):
    proveedor = ProveedorFalso()

    resultado = backfill_gaps(db, proveedor, [])

    assert proveedor.llamadas == []
    assert resultado.barras_escritas == 0
    assert db.query("select count(1) n from ingest_runs")[0]["n"] == 0


def test_relleno_apagado_con_cero_dias(db, perfil):
    """`INGEST_BACKFILL_DAYS=0` tiene que ser un apagado de verdad, no un dia."""
    proveedor = ProveedorFalso({"AAPL": barras_recientes(3)})

    resultado = backfill_gaps(db, proveedor, ["AAPL"], days=0)

    assert proveedor.llamadas == []
    assert resultado.barras_escritas == 0


def test_el_relleno_se_puede_abandonar_entre_simbolos(db, perfil):
    """Con 89 simbolos son ~4-5 minutos de descarga (medido el 2026-08-08). Sin
    poder abandonar, un `docker stop` a la hora del mantenimiento esperaria todo
    eso y acabaria en SIGKILL."""
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
    # Lo hecho se queda escrito: por eso las escrituras van por simbolo y no en
    # un unico lote al final.
    assert db.query("select count(1) n from bars_1m")[0]["n"] == 4
    assert "interrumpido" in resultado.error


def test_una_pasada_a_medias_no_se_registra_como_completa(db, perfil):
    """Si `symbols_ok` contara lo que devolvio el proveedor, una pasada abandonada
    quedaria en la base como si hubiera revisado la ventana entera."""
    datos = {s: barras_recientes(2) for s in ("AAA", "BBB", "CCC")}

    backfill_gaps(
        db, ProveedorFalso(datos), sorted(datos), should_stop=lambda: True
    )

    run = db.ingest_health(kind="backfill")[0]
    assert run["symbols_ok"] == 0
    assert run["symbols_failed"] == 3
    assert "interrumpido" in run["error"]


def test_sin_should_stop_se_revisan_todos(db, perfil):
    datos = {s: barras_recientes(2) for s in ("AAA", "BBB", "CCC")}

    resultado = backfill_gaps(db, ProveedorFalso(datos), sorted(datos))

    assert not resultado.interrumpido
    assert resultado.revisados == ["AAA", "BBB", "CCC"]
    assert db.ingest_health(kind="backfill")[0]["symbols_ok"] == 3


def test_kind_llega_a_una_base_que_ya_existia(tmp_path):
    """Misma leccion que F6.4: `create table if not exists` no anade columnas a
    una tabla que ya existe, asi que sin la migracion esto funcionaria en una base
    nueva y faltaria en la que esta corriendo. Las filas de antes quedan como
    'tick', que es lo que eran."""
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


def test_un_simbolo_sin_datos_no_cuenta_como_hueco(db, perfil):
    resultado = backfill_gaps(
        db, ProveedorFalso({"AAPL": barras_recientes(2)}), ["AAPL", "FANTASMA"]
    )

    assert resultado.con_datos == 1
    assert "FANTASMA" not in resultado.huecos
    assert db.ingest_health(kind="backfill")[0]["symbols_failed"] == 1


# -- Aviso de contencion (F2.9) ---------------------------------------------


def test_carga_inicial_grande_no_dispara_el_aviso(db, perfil, caplog, monkeypatch):
    """El primer tick escribe la sesion entera y tarda segundos sin que nadie lo
    bloquee. Un aviso que salta ahi cria lobos y se acaba ignorando.

    El reloj es falso a proposito, igual que en el test de abajo. Antes se medi­a
    el disco de verdad, y eso hacia que el test dijera la verdad solo en la
    maquina donde se escribio: pasaba en el anfitrion (~1.1 ms/fila) y fallaba
    dentro del contenedor (~3.9), que es justamente donde el codigo corre. Un test
    que se rompe en el entorno de destino y pasa en el de desarrollo es el peor
    reparto posible.

    El 1.5 s para 400 filas no es inventado: es lo medido en el contenedor.
    """
    import logging

    import src.ingest as ingest_mod

    reloj = iter([0.0, 0.0, 0.0, 1.5])
    monkeypatch.setattr(ingest_mod.time, "monotonic", lambda: next(reloj))

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
