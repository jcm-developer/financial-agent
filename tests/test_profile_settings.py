"""F6.4 and F6.3: the cycle's parameters come from the profile, and get recorded.

Before F6.4 the cycle read its parameters from the `.env`. That made an
experiment irreproducible: the file was edited and the earlier history was left
without an explanation. What is tested here is that whole chain:

  * a row of `agent_settings` produces the right `Settings`,
  * what does not add up is refused **while resolving**, with the profile's name,
    and not three functions later inside yfinance,
  * choosing a profile is never guesswork,
  * the cycle leaves a copy of its parameters (F6.3) and that copy carries no
    secrets,
  * a database that already existed receives the new columns.
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
    """An active profile with a universe, ready to resolve."""
    profile_id = db.create_profile(name="experimento-01", description="control")
    db.set_profile_universe(profile_id, ["AAPL", "MSFT"])
    db.set_profile_status(profile_id, "active")
    return profile_id


# -- Resolucion --------------------------------------------------------------


def test_the_settings_come_from_the_profiles_row(db, perfil):
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
    # The book is named after the profile: that is how the cycle finds it.
    assert settings.portfolio_name == "experimento-01"


def test_the_infrastructure_still_comes_from_the_environment(db, perfil):
    """`agent_settings` stores no paths and no keys: that belongs to the machine."""
    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.db_path == INFRA.db_path
    assert settings.model_api_key == INFRA.model_api_key
    assert settings.log_level == INFRA.log_level


def test_the_risk_limits_go_through_the_sliders(db, perfil):
    db.update_settings(perfil, {"risk_profile": 10, "diversification": 1})

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.risk.risk_per_trade_pct == pytest.approx(3.0)
    assert settings.risk.max_open_positions == 3
    assert settings.risk_summary and "10/10" in settings.risk_summary


def test_advanced_mode_reaches_the_risk_manager(db, perfil):
    db.update_settings(perfil, {
        "advanced_overrides": 1, "max_open_positions": 2, "min_conviction": 90,
    })

    settings = resolve_settings(db, perfil, infra=INFRA)

    assert settings.risk.max_open_positions == 2
    assert settings.risk.min_conviction == 90


def test_the_screener_is_assembled_from_the_profile(db, perfil):
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


def test_with_no_universe_there_is_no_funnel(db, perfil):
    settings = resolve_settings(db, perfil, infra=INFRA)

    assert not settings.screener.enabled


def test_with_a_universe_file_the_watchlist_is_optional(db):
    """The universe replaces the watchlist, so a profile with no symbols of its
    own but with a file is usable."""
    profile_id = db.create_profile(name="embudo")
    db.update_settings(profile_id, {"universe_file": "universe/sp500.txt"})

    settings = resolve_settings(db, profile_id, infra=INFRA)

    assert settings.watchlist == ()
    assert settings.screener.enabled


# -- What is refused while resolving -----------------------------------------


def test_a_profile_with_nothing_to_analyse_is_refused(db):
    profile_id = db.create_profile(name="vacio")

    with pytest.raises(ConfigError, match="nada que analizar"):
        resolve_settings(db, profile_id, infra=INFRA)


def test_the_one_minute_interval_is_no_good_for_the_cycle(db, perfil):
    """`agent_settings.bar_interval` admits '1m' because the column is shared
    with the ingestor. The cycle does not: with one-minute bars there is no
    history for the long indicators."""
    db.update_settings(perfil, {"bar_interval": "1m"})

    with pytest.raises(ConfigError, match="ingestor"):
        resolve_settings(db, perfil, infra=INFRA)


def test_the_error_names_the_profile(db, perfil):
    """With several experiments at once, an error with no name forces guessing
    which of them is misconfigured."""
    db.update_settings(perfil, {"bar_interval": "1m"})

    with pytest.raises(ConfigError, match="experimento-01"):
        resolve_settings(db, perfil, infra=INFRA)


def test_a_profile_that_does_not_exist_is_refused(db):
    with pytest.raises(ConfigError, match="no existe"):
        resolve_settings(db, "no-existe", infra=INFRA)


def test_with_no_model_key_it_fails_while_resolving(db, perfil):
    """Better here than inside the first call to the LLM, with the cycle already
    open and rows written."""
    sin_clave = Infra(db_path="x.db", model_api_key="")

    with pytest.raises(ConfigError, match="NVIDIA_API_KEY"):
        resolve_settings(db, perfil, infra=sin_clave)


# -- Eleccion de perfil ------------------------------------------------------


def test_with_no_profiles_the_message_explains_how_to_start(db):
    with pytest.raises(ConfigError, match="import-profile"):
        select_profile(db)


def test_a_single_active_profile_is_chosen_on_its_own(db, perfil):
    assert select_profile(db) == perfil


def test_with_several_active_one_must_be_chosen(db, perfil):
    """Running a cycle against the wrong experiment dirties two histories at once
    and cannot be undone."""
    otro = db.create_profile(name="experimento-02")
    db.set_profile_status(otro, "active")

    with pytest.raises(ConfigError, match="--profile"):
        select_profile(db)


def test_with_none_active_nothing_is_guessed_either(db):
    db.create_profile(name="borrador")

    with pytest.raises(ConfigError, match="Ningun perfil esta activo"):
        select_profile(db)


def test_the_name_wins_over_the_status(db, perfil):
    """Trading by hand against a paused profile is allowed; what is not allowed
    is it being chosen without saying so."""
    pausado = db.create_profile(name="pausado")

    assert select_profile(db, name="pausado") == pausado


def test_a_name_that_does_not_exist_lists_the_ones_that_do(db, perfil):
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


def test_importing_the_env_leaves_a_usable_profile(db):
    profile_id = import_env_profile(db, _env_settings())

    assert db.get_profile(profile_id)["status"] == "active"
    assert db.get_profile_universe(profile_id) == ["AAPL", "TSLA"]
    settings = resolve_settings(db, profile_id, infra=INFRA)
    assert settings.initial_budget == 10_000.0
    assert settings.watchlist == ("AAPL", "TSLA")


def test_importing_keeps_the_exact_limits_of_the_env(db):
    """They are imported as advanced mode on purpose.

    The `.env` carried nine explicit numbers; replacing them with the ones coming
    out of `risk_profile=5` would change the agent's behaviour in the very
    operation that was only meant to move the configuration somewhere else.
    """
    profile_id = import_env_profile(db, _env_settings())

    row = db.get_settings(profile_id)
    assert row["advanced_overrides"] == 1
    assert row["risk_per_trade_pct"] == pytest.approx(1.5)
    assert row["max_open_positions"] == 7

    settings = resolve_settings(db, profile_id, infra=INFRA)
    assert settings.risk.risk_per_trade_pct == pytest.approx(1.5)
    assert settings.risk.max_open_positions == 7


def test_the_explicit_name_beats_the_one_in_the_env(db):
    profile_id = import_env_profile(db, _env_settings(), name="otro-nombre")

    assert db.get_profile(profile_id)["name"] == "otro-nombre"


def test_importing_the_same_name_twice_fails(db):
    from src.db import DatabaseError

    import_env_profile(db, _env_settings())

    with pytest.raises(DatabaseError, match="Ya existe"):
        import_env_profile(db, _env_settings())


# -- F6.3: the copy of the parameters in the cycle ---------------------------


def test_the_snapshot_carries_no_secrets():
    """The history gets exported and opened with DB Browser: a key inside a JSON
    column is not something you see coming."""
    datos = _env_settings(model_api_key="nvapi-secreto").snapshot()

    assert "model_api_key" not in datos
    assert "nvapi-secreto" not in str(datos)
    assert "db_path" not in datos


def test_the_snapshot_carries_the_effective_limits():
    datos = _env_settings().snapshot()

    assert datos["risk"]["risk_per_trade_pct"] == pytest.approx(1.5)
    assert datos["initial_budget"] == 10_000.0
    assert datos["watchlist"] == ["AAPL", "TSLA"]


def test_the_cycle_stores_and_returns_its_settings(db, perfil):
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


def test_a_cycle_with_no_copy_returns_none(db, perfil):
    """Cycles predating F6.3 do not carry it. It is missing information, not a
    zero: comparing experiments demands telling "it ran with these settings" from
    "we do not know which settings it ran with".
    """
    portfolio_id = db.get_profile(perfil)["portfolio_id"]
    cycle_id = db.start_cycle(
        portfolio_id=portfolio_id, equity_start=1, cash_start=1,
        market_open=False, symbols=[], llm_model="x",
    )

    assert cycle_settings(db, cycle_id) is None


# -- Coherencia entre esquema y codigo ---------------------------------------


def test_every_derivable_limit_has_a_nullable_column(db, perfil):
    """If a limit of `RiskLimits` has no column, advanced mode cannot set it and
    F6.8's form would have a field that stores nothing."""
    row = db.get_settings(perfil)

    for campo in DERIVED_FIELDS:
        assert campo in row, f"falta la columna {campo} en agent_settings"
        assert row[campo] is None, f"{campo} deberia nacer NULL"


def test_the_new_columns_appear_in_a_database_that_already_existed(tmp_path):
    """`create table if not exists` does not add columns to an existing table.

    Without the migration, a new column would work on a freshly created database
    and be missing from the one already running, which is the worst possible split.
    """
    ruta = tmp_path / "vieja.db"
    nuevas = ADDED_COLUMNS["agent_settings"]

    with Database(path=ruta) as database:
        database.create_profile(name="anterior")

    # The pre-F6.4 database is simulated by dropping the new columns.
    plana = sqlite3.connect(ruta)
    for columna in nuevas:
        plana.execute(f"alter table agent_settings drop column {columna}")
    plana.commit()
    restantes = {row[1] for row in plana.execute("pragma table_info(agent_settings)")}
    plana.close()
    assert not (set(nuevas) & restantes), "la simulacion no quito las columnas"

    with Database(path=ruta) as database:
        row = database.get_settings(database.list_profiles()[0]["id"])

    for columna in nuevas:
        assert columna in row, f"la migracion no anadio {columna}"


def test_the_migration_is_idempotent(tmp_path):
    """It runs on every startup: the second time it must do nothing."""
    ruta = tmp_path / "repetida.db"

    with Database(path=ruta) as database:
        antes = database._columns("agent_settings")
    with Database(path=ruta) as database:
        despues = database._columns("agent_settings")

    assert antes == despues
