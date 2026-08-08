"""Tests de la API (F3.9).

Se prueban contra `TestClient`, que habla con la aplicacion en memoria via httpx:
sin abrir sockets, sin red y sin arrancar uvicorn. Cada test monta su propia
aplicacion sobre una base en `tmp_path`, que es la razon de que `ApiConfig` viva
en `app.state` y no en un global.

El grupo que mas importa es el de "la API no puede escribir en el historico".
No comprueba que los endpoints se porten bien —eso seria comprobar el codigo que
ya se ha leido— sino que **no puedan portarse mal**: R5 dice que al abrir la API
a escritura se pierde la garantia de solo lectura del dashboard, y estos tests
son la mitigacion que D5 prometio.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.deps import ApiConfig, config_db, read_db
from api.guard import WRITABLE, ConfigDatabase, HistoryIsReadOnly, history_tables
from api.main import create_app
from api.models import SettingsUpdate
from src.db import Database

# Rutas que cambian estado sin escribir en la base: lanzan `run.py cycle` como
# subproceso, que abre su propia conexion. Es la separacion que describe
# `api/routes/control.py`, y esta escrita aqui para que añadir una ruta de
# escritura sin conexion acotada tenga que ser una decision consciente.
CONTROL_ROUTES = {"/api/cycles/run", "/api/cycles/stop"}


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "api.db")


@pytest.fixture
def client(db_path):
    app = create_app(ApiConfig(db_path=db_path, controls=True))
    with TestClient(app) as test_client:
        test_client.app_ref = app
        yield test_client


@pytest.fixture
def profile(client):
    """Un perfil europeo recien creado, en borrador."""
    response = client.post("/api/profiles", json={"name": "europa-01", "market": "eu"})
    assert response.status_code == 201, response.text
    return response.json()


def seed_history(db_path: str, profile_id: str) -> dict:
    """Rellena a mano un ciclo con su decision, su veredicto y su orden.

    Escribe con la conexion normal a proposito: es lo que hace el agente. La
    gracia de los tests de abajo es que la API pueda **leer** todo esto y no
    pueda tocarlo.
    """
    with Database(path=db_path) as db:
        portfolio_id = db.get_profile(profile_id)["portfolio_id"]
        cycle_id = db.start_cycle(
            portfolio_id=portfolio_id, equity_start=10_000.0, cash_start=10_000.0,
            market_open=True, symbols=["SAN.MC", "ITX.MC"], llm_model="stub",
            settings={"market": "eu", "risk_profile": 5},
        )
        db.finish_cycle(cycle_id, status="completed", equity_end=10_120.0)

        db.execute(
            "insert into decisions (id, cycle_id, portfolio_id, symbol, kind, action, "
            "conviction, thesis, reference_price, llm_model, created_at) "
            "values ('d1', ?, ?, 'SAN.MC', 'entry', 'buy', 80, 'Tendencia.', "
            "4.5, 'stub', '2026-08-08T10:00:00+00:00')",
            (cycle_id, portfolio_id),
        )
        db.execute(
            "insert into risk_events (id, cycle_id, portfolio_id, decision_id, symbol, "
            "verdict, rule, reason, approved_qty, created_at) values "
            "('r1', ?, ?, 'd1', 'SAN.MC', 'approved', 'position_size', 'ok', 100, "
            "'2026-08-08T10:00:01+00:00')",
            (cycle_id, portfolio_id),
        )
        order_id = db.save_order(
            cycle_id=cycle_id, portfolio_id=portfolio_id, symbol="SAN.MC",
            side="buy", qty=100, status="filled", decision_id="d1",
            filled_qty=100, filled_avg_price=4.5,
        )
        position_id = db.open_position(
            portfolio_id=portfolio_id, symbol="SAN.MC", qty=100, entry_price=4.5,
            stop_price=4.0, target_price=5.5, thesis="Tendencia.",
            entry_order_id=order_id,
        )
        db.save_equity_snapshot(
            portfolio_id=portfolio_id, cycle_id=cycle_id, equity=10_120.0,
            cash=9_670.0, positions_value=450.0, open_positions=1,
            day_pnl=120.0, day_pnl_pct=1.2,
        )
        db.upsert_quotes([{
            "symbol": "SAN.MC", "price": 4.8, "prev_close": 4.5, "change_pct": 6.6,
            "volume": 1_000_000, "as_of": "2026-08-08T15:30:00+00:00",
        }])
        run_id = db.start_ingest_run(symbols_requested=2)
        db.finish_ingest_run(run_id, symbols_ok=2, symbols_failed=0, latency_ms=850)

    return {
        "portfolio_id": portfolio_id, "cycle_id": cycle_id,
        "order_id": order_id, "position_id": position_id,
    }


def history_counts(db_path: str) -> dict[str, int]:
    """Cuantas filas hay en cada tabla que la API no puede escribir."""
    with Database(path=db_path, read_only=True) as db:
        return {
            table: db.query(f"select count(1) as n from {table}")[0]["n"]
            for table in history_tables(db)
        }


# ======================================================================
# F3.3 -- La API no puede escribir en el historico
# ======================================================================

def test_la_conexion_de_la_api_no_escribe_en_ninguna_tabla_de_historico(
    db_path, profile
):
    """La comprobacion no usa una lista escrita a mano: recorre el esquema.

    Asi, una tabla nueva en `schema.sql` entra sola en la prueba. Con una lista
    fija, la tabla que alguien añadiera manana quedaria sin cubrir justo cuando
    mas falta hace.
    """
    with ConfigDatabase(path=db_path) as db:
        protegidas = history_tables(db)
        assert protegidas, "el esquema deberia tener tablas de historico"

        for table in protegidas:
            with pytest.raises(HistoryIsReadOnly):
                db._execute(f"delete from {table}")


def test_la_api_si_escribe_en_las_tablas_de_configuracion(db_path, profile):
    with ConfigDatabase(path=db_path) as db:
        assert db.update_settings(profile["id"], {"risk_profile": 9}) == ["risk_profile"]
        db.set_profile_universe(profile["id"], ["SAN.MC"])
        db.set_profile_status(profile["id"], "active")
        assert db.get_settings(profile["id"])["risk_profile"] == 9


def test_la_api_no_ejecuta_sql_libre(db_path):
    """`Database.execute` esta para las herramientas de tools/, no para la web.

    Sin este cierre, el autorizador seria la unica barrera; con el, para tocar la
    base hay que pasar por un metodo con nombre, que es donde se ve que hace.
    """
    with ConfigDatabase(path=db_path) as db:
        with pytest.raises(HistoryIsReadOnly):
            db.execute("update positions set qty = 0")


def test_la_api_no_puede_alterar_el_esquema(db_path):
    with ConfigDatabase(path=db_path) as db:
        for sql in (
            "create table colado (id integer)",
            "drop table cycles",
            "alter table cycles add column colada text",
        ):
            with pytest.raises(HistoryIsReadOnly):
                db._execute(sql)


def test_borrar_un_perfil_si_arrastra_su_historico(db_path, profile):
    """La unica excepcion, y es deliberada: borrar un experimento lo borra entero.

    Se comprueba **las dos mitades**: que la cascada llega al historico (si el
    autorizador la cortara, el borrado fallaria a medias) y que la ventana se
    cierra despues, para que la conexion no siga sirviendo peticiones sin
    restricciones el resto de su vida.
    """
    seed_history(db_path, profile["id"])
    assert history_counts(db_path)["positions"] == 1

    with ConfigDatabase(path=db_path) as db:
        db.delete_profile(profile["id"])

        contados = history_counts(db_path)
        assert contados["positions"] == 0
        assert contados["cycles"] == 0
        assert contados["decisions"] == 0

        # Y la puerta vuelve a estar cerrada.
        with pytest.raises(HistoryIsReadOnly):
            db._execute("delete from bars_1m")


def test_ningun_endpoint_de_escritura_recibe_una_conexion_sin_acotar(client):
    """Comprobacion estructural, no de comportamiento.

    Mira las dependencias declaradas de cada ruta: las que cambian estado tienen
    que pedir `config_db` (la acotada) o no pedir base de datos de escritura en
    absoluto. Un endpoint nuevo que abriera una conexion normal falla aqui, que
    es mucho antes de que corrompa nada.
    """
    from fastapi.routing import APIRoute

    for route in client.app_ref.routes:
        if not isinstance(route, APIRoute):
            continue
        if not (route.methods & {"POST", "PATCH", "PUT", "DELETE"}):
            continue

        usadas = _dependency_calls(route.dependant)
        assert usadas <= {read_db, config_db}, (
            f"{route.path} usa una dependencia de base de datos inesperada"
        )
        if route.path in CONTROL_ROUTES:
            assert config_db not in usadas, (
                f"{route.path} es un control: opera lanzando un subproceso, "
                "no escribiendo"
            )
        else:
            assert config_db in usadas, (
                f"{route.path} escribe pero no pide la conexion acotada"
            )


def _dependency_calls(dependant) -> set:
    """Todas las dependencias de una ruta, incluidas las anidadas."""
    encontradas = set()
    pendientes = list(dependant.dependencies)
    while pendientes:
        actual = pendientes.pop()
        if actual.call in (read_db, config_db):
            encontradas.add(actual.call)
        pendientes.extend(actual.dependencies)
    return encontradas


def test_los_endpoints_de_escritura_no_mueven_el_historico(client, db_path, profile):
    """De punta a punta: se ejercitan todas las escrituras y no se mueve nada.

    Es el complemento del test estructural. Aquel dice que la conexion esta
    acotada; este dice que, con la API entera funcionando de verdad, el numero de
    filas del historico es exactamente el mismo antes y despues.
    """
    seed_history(db_path, profile["id"])
    antes = history_counts(db_path)

    assert client.patch(
        f"/api/profiles/{profile['id']}/settings",
        json={"risk_profile": 8, "diversification": 3, "dry_run": True},
    ).status_code == 200
    assert client.patch(
        f"/api/profiles/{profile['id']}", json={"status": "active"}
    ).status_code == 200
    assert client.put(
        f"/api/profiles/{profile['id']}/universe", json={"symbols": ["SAN.MC", "ITX.MC"]}
    ).status_code == 200
    assert client.post(
        f"/api/profiles/{profile['id']}/duplicate", json={"name": "europa-02"}
    ).status_code == 201

    assert history_counts(db_path) == antes


def test_el_dashboard_se_sirve_de_una_conexion_de_solo_lectura(client, db_path, profile):
    """La garantia que ya existia antes de F3 y que no se ha perdido.

    Los endpoints de lectura siguen abriendo SQLite en modo `ro`: por ahi no se
    escribe aunque el codigo lo intente.
    """
    seed_history(db_path, profile["id"])
    assert client.get(f"/api/dashboard?profile={profile['id']}").status_code == 200

    with Database(path=db_path, read_only=True) as db:
        with pytest.raises(Exception):
            db._execute("delete from cycles")


# ======================================================================
# F3.2 -- Endpoints de lectura
# ======================================================================

def test_lista_de_perfiles_vacia(client):
    assert client.get("/api/profiles").json() == []


def test_perfil_creado_trae_mercado_divisa_y_limites(profile):
    assert profile["market"] == "eu"
    assert profile["currency"] == "EUR"
    # La divisa acompaña al numero: un presupuesto europeo con '$' invita a
    # compararlo con el de otro perfil como si fuera la misma unidad (FE.8).
    assert profile["currency_symbol"] == "€"
    assert profile["watched_symbols"] == 89
    assert profile["status"] == "draft"
    # Los limites salen de risk_presets, no de una cuenta repetida en la API.
    assert profile["limits"]["max_open_positions"] == 13
    assert "risk_per_trade_pct" in profile["limits"]["derived_fields"]


def test_el_suelo_de_liquidez_lo_pone_el_mercado(profile):
    """FE.11 tambien desde la API: con los 20 M de 'us' el screener europeo
    descartaria 15 de los 89 sin decir nada."""
    assert profile["settings"]["screener_min_dollar_volume"] == 5_000_000


def test_dashboard_lleva_perfil_y_mercado(client, db_path, profile):
    seed_history(db_path, profile["id"])
    data = client.get(f"/api/dashboard?profile=europa-01").json()

    assert data["profile"]["name"] == "europa-01"
    assert data["market"]["currency"] == "EUR"
    assert data["summary"]["cycles"] == 1


def test_posiciones_se_valoran_con_la_cotizacion_en_vivo(client, db_path, profile):
    """Y se dice **de donde** sale el precio.

    Una posicion valorada con el cierre de anteayer y otra con el precio de hace
    un minuto no se pueden sumar sin saberlo, asi que el origen viaja con el dato.
    """
    seed_history(db_path, profile["id"])
    fila = client.get(f"/api/positions?profile={profile['id']}").json()["items"][0]

    assert fila["symbol"] == "SAN.MC"
    assert fila["price_source"] == "live"
    assert fila["last_price"] == 4.8
    assert fila["unrealized_pnl"] == pytest.approx(30.0)
    assert fila["stop_distance_pct"] == pytest.approx(20.0)


def test_decisiones_traen_el_veredicto_de_riesgo(client, db_path, profile):
    seed_history(db_path, profile["id"])
    pagina = client.get(f"/api/decisions?profile={profile['id']}").json()

    assert pagina["total"] == 1
    decision = pagina["items"][0]
    assert decision["action"] == "buy"
    assert decision["verdict"] == "approved"
    assert decision["order_status"] == "filled"


def test_las_listas_pagina(client, db_path, profile):
    seed_history(db_path, profile["id"])
    pagina = client.get(f"/api/orders?profile={profile['id']}&limit=1&offset=1").json()

    assert pagina["total"] == 1
    assert pagina["limit"] == 1 and pagina["offset"] == 1
    assert pagina["items"] == []


def test_filtros_de_las_listas(client, db_path, profile):
    seed_history(db_path, profile["id"])
    pid = profile["id"]

    assert client.get(f"/api/decisions?profile={pid}&action=sell").json()["total"] == 0
    assert client.get(f"/api/decisions?profile={pid}&action=buy").json()["total"] == 1
    assert client.get(f"/api/risk-events?profile={pid}&verdict=rejected").json()["total"] == 0
    assert client.get(f"/api/positions?profile={pid}&status=closed").json()["total"] == 0


def test_detalle_de_ciclo_trae_los_parametros_con_los_que_corrio(
    client, db_path, profile
):
    """F6.3: sin esa copia, un experimento cuyos ajustes se editan a mitad deja
    de ser interpretable."""
    seed = seed_history(db_path, profile["id"])
    detalle = client.get(f"/api/cycles/{seed['cycle_id']}").json()

    assert detalle["status"] == "completed"
    assert detalle["equity_delta"] == pytest.approx(120.0)
    assert detalle["settings"]["market"] == "eu"
    assert detalle["symbols_scanned"] == ["SAN.MC", "ITX.MC"]


def test_ciclo_inexistente_da_404(client):
    assert client.get("/api/cycles/no-existe").status_code == 404


def test_cotizaciones_dicen_su_antiguedad(client, db_path, profile):
    """`age_seconds` es la medida de F2.1c: 'cada minuto' solo vale si el dato es
    de hace un minuto, y Yahoo sirve Europa con desfase."""
    seed_history(db_path, profile["id"])
    quotes = client.get("/api/quotes").json()

    assert quotes[0]["symbol"] == "SAN.MC"
    assert quotes[0]["age_seconds"] is not None


def test_estado_del_ingestor(client, db_path, profile):
    seed_history(db_path, profile["id"])
    estado = client.get("/api/ingest-status").json()

    assert estado["bars_stored"] == 0
    assert estado["quotes_stored"] == 1
    assert estado["consecutive_failures"] == 0
    assert estado["avg_latency_ms"] == 850
    assert estado["message"]


def test_sin_perfiles_activos_el_ingestor_esta_sano(client):
    """Dormir fuera de la ventana operativa no es una averia. Si el panel
    estuviera en rojo todas las noches, el rojo dejaria de significar algo."""
    estado = client.get("/api/ingest-status").json()
    assert estado["healthy"] is True


def test_mercados(client):
    mercados = {m["code"]: m for m in client.get("/api/markets").json()}

    assert mercados["eu"]["currency"] == "EUR"
    assert mercados["eu"]["session_open"] == "09:00"
    # La ventana operativa no es la sesion (FE.13): 09:15-17:45 sobre 09:00-17:30.
    assert mercados["eu"]["operating_open"] == "09:15"
    assert mercados["eu"]["operating_close"] == "17:45"
    assert mercados["us"]["operating_open"] == mercados["us"]["session_open"]


def test_perfil_inexistente_da_404_con_los_que_hay(client, profile):
    respuesta = client.get("/api/positions?profile=no-existe")
    assert respuesta.status_code == 404
    assert "europa-01" in respuesta.json()["detail"]


def test_se_puede_pedir_un_perfil_por_nombre_o_por_id(client, profile):
    por_nombre = client.get("/api/profiles/europa-01").json()
    por_id = client.get(f"/api/profiles/{profile['id']}").json()
    assert por_nombre["id"] == por_id["id"]


# ======================================================================
# F3.3 -- Endpoints de escritura
# ======================================================================

def test_no_se_pueden_crear_dos_perfiles_con_el_mismo_nombre(client, profile):
    respuesta = client.post("/api/profiles", json={"name": "europa-01", "market": "eu"})
    assert respuesta.status_code == 409


def test_el_sp500_entero_se_rechaza_sin_un_tope_explicito(client):
    """R2: son peticiones por minuto contra Yahoo desde una IP domestica. La
    misma regla que aplica `run.py new-profile`, porque es el mismo codigo."""
    respuesta = client.post("/api/profiles", json={"name": "us-01", "market": "us"})
    assert respuesta.status_code == 422
    assert "--watch" in respuesta.json()["detail"]

    con_tope = client.post(
        "/api/profiles", json={"name": "us-01", "market": "us", "watch": 50}
    )
    assert con_tope.status_code == 201
    assert con_tope.json()["watched_symbols"] == 50


def test_actualizar_parametros_devuelve_solo_lo_que_cambio(client, profile):
    """`update_settings` ignora los campos que llegan con el valor que ya tenian:
    un historial con filas que no cambian nada solo estorba."""
    pid = profile["id"]
    primera = client.patch(
        f"/api/profiles/{pid}/settings", json={"risk_profile": 8}
    ).json()
    assert primera["applied"] == ["risk_profile"]
    assert primera["limits"]["risk_per_trade_pct"] > 1.0

    segunda = client.patch(
        f"/api/profiles/{pid}/settings", json={"risk_profile": 8}
    ).json()
    assert segunda["applied"] == []


def test_el_historial_de_parametros_registra_el_cambio(client, profile):
    pid = profile["id"]
    client.patch(f"/api/profiles/{pid}/settings", json={"diversification": 9})
    historial = client.get(f"/api/profiles/{pid}/settings/history").json()

    cambio = next(f for f in historial["items"] if f["field"] == "diversification")
    assert cambio["old_value"] == "5" and cambio["new_value"] == "9"
    assert cambio["source"] == "ui"


def test_apagar_el_modo_avanzado_devuelve_el_mando_a_los_deslizadores(client, profile):
    """F6.5: si los numeros viejos siguieran ganando, apagar el interruptor no
    haria nada visible y se seguiria operando con limites ya descartados."""
    pid = profile["id"]
    client.patch(
        f"/api/profiles/{pid}/settings",
        json={"advanced_overrides": True, "max_open_positions": 2},
    )
    assert client.get(f"/api/profiles/{pid}/limits").json()["max_open_positions"] == 2

    client.patch(f"/api/profiles/{pid}/settings", json={"advanced_overrides": False})
    limites = client.get(f"/api/profiles/{pid}/limits").json()
    assert limites["max_open_positions"] == 13
    assert "max_open_positions" in limites["derived_fields"]


def test_un_parametro_desconocido_se_rechaza(client, profile):
    respuesta = client.patch(
        f"/api/profiles/{profile['id']}/settings", json={"inventado": 1}
    )
    assert respuesta.status_code == 422


def test_un_parametro_fuera_de_rango_se_rechaza(client, profile):
    respuesta = client.patch(
        f"/api/profiles/{profile['id']}/settings", json={"risk_profile": 42}
    )
    assert respuesta.status_code == 422


def test_no_se_puede_dejar_un_perfil_con_simbolos_de_otra_bolsa(client, profile):
    """La regla de FE.5, aplicada al guardar en lugar de al arrancar el ciclo.

    El sintoma sin la comprobacion es silencioso: el simbolo forastero no
    revienta, se queda con el cierre del dia anterior y el analista decide sobre
    datos rancios.
    """
    respuesta = client.patch(
        f"/api/profiles/{profile['id']}/settings", json={"market": "us"}
    )
    assert respuesta.status_code == 422
    assert "otra bolsa" in respuesta.json()["detail"]


def test_el_universo_en_vivo_rechaza_simbolos_forasteros(client, profile):
    respuesta = client.put(
        f"/api/profiles/{profile['id']}/universe", json={"symbols": ["AAPL", "SAN.MC"]}
    )
    assert respuesta.status_code == 422
    assert "AAPL" in respuesta.json()["detail"]


def test_duplicar_copia_parametros_y_universo_pero_no_historico(
    client, db_path, profile
):
    """El gesto central del experimento (F5.4): clonar, cambiar un parametro y
    comparar. Heredar el historico haria justamente que no se pudieran comparar.
    """
    seed_history(db_path, profile["id"])
    client.patch(f"/api/profiles/{profile['id']}/settings", json={"risk_profile": 9})

    copia = client.post(
        f"/api/profiles/{profile['id']}/duplicate", json={"name": "europa-02"}
    ).json()

    assert copia["settings"]["risk_profile"] == 9
    assert copia["watched_symbols"] == profile["watched_symbols"]
    assert copia["status"] == "draft"
    assert copia["metrics"]["cycles"] == 0
    assert copia["metrics"]["closed_trades"] == 0


def test_borrar_exige_repetir_el_nombre(client, profile):
    """Es la unica llamada de la API que destruye datos que costo semanas
    generar, y un DELETE a la URL equivocada es un gesto de un segundo."""
    sin_confirmar = client.delete(f"/api/profiles/{profile['id']}")
    assert sin_confirmar.status_code == 400

    mal = client.delete(f"/api/profiles/{profile['id']}?confirm=otro-nombre")
    assert mal.status_code == 400

    bien = client.delete(f"/api/profiles/{profile['id']}?confirm=europa-01")
    assert bien.status_code == 200
    assert client.get("/api/profiles").json() == []


def test_no_se_renombra_un_perfil_que_ya_tiene_historico(client, db_path, profile):
    """La cartera se llama igual que el perfil: renombrarlo dejaria el historico
    colgando de un nombre que ya no existe."""
    seed_history(db_path, profile["id"])
    respuesta = client.patch(
        f"/api/profiles/{profile['id']}", json={"name": "otro-nombre"}
    )
    assert respuesta.status_code == 409

    # Sin historico si se puede.
    nuevo = client.post("/api/profiles", json={"name": "europa-03", "market": "eu"}).json()
    assert client.patch(
        f"/api/profiles/{nuevo['id']}", json={"name": "europa-renombrado"}
    ).status_code == 200


def test_activar_y_pausar_un_perfil(client, profile):
    pid = profile["id"]
    assert client.patch(f"/api/profiles/{pid}", json={"status": "active"}).json()["status"] == "active"
    assert client.patch(f"/api/profiles/{pid}", json={"status": "paused"}).json()["status"] == "paused"
    assert client.patch(f"/api/profiles/{pid}", json={"status": "inventado"}).status_code == 422


def test_un_perfil_activo_entra_en_el_universo_del_ingestor(client, db_path, profile):
    """La trampa de FE.7: `universe_file` es lo que criba el screener y
    `profile_universe` es lo que el ingestor sigue. Son cosas distintas."""
    client.patch(f"/api/profiles/{profile['id']}", json={"status": "active"})
    estado = client.get("/api/ingest-status").json()

    assert estado["symbols_by_market"] == {"eu": 89}


# ======================================================================
# F3.4 -- Control de ciclos
# ======================================================================

class StubRunner:
    """Sustituye al `CycleRunner` real: los tests no lanzan subprocesos."""

    def __init__(self) -> None:
        self.started: list[tuple[str | None, bool]] = []
        self._running = False

    def start(self, *, profile=None, dry_run=False):
        self.started.append((profile, dry_run))
        self._running = True
        return True, "Ciclo lanzado."

    def stop(self):
        if not self._running:
            return False, "No hay ningun ciclo en marcha."
        self._running = False
        return True, "Se ha pedido la parada del ciclo."

    def status(self):
        return {
            "enabled": True, "running": self._running, "profile": None,
            "dry_run": False, "started_at": None, "finished_at": None,
            "returncode": None, "lines": [], "stage": "inactivo",
            "elapsed_seconds": None,
        }

    def lines_since(self, index):
        return 0, []


def test_lanzar_un_ciclo_pasa_el_perfil(client, profile):
    runner = StubRunner()
    client.app_ref.state.runner = runner

    respuesta = client.post(
        "/api/cycles/run", json={"profile": profile["id"], "dry_run": True}
    )
    assert respuesta.status_code == 200
    # Se pasa el **nombre**, que es lo que entiende `run.py --profile`.
    assert runner.started == [("europa-01", True)]


def test_no_se_lanza_un_ciclo_si_ya_hay_uno_corriendo(client, db_path, profile):
    """Se mira tambien la tabla `cycles`, no solo el subproceso propio: el
    planificador puede tener uno en marcha del que este proceso no sabe nada, y
    dos ciclos sobre la misma cartera se pisarian las posiciones."""
    client.app_ref.state.runner = StubRunner()
    with Database(path=db_path) as db:
        portfolio_id = db.get_profile(profile["id"])["portfolio_id"]
        db.start_cycle(
            portfolio_id=portfolio_id, equity_start=1, cash_start=1,
            market_open=True, symbols=[], llm_model="stub",
        )

    respuesta = client.post("/api/cycles/run", json={})
    assert respuesta.status_code == 409
    assert "planificador" in respuesta.json()["detail"]


def test_parar_sin_ciclo_en_marcha(client):
    client.app_ref.state.runner = StubRunner()
    assert client.post("/api/cycles/stop").status_code == 409


def test_con_los_controles_apagados_no_hay_forma_de_disparar_nada(db_path):
    """F3.8: la API sirve datos igual, pero sin boton."""
    app = create_app(ApiConfig(db_path=db_path, controls=False))
    with TestClient(app) as sin_controles:
        assert sin_controles.post("/api/cycles/run", json={}).status_code == 403
        assert sin_controles.post("/api/cycles/stop").status_code == 403
        estado = sin_controles.get("/api/cycles/control/status").json()
        assert estado["enabled"] is False
        # Y lo que solo lee sigue funcionando.
        assert sin_controles.get("/api/profiles").status_code == 200


# ======================================================================
# F3.5 -- SSE
# ======================================================================

def _parse_sse(texto: str) -> dict[str, dict]:
    eventos: dict[str, dict] = {}
    nombre = None
    for linea in texto.splitlines():
        if linea.startswith("event: "):
            nombre = linea[7:].strip()
        elif linea.startswith("data: ") and nombre:
            eventos[nombre] = json.loads(linea[6:])
    return eventos


@pytest.fixture
def stream_client(db_path):
    """Cliente con un stream que se cierra solo casi enseguida.

    `stream_max_seconds` existe en produccion por higiene —EventSource reconecta
    solo, asi que cortar de vez en cuando devuelve recursos— y aqui ademas hace
    el test posible: un generador infinito deja colgado al `TestClient` en el
    cierre, porque en memoria nunca llega a haber una desconexion que detectar.
    """
    app = create_app(
        ApiConfig(
            db_path=db_path, controls=True,
            stream_interval=0.5, stream_max_seconds=5.0,
        )
    )
    with TestClient(app) as test_client:
        test_client.app_ref = app
        yield test_client


def test_el_stream_manda_el_estado_inicial(stream_client, db_path):
    """Un cliente que se conecta con el mercado parado tiene que ver algo.

    Sin el volcado inicial no habria forma de distinguir "no ha cambiado nada"
    de "la conexion esta rota".
    """
    creado = stream_client.post(
        "/api/profiles", json={"name": "europa-01", "market": "eu"}
    ).json()
    seed_history(db_path, creado["id"])

    respuesta = stream_client.get("/api/stream")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/event-stream")

    eventos = _parse_sse(respuesta.text)
    assert eventos["quotes"]["quotes"][0]["symbol"] == "SAN.MC"
    assert "healthy" in eventos["ingest"]
    assert eventos["cycle"]["running"] is False
    # Y se cierra por edad, avisando de que hay que reconectar.
    assert eventos["bye"]["reconnect"] is True


def test_el_stream_filtra_por_simbolo(stream_client, db_path):
    creado = stream_client.post(
        "/api/profiles", json={"name": "europa-01", "market": "eu"}
    ).json()
    seed_history(db_path, creado["id"])

    eventos = _parse_sse(stream_client.get("/api/stream?symbols=ITX.MC").text)
    assert eventos["quotes"]["quotes"] == []


# ======================================================================
# F3.6 -- Modelos
# ======================================================================

def test_settings_update_cubre_las_columnas_reales_de_agent_settings(db_path):
    """El formulario de F6.8 tiene que poder tocar todo lo que hay en la tabla.

    Sin esta prueba, una columna nueva en `agent_settings` seria inalcanzable
    desde la interfaz y nadie se enteraria hasta buscarla en la pantalla.
    """
    with Database(path=db_path) as db:
        columnas = db._columns("agent_settings") - {"profile_id", "updated_at"}

    del_modelo = set(SettingsUpdate.model_fields)
    assert del_modelo == columnas, (
        f"sobran en el modelo: {sorted(del_modelo - columnas)}; "
        f"faltan: {sorted(columnas - del_modelo)}"
    )


def test_las_tablas_escribibles_existen_de_verdad(db_path):
    """`WRITABLE` se escribe a mano; una errata la dejaria sin efecto."""
    with Database(path=db_path) as db:
        reales = {
            row["name"]
            for row in db.query("select name from sqlite_master where type = 'table'")
        }
    assert set(WRITABLE) <= reales


def test_el_openapi_se_publica_y_los_tipos_se_generan(client):
    esquema = client.get("/openapi.json").json()
    assert "/api/profiles" in esquema["paths"]

    from tools.gen_api_types import render

    typescript = render(esquema)
    assert "export interface ProfileSummary" in typescript
    assert "export interface ApiOperations" in typescript
    # La clave de API sale enmascarada tambien en el contrato.
    assert "llm_api_key_masked" in typescript


# ======================================================================
# F3.7 / F3.8 -- Estaticos y escucha
# ======================================================================

def test_una_ruta_de_api_que_no_existe_da_404_en_json(client):
    """Sin esta excepcion, la vuelta a index.html devolveria el HTML de la
    aplicacion con un 200 y el sintoma seria un JSON.parse fallando tres capas
    mas abajo."""
    respuesta = client.get("/api/inventado")
    assert respuesta.status_code == 404
    assert respuesta.headers["content-type"].startswith("application/json")


def test_las_rutas_del_spa_caen_en_el_frontend(client):
    respuesta = client.get("/perfiles/europa-01")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/html")


def test_sin_build_del_frontend_se_dice_que_falta(tmp_path, db_path):
    """Un 404 pelado se leeria como una averia; esto dice que llega en F4.

    El `app_dist` se apunta a un directorio vacio a proposito. Antes usaba el
    cliente por defecto, o sea el `app/dist` real, y el test decia la verdad solo
    mientras nadie hubiera compilado el frontend: en cuanto se hizo `npm run
    build` (F4 tramo A) empezo a fallar en la maquina de desarrollo y a pasar en
    CI, que es la peor combinacion posible.
    """
    vacio = tmp_path / "sin-build"
    vacio.mkdir()

    app = create_app(ApiConfig(db_path=db_path, app_dist=vacio))
    with TestClient(app) as sin_build:
        assert "app/dist" in sin_build.get("/").text


def test_el_spa_sirve_los_ficheros_del_build(tmp_path, db_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    app = create_app(ApiConfig(db_path=db_path, app_dist=dist))
    with TestClient(app) as con_build:
        assert con_build.get("/assets/app.js").text == "console.log(1)"
        # Una ruta del router del SPA no es un fichero: le toca el index.
        assert con_build.get("/decisiones").text == "<html>app</html>"


def test_por_defecto_escucha_solo_en_loopback(monkeypatch):
    """F3.8: son datos de una cuenta de inversion en la maquina de uno."""
    monkeypatch.delenv("API_HOST", raising=False)
    monkeypatch.delenv("API_CONTROLS", raising=False)
    config = ApiConfig.load(db_path="data/trading.db")

    assert config.host == "127.0.0.1"
    assert config.controls is True
