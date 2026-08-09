"""Experiment profiles, agent settings and live market data.

It covers the tables F1 introduces. What gets tested most here is not the happy
path but three invariants that, if broken, ruin the experiment in silence:

  * deleting a profile drags all of its history along (otherwise orphans are left
    that contaminate the next one's metrics),
  * the settings history records the real changes and only those,
  * the ingestor's writes are idempotent (the current minute's bar is rewritten
    every minute until it closes).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.db import DatabaseError


def _iso(moment: datetime) -> str:
    return moment.isoformat()


# -- Perfiles ---------------------------------------------------------------


def test_creating_a_profile_leaves_settings_and_a_book(db):
    """A profile is born usable: with default settings and with a book."""
    profile_id = db.create_profile(name="experimento-01", description="control")

    profile = db.get_profile(profile_id)
    assert profile["name"] == "experimento-01"
    assert profile["status"] == "draft"
    assert profile["portfolio_id"], "el perfil debe traer cartera desde el minuto uno"

    settings = db.get_settings(profile_id)
    assert settings["llm_provider"] == "nvidia"
    assert settings["risk_profile"] == 5
    assert settings["diversification"] == 5


def test_hard_limits_are_born_null(db):
    """NULL means 'derive it from the sliders'.

    If they were born with numbers, moving the risk slider would change nothing
    and the user would not understand why.
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


def test_a_profile_name_is_unique(db):
    db.create_profile(name="repetido")
    with pytest.raises(DatabaseError, match="Ya existe"):
        db.create_profile(name="repetido")


def test_an_empty_name_is_refused(db):
    with pytest.raises(DatabaseError, match="nombre"):
        db.create_profile(name="   ")


def test_deleting_a_profile_drags_its_history_along(db):
    """The cascade is what allows throwing a failed experiment away in one go.

    Without it, cycles and decisions would be left ownerless, later turning up in
    the analysis views and corrupting the comparison between experiments.
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


def test_deleting_a_profile_drags_the_simulated_brokers_ledger_along(db):
    """`sim_accounts` does not hang off `portfolios` with an FK: its id **is** the
    portfolio_id, but without a `references`, so the cascade does not reach it on
    its own.

    It is checked separately because the symptom is mute: the profile disappears
    from every screen and its cash, its simulated positions and its fills stay
    there taking up space, with nothing tying them to anyone.
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


def test_an_invalid_status_is_refused(db):
    profile_id = db.create_profile(name="p")
    with pytest.raises(DatabaseError, match="Estado invalido"):
        db.set_profile_status(profile_id, "encendido")


def test_archiving_stamps_the_date_and_takes_it_off_the_listing(db):
    profile_id = db.create_profile(name="viejo")
    db.set_profile_status(profile_id, "archived")

    assert db.get_profile(profile_id)["archived_at"] is not None
    assert db.list_profiles() == []
    assert len(db.list_profiles(include_archived=True)) == 1


# -- Parametros --------------------------------------------------------------


def test_changing_a_setting_leaves_a_trace(db):
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


def test_rewriting_the_same_value_does_not_dirty_the_history(db):
    """The history exists to explain changes of behaviour.

    A row that changes nothing only makes it harder to find the one that does.
    """
    profile_id = db.create_profile(name="p")
    db.update_settings(profile_id, {"risk_profile": 7})

    cambiados = db.update_settings(profile_id, {"risk_profile": 7})

    assert cambiados == []
    assert len(db.settings_history(profile_id)) == 1


def test_an_unknown_setting_is_refused(db):
    """A misspelt field name must fail, not be stored in silence."""
    profile_id = db.create_profile(name="p")
    with pytest.raises(DatabaseError, match="desconocidos"):
        db.update_settings(profile_id, {"risk_profil": 9})


def test_sql_cannot_be_smuggled_in_through_the_field_name(db):
    """Column names take no placeholder, so they are validated separately."""
    profile_id = db.create_profile(name="p")
    with pytest.raises(DatabaseError, match="desconocidos"):
        db.update_settings(profile_id, {"risk_profile = 1, llm_model": "x"})


def test_the_schema_stops_settings_out_of_range(db):
    profile_id = db.create_profile(name="p")
    with pytest.raises(DatabaseError):
        db.update_settings(profile_id, {"risk_profile": 11})


def test_settings_of_a_profile_that_does_not_exist(db):
    with pytest.raises(DatabaseError, match="no tiene parametros"):
        db.get_settings("no-existe")


# -- Universo ----------------------------------------------------------------


def test_the_universe_is_normalised_and_replaced(db):
    profile_id = db.create_profile(name="p")

    db.set_profile_universe(profile_id, [" aapl ", "MSFT", "aapl", ""])
    assert db.get_profile_universe(profile_id) == ["AAPL", "MSFT"]

    db.set_profile_universe(profile_id, ["NVDA"])
    assert db.get_profile_universe(profile_id) == ["NVDA"]


def test_the_active_universe_only_looks_at_active_profiles(db):
    activo = db.create_profile(name="activo")
    pausado = db.create_profile(name="pausado")
    db.set_profile_universe(activo, ["AAPL"])
    db.set_profile_universe(pausado, ["TSLA"])
    db.set_profile_status(activo, "active")

    assert db.active_universe() == ["AAPL"]


def test_the_active_universe_includes_open_positions(db):
    """An open position needs a price even when its symbol leaves the universe.

    Otherwise the agent would be left unable to value or close what it already holds.
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


def test_quotes_live_does_not_grow(db):
    """One row per symbol, replaced. If it grew, it would be a duplicate history
    of bars_1m."""
    ahora = datetime.now(timezone.utc)

    db.upsert_quotes([{"symbol": "AAPL", "price": 100.0, "as_of": _iso(ahora)}])
    db.upsert_quotes([{"symbol": "AAPL", "price": 101.5, "as_of": _iso(ahora)}])

    assert db.query("select count(1) n from quotes_live")[0]["n"] == 1
    assert db.latest_quotes()["AAPL"]["price"] == 101.5


def test_bars_1m_rewrites_the_bar_in_progress(db):
    """The current minute's bar changes while the market is still open.

    Hence `insert or replace` and not `insert or ignore`: with ignore, the
    minute's closing price would stay frozen at the first value seen.
    """
    ts = _iso(datetime(2026, 8, 7, 15, 30, tzinfo=timezone.utc))
    barra = {"symbol": "AAPL", "ts": ts, "open": 100, "high": 100,
             "low": 100, "close": 100, "volume": 500}

    db.upsert_bars_1m([barra])
    db.upsert_bars_1m([{**barra, "high": 103, "close": 102, "volume": 1500}])

    rows = db.query("select * from bars_1m")
    assert len(rows) == 1
    assert rows[0]["close"] == 102
    assert rows[0]["volume"] == 1500


def test_prune_respects_the_window(db):
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


def test_prune_demands_a_positive_window(db):
    """keep_days=0 would wipe the whole history in one go."""
    with pytest.raises(DatabaseError, match="al menos 1"):
        db.prune_bars_1m(keep_days=0)


def test_an_ingest_run_opens_and_closes(db):
    run_id = db.start_ingest_run(symbols_requested=50)
    db.finish_ingest_run(
        run_id, symbols_ok=48, symbols_failed=2, latency_ms=1550, rate_limited=False
    )

    run = db.ingest_health(limit=1)[0]
    assert (run["symbols_ok"], run["symbols_failed"]) == (48, 2)
    assert run["latency_ms"] == 1550
    assert run["finished_at"] is not None


def test_ingest_health_returns_the_most_recent_first(db):
    for _ in range(3):
        db.finish_ingest_run(
            db.start_ingest_run(symbols_requested=10),
            symbols_ok=10, symbols_failed=0, latency_ms=100,
        )

    salud = db.ingest_health(limit=2)
    assert len(salud) == 2
    assert salud[0]["id"] > salud[1]["id"]


def test_empty_writes_do_not_break(db):
    """The ingestor can finish a tick with nothing to write (a calm market, or
    every symbol failing)."""
    assert db.upsert_quotes([]) == 0
    assert db.upsert_bars_1m([]) == 0


# ----------------------------------------------------------------------
# The demo, which is the README's front door
# ----------------------------------------------------------------------

def test_the_demo_creates_a_profile_and_not_just_a_book(tmp_path):
    """With no profile, F4's interface cannot show it.

    `tools/seed_demo.py` called only `ensure_portfolio`, which leaves an orphan
    book. The console found it by name and the old dashboard offered it in its
    selector, so the demo seemed to work; but the new interface navigates by
    profile (`/p/demo/...`) and `/api/profiles` returned an empty list. It is the
    first thing the README tells you to do, so it stays pinned down.
    """
    import sys
    from pathlib import Path

    from src.db import Database

    raiz = Path(__file__).resolve().parent.parent
    if str(raiz / "tools") not in sys.path:
        sys.path.insert(0, str(raiz / "tools"))
    import seed_demo

    with Database(path=tmp_path / "demo.db") as db:
        seed_demo.seed(db)

        perfil = db.get_profile_by_name(seed_demo.DEMO_NAME)
        assert perfil is not None, "la demo tiene que existir como perfil"
        assert perfil["status"] == "active"
        assert perfil["portfolio_id"], "y su cartera tiene que colgar del perfil"
        # Y con datos: una demo vacia no prueba nada.
        assert db.query("select count(1) n from cycles")[0]["n"] > 0
