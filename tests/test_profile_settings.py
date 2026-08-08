"""F6.4 y F6.3: los parametros del ciclo salen del perfil, y quedan registrados.

Antes de F6.4 el ciclo leia sus parametros del `.env`. Eso hacia que un
experimento no fuera reproducible: el fichero se editaba y el historico anterior
quedaba sin explicacion. Lo que se prueba aqui es esa cadena entera:

  * una fila de `agent_settings` produce los `Settings` correctos,
  * lo que no cuadra se rechaza **al resolver**, con el nombre del perfil, y no
    tres funciones despues dentro de yfinance,
  * elegir perfil nunca es una adivinanza,
  * el ciclo deja copia de sus parametros (F6.3) y esa copia no lleva secretos,
  * una base que ya existia recibe las columnas nuevas.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.config import ConfigError, Infra, Settings
from src.db import ADDED_COLUMNS, Database
from src.profile_settings import (
    cycle_settings,
    import_env_profile,
    resolve_settings,
    select_profile,
)
from src.risk_presets import DERIVED_FIELDS

INFRA = Infra(
    db_path="data/no-se-usa.db",
    log_level="CRITICAL",
    model_api_key="clave-de-prueba",
    model_base_url="http://stub",
)


@pytest.fixture
def perfil(db):
    """Un perfil activo con universo, listo para resolver."""
    profile_id = db.create_profile(name="experimento-01", description="control")
    db.set_profile_universe(profile_id, ["AAPL", "MSFT"])
    db.set_profile_status(profile_id, "active")
    return profile_id


# -- Resolucion --------------------------------------------------------------


def test_los_settings_salen_de_la_fila_del_perfil(db, perfil):
    db.update_settings(perfil, {
        "initial_budget": 25_000.0,
        "llm_model": "otro/modelo",
        "llm_temperature": 0.7,
        "bar_interval": "1h",
        "lookback_days": 300,
        "dry_run": 1,
        "sim_slippage_bps": 12.0,
    })

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.initial_budget == 25_000.0
    assert settings.llm_model == "otro/modelo"
    assert settings.llm_temperature == 0.7
    assert settings.bar_interval == "1h"
    assert settings.lookback_days == 300
    assert settings.dry_run is True
    assert settings.sim_slippage_bps == 12.0
    assert settings.watchlist == ("AAPL", "MSFT")
    assert settings.profile_id == perfil
    # La cartera se llama igual que el perfil: es como la encuentra el ciclo.
    assert settings.portfolio_name == "experimento-01"


def test_la_infraestructura_sigue_viniendo_del_entorno(db, perfil):
    """`agent_settings` no guarda rutas ni claves: eso es de la maquina."""
    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.db_path == INFRA.db_path
    assert settings.model_api_key == INFRA.model_api_key
    assert settings.log_level == INFRA.log_level


def test_los_limites_de_riesgo_pasan_por_los_deslizadores(db, perfil):
    db.update_settings(perfil, {"risk_profile": 10, "diversification": 1})

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.risk.risk_per_trade_pct == pytest.approx(3.0)
    assert settings.risk.max_open_positions == 3
    assert settings.risk_summary and "10/10" in settings.risk_summary


def test_el_modo_avanzado_llega_hasta_el_risk_manager(db, perfil):
    db.update_settings(perfil, {
        "advanced_overrides": 1, "max_open_positions": 2, "min_conviction": 90,
    })

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.risk.max_open_positions == 2
    assert settings.risk.min_conviction == 90


def test_el_screener_se_arma_desde_el_perfil(db, perfil):
    db.update_settings(perfil, {
        "universe_file": "universe/sp500.txt",
        "screener_top_n": 30,
        "screener_mode": "random",
        "screener_min_price": 12.0,
    })

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.screener.enabled
    assert settings.screener.top_n == 30
    assert settings.screener.mode == "random"
    assert settings.screener.min_price == 12.0


def test_sin_universo_no_hay_embudo(db, perfil):
    settings = resolve_settings(db, perfil, infra=INFRA)

    assert not settings.screener.enabled


def test_con_fichero_de_universo_la_watchlist_es_opcional(db):
    """El universo sustituye a la watchlist, asi que un perfil sin simbolos
    propios pero con fichero si es utilizable."""
    profile_id = db.create_profile(name="embudo")
    db.update_settings(profile_id, {"universe_file": "universe/sp500.txt"})

    settings = resolve_settings(db, profile_id, infra=INFRA)

    assert settings.watchlist == ()
    assert settings.screener.enabled


# -- Lo que se rechaza al resolver -------------------------------------------


def test_un_perfil_sin_nada_que_analizar_se_rechaza(db):
    profile_id = db.create_profile(name="vacio")

    with pytest.raises(ConfigError, match="nada que analizar"):
        resolve_settings(db, profile_id, infra=INFRA)


def test_el_intervalo_de_un_minuto_no_sirve_para_el_ciclo(db, perfil):
    """`agent_settings.bar_interval` admite '1m' porque la columna la comparte
    con el ingestor. El ciclo no: con barras de un minuto no hay historico para
    los indicadores largos."""
    db.update_settings(perfil, {"bar_interval": "1m"})

    with pytest.raises(ConfigError, match="ingestor"):
        resolve_settings(db, perfil, infra=INFRA)


def test_el_error_nombra_el_perfil(db, perfil):
    """Con varios experimentos a la vez, un error sin nombre obliga a adivinar
    cual de ellos esta mal configurado."""
    db.update_settings(perfil, {"bar_interval": "1m"})

    with pytest.raises(ConfigError, match="experimento-01"):
        resolve_settings(db, perfil, infra=INFRA)


def test_un_perfil_inexistente_se_rechaza(db):
    with pytest.raises(ConfigError, match="no existe"):
        resolve_settings(db, "no-existe", infra=INFRA)


def test_sin_clave_de_modelo_falla_al_resolver(db, perfil):
    """Mejor aqui que dentro de la primera llamada al LLM, con el ciclo ya
    abierto y filas escritas."""
    sin_clave = Infra(db_path="x.db", model_api_key="")

    with pytest.raises(ConfigError, match="NVIDIA_API_KEY"):
        resolve_settings(db, perfil, infra=sin_clave)


# -- Eleccion de perfil ------------------------------------------------------


def test_sin_perfiles_el_mensaje_explica_como_empezar(db):
    with pytest.raises(ConfigError, match="import-profile"):
        select_profile(db)


def test_un_unico_perfil_activo_se_elige_solo(db, perfil):
    assert select_profile(db) == perfil


def test_con_varios_activos_hay_que_elegir(db, perfil):
    """Ejecutar un ciclo contra el experimento equivocado ensucia dos historicos
    a la vez y no se puede deshacer."""
    otro = db.create_profile(name="experimento-02")
    db.set_profile_status(otro, "active")

    with pytest.raises(ConfigError, match="--profile"):
        select_profile(db)


def test_sin_ninguno_activo_tampoco_se_adivina(db):
    db.create_profile(name="borrador")

    with pytest.raises(ConfigError, match="Ningun perfil esta activo"):
        select_profile(db)


def test_el_nombre_manda_sobre_el_estado(db, perfil):
    """Se puede operar a mano contra un perfil pausado; lo que no se puede es
    que se elija sin decirlo."""
    pausado = db.create_profile(name="pausado")

    assert select_profile(db, name="pausado") == pausado


def test_un_nombre_que_no_existe_lista_los_que_si(db, perfil):
    with pytest.raises(ConfigError, match="experimento-01"):
        select_profile(db, name="typo")


# -- Importacion del .env ----------------------------------------------------


def _env_settings(**overrides) -> Settings:
    from src.config import RiskLimits, ScreenerSettings

    base = dict(
        sim_slippage_bps=5.0, sim_commission=0.0,
        model_api_key="clave", model_base_url="http://stub",
        llm_model="meta/llama-3.3-70b-instruct", llm_temperature=0.2,
        llm_timeout_seconds=120.0, llm_max_retries=3,
        db_path="data/trading.db", portfolio_name="del-env",
        initial_budget=10_000.0, watchlist=("AAPL", "TSLA"),
        lookback_days=200, dry_run=False, log_level="INFO",
        bar_interval="1d", skip_when_market_closed=True,
        risk=RiskLimits(risk_per_trade_pct=1.5, max_open_positions=7),
        screener=ScreenerSettings(),
    )
    base.update(overrides)
    return Settings(**base)


def test_importar_el_env_deja_un_perfil_utilizable(db):
    profile_id = import_env_profile(db, _env_settings())

    assert db.get_profile(profile_id)["status"] == "active"
    assert db.get_profile_universe(profile_id) == ["AAPL", "TSLA"]
    settings = resolve_settings(db, profile_id, infra=INFRA)
    assert settings.initial_budget == 10_000.0
    assert settings.watchlist == ("AAPL", "TSLA")


def test_importar_conserva_los_limites_exactos_del_env(db):
    """Se importan como modo avanzado a proposito.

    El `.env` traia nueve numeros explicitos; sustituirlos por los que salen de
    `risk_profile=5` cambiaria el comportamiento del agente en la misma
    operacion en la que solo se pretendia mover la configuracion de sitio.
    """
    profile_id = import_env_profile(db, _env_settings())

    fila = db.get_settings(profile_id)
    assert fila["advanced_overrides"] == 1
    assert fila["risk_per_trade_pct"] == pytest.approx(1.5)
    assert fila["max_open_positions"] == 7

    settings = resolve_settings(db, profile_id, infra=INFRA)
    assert settings.risk.risk_per_trade_pct == pytest.approx(1.5)
    assert settings.risk.max_open_positions == 7


def test_el_nombre_explicito_gana_al_del_env(db):
    profile_id = import_env_profile(db, _env_settings(), name="otro-nombre")

    assert db.get_profile(profile_id)["name"] == "otro-nombre"


def test_importar_dos_veces_el_mismo_nombre_falla(db):
    from src.db import DatabaseError

    import_env_profile(db, _env_settings())

    with pytest.raises(DatabaseError, match="Ya existe"):
        import_env_profile(db, _env_settings())


# -- F6.3: copia de los parametros en el ciclo -------------------------------


def test_el_snapshot_no_lleva_secretos():
    """El historico se exporta y se abre con DB Browser: una clave dentro de una
    columna JSON no se ve venir."""
    datos = _env_settings(model_api_key="nvapi-secreto").snapshot()

    assert "model_api_key" not in datos
    assert "nvapi-secreto" not in str(datos)
    assert "db_path" not in datos


def test_el_snapshot_lleva_los_limites_efectivos():
    datos = _env_settings().snapshot()

    assert datos["risk"]["risk_per_trade_pct"] == pytest.approx(1.5)
    assert datos["initial_budget"] == 10_000.0
    assert datos["watchlist"] == ["AAPL", "TSLA"]


def test_el_ciclo_guarda_y_devuelve_sus_parametros(db, perfil):
    portfolio_id = db.get_profile(perfil)["portfolio_id"]
    settings = resolve_settings(db, perfil, infra=INFRA)

    cycle_id = db.start_cycle(
        portfolio_id=portfolio_id, equity_start=10_000, cash_start=10_000,
        market_open=True, symbols=["AAPL"], llm_model=settings.llm_model,
        settings=settings.snapshot(),
    )

    guardado = cycle_settings(db, cycle_id)
    assert guardado is not None
    assert guardado["profile_id"] == perfil
    assert guardado["risk"]["max_open_positions"] == settings.risk.max_open_positions


def test_un_ciclo_sin_copia_devuelve_none(db, perfil):
    """Los ciclos anteriores a F6.3 no la llevan. Es informacion que falta, no un
    cero: comparar experimentos exige distinguir "corrio con estos ajustes" de
    "no se sabe con que ajustes corrio".
    """
    portfolio_id = db.get_profile(perfil)["portfolio_id"]
    cycle_id = db.start_cycle(
        portfolio_id=portfolio_id, equity_start=1, cash_start=1,
        market_open=False, symbols=[], llm_model="x",
    )

    assert cycle_settings(db, cycle_id) is None


# -- Coherencia entre esquema y codigo ---------------------------------------


def test_todo_limite_derivable_tiene_columna_anulable(db, perfil):
    """Si un limite de `RiskLimits` no tiene columna, el modo avanzado no puede
    fijarlo y el formulario de F6.8 tendria un campo que no guarda nada."""
    fila = db.get_settings(perfil)

    for campo in DERIVED_FIELDS:
        assert campo in fila, f"falta la columna {campo} en agent_settings"
        assert fila[campo] is None, f"{campo} deberia nacer NULL"


def test_las_columnas_nuevas_aparecen_en_una_base_que_ya_existia(tmp_path):
    """`create table if not exists` no anade columnas a una tabla existente.

    Sin la migracion, una columna nueva funcionaria en una base recien creada y
    faltaria en la que ya esta corriendo, que es el peor reparto posible.
    """
    ruta = tmp_path / "vieja.db"
    nuevas = ADDED_COLUMNS["agent_settings"]

    with Database(path=ruta) as database:
        database.create_profile(name="anterior")

    # Se simula la base de antes de F6.4 quitando las columnas nuevas.
    plana = sqlite3.connect(ruta)
    for columna in nuevas:
        plana.execute(f"alter table agent_settings drop column {columna}")
    plana.commit()
    restantes = {row[1] for row in plana.execute("pragma table_info(agent_settings)")}
    plana.close()
    assert not (set(nuevas) & restantes), "la simulacion no quito las columnas"

    with Database(path=ruta) as database:
        fila = database.get_settings(database.list_profiles()[0]["id"])

    for columna in nuevas:
        assert columna in fila, f"la migracion no anadio {columna}"


def test_la_migracion_es_idempotente(tmp_path):
    """Se ejecuta en cada arranque: la segunda vez no debe hacer nada."""
    ruta = tmp_path / "repetida.db"

    with Database(path=ruta) as database:
        antes = database._columns("agent_settings")
    with Database(path=ruta) as database:
        despues = database._columns("agent_settings")

    assert antes == despues
