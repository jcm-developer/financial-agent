"""Perfiles de experimento, parametros del agente y datos de mercado en vivo.

Cubre las tablas que introduce F1. Lo que mas se prueba aqui no es el camino
feliz sino tres invariantes que, si se rompen, arruinan el experimento en
silencio:

  * borrar un perfil arrastra todo su historico (si no, quedan huerfanos que
    contaminan las metricas del siguiente),
  * el historial de parametros registra los cambios reales y solo esos,
  * las escrituras del ingestor son idempotentes (la barra del minuto en curso
    se reescribe cada minuto hasta que cierra).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.db import DatabaseError


def _iso(moment: datetime) -> str:
    return moment.isoformat()


# -- Perfiles ---------------------------------------------------------------


def test_crear_perfil_deja_parametros_y_cartera(db):
    """Un perfil nace utilizable: con parametros por defecto y con cartera."""
    profile_id = db.create_profile(name="experimento-01", description="control")

    profile = db.get_profile(profile_id)
    assert profile["name"] == "experimento-01"
    assert profile["status"] == "draft"
    assert profile["portfolio_id"], "el perfil debe traer cartera desde el minuto uno"

    settings = db.get_settings(profile_id)
    assert settings["llm_provider"] == "nvidia"
    assert settings["risk_profile"] == 5
    assert settings["diversification"] == 5


def test_limites_duros_nacen_nulos(db):
    """NULL significa 'derivalo de los sliders'.

    Si nacieran con numeros, mover el slider de riesgo no cambiaria nada y el
    usuario no entenderia por que.
    """
    profile_id = db.create_profile(name="p")
    settings = db.get_settings(profile_id)

    for campo in (
        "risk_per_trade_pct", "max_position_pct", "max_total_exposure_pct",
        "max_open_positions", "max_daily_loss_pct", "min_conviction",
        "stop_atr_multiple", "min_reward_risk", "min_order_notional",
    ):
        assert settings[campo] is None, f"{campo} deberia nacer NULL"
    assert settings["advanced_overrides"] == 0


def test_nombre_de_perfil_es_unico(db):
    db.create_profile(name="repetido")
    with pytest.raises(DatabaseError, match="Ya existe"):
        db.create_profile(name="repetido")


def test_nombre_vacio_se_rechaza(db):
    with pytest.raises(DatabaseError, match="nombre"):
        db.create_profile(name="   ")


def test_borrar_perfil_arrastra_su_historico(db):
    """La cascada es lo que permite tirar un experimento fallido de una vez.

    Sin ella quedarian ciclos y decisiones sin dueno, que luego aparecen en las
    vistas de analisis y corrompen la comparacion entre experimentos.
    """
    profile_id = db.create_profile(name="a-borrar")
    portfolio_id = db.get_profile(profile_id)["portfolio_id"]

    cycle_id = db.start_cycle(
        portfolio_id=portfolio_id, equity_start=10_000, cash_start=10_000,
        market_open=True, symbols=["AAPL"], llm_model="test",
    )
    db.set_profile_universe(profile_id, ["AAPL", "MSFT"])
    db.update_settings(profile_id, {"risk_profile": 8})

    assert db.query("select count(1) n from cycles")[0]["n"] == 1

    db.delete_profile(profile_id)

    for tabla in ("profiles", "portfolios", "cycles", "agent_settings",
                  "agent_settings_history", "profile_universe"):
        restantes = db.query(f"select count(1) n from {tabla}")[0]["n"]
        assert restantes == 0, f"{tabla} conservo filas huerfanas: {restantes}"
    assert cycle_id  # el ciclo existio antes del borrado


def test_borrar_perfil_arrastra_el_libro_del_broker_simulado(db):
    """`sim_accounts` no cuelga de `portfolios` con FK: su id **es** el
    portfolio_id, pero sin `references`, asi que la cascada no lo alcanza sola.

    Se comprueba aparte porque el sintoma es mudo: el perfil desaparece de todas
    las pantallas y su efectivo, sus posiciones simuladas y sus ejecuciones
    siguen ahi ocupando sitio, sin nada que los relacione con nadie.
    """
    from src.sim_broker import SimBroker

    profile_id = db.create_profile(name="a-borrar")
    portfolio_id = db.get_profile(profile_id)["portfolio_id"]
    SimBroker(
        database=db, portfolio_id=portfolio_id, initial_cash=10_000.0,
        slippage_bps=0.0, commission_per_order=0.0,
    ).get_account_state()

    assert db.query("select count(1) n from sim_accounts")[0]["n"] == 1

    db.delete_profile(profile_id)

    for tabla in ("sim_accounts", "sim_positions", "sim_fills"):
        restantes = db.query(f"select count(1) n from {tabla}")[0]["n"]
        assert restantes == 0, f"{tabla} conservo filas huerfanas: {restantes}"


def test_estado_invalido_se_rechaza(db):
    profile_id = db.create_profile(name="p")
    with pytest.raises(DatabaseError, match="Estado invalido"):
        db.set_profile_status(profile_id, "encendido")


def test_archivar_sella_la_fecha_y_lo_saca_del_listado(db):
    profile_id = db.create_profile(name="viejo")
    db.set_profile_status(profile_id, "archived")

    assert db.get_profile(profile_id)["archived_at"] is not None
    assert db.list_profiles() == []
    assert len(db.list_profiles(include_archived=True)) == 1


# -- Parametros --------------------------------------------------------------


def test_cambiar_parametro_deja_rastro(db):
    profile_id = db.create_profile(name="p")

    cambiados = db.update_settings(
        profile_id, {"risk_profile": 9, "diversification": 2}, source="ui"
    )

    assert cambiados == ["diversification", "risk_profile"]
    assert db.get_settings(profile_id)["risk_profile"] == 9

    historial = db.settings_history(profile_id)
    assert len(historial) == 2
    riesgo = next(h for h in historial if h["field"] == "risk_profile")
    assert (riesgo["old_value"], riesgo["new_value"]) == ("5", "9")
    assert riesgo["source"] == "ui"


def test_reescribir_el_mismo_valor_no_ensucia_el_historial(db):
    """El historial existe para explicar cambios de comportamiento.

    Una fila que no cambia nada solo hace mas dificil encontrar la que si.
    """
    profile_id = db.create_profile(name="p")
    db.update_settings(profile_id, {"risk_profile": 7})

    cambiados = db.update_settings(profile_id, {"risk_profile": 7})

    assert cambiados == []
    assert len(db.settings_history(profile_id)) == 1


def test_parametro_desconocido_se_rechaza(db):
    """Un nombre de campo mal escrito debe fallar, no guardarse en silencio."""
    profile_id = db.create_profile(name="p")
    with pytest.raises(DatabaseError, match="desconocidos"):
        db.update_settings(profile_id, {"risk_profil": 9})


def test_no_se_puede_colar_sql_por_el_nombre_del_campo(db):
    """Los nombres de columna no admiten placeholder, asi que se validan aparte."""
    profile_id = db.create_profile(name="p")
    with pytest.raises(DatabaseError, match="desconocidos"):
        db.update_settings(profile_id, {"risk_profile = 1, llm_model": "x"})


def test_parametros_fuera_de_rango_los_para_el_esquema(db):
    profile_id = db.create_profile(name="p")
    with pytest.raises(DatabaseError):
        db.update_settings(profile_id, {"risk_profile": 11})


def test_settings_de_perfil_inexistente(db):
    with pytest.raises(DatabaseError, match="no tiene parametros"):
        db.get_settings("no-existe")


# -- Universo ----------------------------------------------------------------


def test_universo_se_normaliza_y_se_reemplaza(db):
    profile_id = db.create_profile(name="p")

    db.set_profile_universe(profile_id, [" aapl ", "MSFT", "aapl", ""])
    assert db.get_profile_universe(profile_id) == ["AAPL", "MSFT"]

    db.set_profile_universe(profile_id, ["NVDA"])
    assert db.get_profile_universe(profile_id) == ["NVDA"]


def test_universo_activo_solo_mira_perfiles_activos(db):
    activo = db.create_profile(name="activo")
    pausado = db.create_profile(name="pausado")
    db.set_profile_universe(activo, ["AAPL"])
    db.set_profile_universe(pausado, ["TSLA"])
    db.set_profile_status(activo, "active")

    assert db.active_universe() == ["AAPL"]


def test_universo_activo_incluye_posiciones_abiertas(db):
    """Una posicion abierta necesita precio aunque su simbolo salga del universo.

    Si no, el agente se quedaria sin poder valorar ni cerrar lo que ya tiene.
    """
    profile_id = db.create_profile(name="p")
    portfolio_id = db.get_profile(profile_id)["portfolio_id"]
    db.set_profile_universe(profile_id, ["AAPL"])
    db.set_profile_status(profile_id, "active")

    db.execute(
        "insert into positions (id, portfolio_id, symbol, status, qty, entry_price, "
        "opened_at) values ('x', ?, 'TSLA', 'open', 10, 100, ?)",
        (portfolio_id, _iso(datetime.now(timezone.utc))),
    )

    assert db.active_universe() == ["AAPL", "TSLA"]


# -- Datos de mercado en vivo ------------------------------------------------


def test_quotes_live_no_crece(db):
    """Una fila por simbolo, se reemplaza. Si creciera, seria un historico
    duplicado de bars_1m."""
    ahora = datetime.now(timezone.utc)

    db.upsert_quotes([{"symbol": "AAPL", "price": 100.0, "as_of": _iso(ahora)}])
    db.upsert_quotes([{"symbol": "AAPL", "price": 101.5, "as_of": _iso(ahora)}])

    assert db.query("select count(1) n from quotes_live")[0]["n"] == 1
    assert db.latest_quotes()["AAPL"]["price"] == 101.5


def test_bars_1m_reescribe_la_barra_en_curso(db):
    """La barra del minuto actual cambia mientras el mercado sigue abierto.

    Por eso `insert or replace` y no `insert or ignore`: con ignore, el precio de
    cierre del minuto se quedaria congelado en el primer valor visto.
    """
    ts = _iso(datetime(2026, 8, 7, 15, 30, tzinfo=timezone.utc))
    barra = {"symbol": "AAPL", "ts": ts, "open": 100, "high": 100,
             "low": 100, "close": 100, "volume": 500}

    db.upsert_bars_1m([barra])
    db.upsert_bars_1m([{**barra, "high": 103, "close": 102, "volume": 1500}])

    filas = db.query("select * from bars_1m")
    assert len(filas) == 1
    assert filas[0]["close"] == 102
    assert filas[0]["volume"] == 1500


def test_prune_respeta_la_ventana(db):
    ahora = datetime.now(timezone.utc)
    db.upsert_bars_1m([
        {"symbol": "AAPL", "ts": _iso(ahora - timedelta(days=100)),
         "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0},
        {"symbol": "AAPL", "ts": _iso(ahora - timedelta(days=1)),
         "open": 2, "high": 2, "low": 2, "close": 2, "volume": 0},
    ])

    borradas = db.prune_bars_1m(keep_days=90)

    assert borradas == 1
    assert db.query("select count(1) n from bars_1m")[0]["n"] == 1


def test_prune_exige_ventana_positiva(db):
    """keep_days=0 borraria el historico entero de un tiron."""
    with pytest.raises(DatabaseError, match="al menos 1"):
        db.prune_bars_1m(keep_days=0)


def test_ingest_run_se_abre_y_se_cierra(db):
    run_id = db.start_ingest_run(symbols_requested=50)
    db.finish_ingest_run(
        run_id, symbols_ok=48, symbols_failed=2, latency_ms=1550, rate_limited=False
    )

    run = db.ingest_health(limit=1)[0]
    assert (run["symbols_ok"], run["symbols_failed"]) == (48, 2)
    assert run["latency_ms"] == 1550
    assert run["finished_at"] is not None


def test_ingest_health_devuelve_lo_mas_reciente_primero(db):
    for _ in range(3):
        db.finish_ingest_run(
            db.start_ingest_run(symbols_requested=10),
            symbols_ok=10, symbols_failed=0, latency_ms=100,
        )

    salud = db.ingest_health(limit=2)
    assert len(salud) == 2
    assert salud[0]["id"] > salud[1]["id"]


def test_escrituras_vacias_no_rompen(db):
    """El ingestor puede terminar un tick sin nada que escribir (mercado en
    calma, o todos los simbolos fallidos)."""
    assert db.upsert_quotes([]) == 0
    assert db.upsert_bars_1m([]) == 0
