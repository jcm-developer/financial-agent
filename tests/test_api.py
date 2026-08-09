"""Tests of the API (F3.9).

They run against `TestClient`, which talks to the application in memory via
httpx: no sockets opened, no network and no uvicorn started. Each test builds its
own application over a database in `tmp_path`, which is the reason `ApiConfig`
lives in `app.state` and not in a global.

The group that matters most is "the API cannot write to the history". It does not
check that the endpoints behave —that would be checking code that has already
been read— but that they **cannot misbehave**: R5 says that opening the API to
writes loses the dashboard's read-only guarantee, and these tests are the
mitigation D5 promised.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.deps import ApiConfig, config_db, read_db
from api.guard import WRITABLE, ConfigDatabase, HistoryIsReadOnly, history_tables
from api.main import create_app
from api.models import AgentSettings, SettingsUpdate
from src.db import Database

# Routes that change state without writing to the database: they launch
# `run.py cycle` as a subprocess, which opens its own connection. It is the
# separation `api/routes/control.py` describes, and it is written here so that
# adding a write route without a fenced connection has to be a conscious decision.
CONTROL_ROUTES = {
    "/api/cycles/run",
    "/api/cycles/stop",
    # Closing an experiment sells the book (F5.8), so it writes to the history —
    # and precisely because of that it cannot be an endpoint that writes: it goes
    # out as `run.py close-experiment`, which opens its own connection. The test
    # below forced this line to be written, which is what it is for.
    "/api/cycles/close-experiment",
}


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
    """Fills in a cycle by hand with its decision, its verdict and its order.

    It writes with the normal connection on purpose: that is what the agent does.
    The point of the tests below is that the API can **read** all of this and
    cannot touch it.
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
    """How many rows there are in each table the API cannot write."""
    with Database(path=db_path, read_only=True) as db:
        return {
            table: db.query(f"select count(1) as n from {table}")[0]["n"]
            for table in history_tables(db)
        }


# ======================================================================
# F3.3 -- The API cannot write to the history
# ======================================================================

def test_the_api_connection_writes_to_no_history_table(
    db_path, profile
):
    """The check does not use a hand-written list: it walks the schema.

    That way a new table in `schema.sql` enters the test on its own. With a fixed
    list, the table somebody adds tomorrow would go uncovered exactly when it is
    needed most.
    """
    with ConfigDatabase(path=db_path) as db:
        protegidas = history_tables(db)
        assert protegidas, "el esquema deberia tener tablas de historico"

        for table in protegidas:
            with pytest.raises(HistoryIsReadOnly):
                db._execute(f"delete from {table}")


def test_the_api_does_write_to_the_configuration_tables(db_path, profile):
    with ConfigDatabase(path=db_path) as db:
        assert db.update_settings(profile["id"], {"risk_profile": 9}) == ["risk_profile"]
        db.set_profile_universe(profile["id"], ["SAN.MC"])
        db.set_profile_status(profile["id"], "active")
        assert db.get_settings(profile["id"])["risk_profile"] == 9


def test_the_api_runs_no_free_form_sql(db_path):
    """`Database.execute` is there for the tools in tools/, not for the web.

    Without this closure, the authorizer would be the only barrier; with it,
    touching the database means going through a named method, which is where what
    it does can be seen.
    """
    with ConfigDatabase(path=db_path) as db:
        with pytest.raises(HistoryIsReadOnly):
            db.execute("update positions set qty = 0")


def test_the_api_cannot_alter_the_schema(db_path):
    with ConfigDatabase(path=db_path) as db:
        for sql in (
            "create table colado (id integer)",
            "drop table cycles",
            "alter table cycles add column colada text",
        ):
            with pytest.raises(HistoryIsReadOnly):
                db._execute(sql)


def test_deleting_a_profile_does_drag_its_history_along(db_path, profile):
    """The only exception, and it is deliberate: deleting an experiment deletes it whole.

    **Both halves** are checked: that the cascade reaches the history (if the
    authorizer cut it off, the delete would fail halfway) and that the window is
    closed afterwards, so the connection does not go on serving requests with no
    restrictions for the rest of its life.
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


def test_no_write_endpoint_receives_an_unfenced_connection(client):
    """A structural check, not a behavioural one.

    It looks at each route's declared dependencies: the ones that change state
    have to ask for `config_db` (the fenced one) or ask for no write database at
    all. A new endpoint opening a normal connection fails here, which is long
    before it corrupts anything.
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
    """Every dependency of a route, nested ones included."""
    encontradas = set()
    pendientes = list(dependant.dependencies)
    while pendientes:
        actual = pendientes.pop()
        if actual.call in (read_db, config_db):
            encontradas.add(actual.call)
        pendientes.extend(actual.dependencies)
    return encontradas


def test_the_write_endpoints_do_not_move_the_history(client, db_path, profile):
    """End to end: every write is exercised and nothing moves.

    It is the complement of the structural test. That one says the connection is
    fenced; this one says that, with the whole API really working, the number of
    rows in the history is exactly the same before and after.
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


def test_the_history_is_served_from_a_read_only_connection(client, db_path, profile):
    """The guarantee that already existed before F3 and has not been lost.

    The read endpoints still open SQLite in `ro` mode: nothing can be written
    through there even if the code tries.
    """
    seed_history(db_path, profile["id"])
    assert client.get(f"/api/positions?profile={profile['id']}").status_code == 200

    with Database(path=db_path, read_only=True) as db:
        with pytest.raises(Exception):
            db._execute("delete from cycles")


# ======================================================================
# F3.2 -- Endpoints de lectura
# ======================================================================

def test_an_empty_profile_list(client):
    assert client.get("/api/profiles").json() == []


def test_a_created_profile_brings_market_currency_and_limits(profile):
    assert profile["market"] == "eu"
    assert profile["currency"] == "EUR"
    # The currency travels with the number: a European budget with '$' invites
    # comparing it against another profile's as if it were the same unit (FE.8).
    assert profile["currency_symbol"] == "€"
    assert profile["watched_symbols"] == 89
    assert profile["status"] == "draft"
    # The limits come from risk_presets, not from arithmetic repeated in the API.
    assert profile["limits"]["max_open_positions"] == 13
    assert "risk_per_trade_pct" in profile["limits"]["derived_fields"]


def test_the_limits_preview_matches_the_anchors_of_f65(client):
    """F6.8's form asks what the sliders would give, without writing anything.

    The three anchors of the F6.5 table are checked and not just "it answers":
    what this endpoint exists to prevent is the interface deriving the limits on
    its own, so the point is that it gives the same numbers the Risk Manager
    applies.
    """
    anchors = {
        1: (0.25, 5.0, 85, 2.0),
        5: (1.0, 20.0, 65, 5.0),
        10: (3.0, 40.0, 45, 10.0),
    }
    for level, (risk_per_trade, max_position, conviction, kill) in anchors.items():
        body = client.get(
            "/api/profiles/limits-preview",
            params={"risk_profile": level, "diversification": 5},
        ).json()
        assert body["risk_per_trade_pct"] == risk_per_trade
        assert body["max_position_pct"] == max_position
        assert body["min_conviction"] == conviction
        assert body["max_daily_loss_pct"] == kill

    # Diversification moves only the number of positions: 1 -> 3, 10 -> 25.
    for level, expected in ((1, 3), (10, 25)):
        body = client.get(
            "/api/profiles/limits-preview",
            params={"risk_profile": 5, "diversification": level},
        ).json()
        assert body["max_open_positions"] == expected


def test_the_preview_does_not_shadow_a_profile_route(client, profile):
    """The literal path is declared before `/{profile_ref}`.

    With the parameter route first, `/api/profiles/limits-preview` would arrive
    as a profile named "limits-preview" and answer 404 for a route that exists.
    The reverse has to keep working too, which is what the second half checks.
    """
    assert client.get("/api/profiles/limits-preview").status_code == 200
    assert client.get(f"/api/profiles/{profile['name']}/limits").status_code == 200


def test_the_preview_writes_nothing(client, db_path, profile):
    """It answers a question; it must not leave the profile changed.

    Asking what a slider would do is the gesture the form repeats on every move,
    so if it wrote, moving the slider to look would already have changed the
    experiment.
    """
    before = client.get(f"/api/profiles/{profile['name']}/settings").json()
    client.get(
        "/api/profiles/limits-preview",
        params={"risk_profile": 10, "diversification": 1},
    )
    after = client.get(f"/api/profiles/{profile['name']}/settings").json()
    assert before == after


def test_a_bad_schedule_is_refused_when_saving(client, profile):
    """Since F6.10 `cycle_times` is what the scheduler runs on.

    A typo typed into the settings form stops being cosmetic: that profile goes
    unscheduled. The scheduler survives it —it skips the profile and says so— but
    from the outside the failure is silent, and a container that looks alive and
    runs nothing is the worst way to find out. So it is refused here, which is
    what turns it into a red field while it is being typed.
    """
    for bad in ("a las cinco", "25:00", "17-40", ""):
        response = client.patch(
            f"/api/profiles/{profile['name']}/settings", json={"cycle_times": bad},
        )
        assert response.status_code == 422, f"{bad!r} deberia rechazarse"


def test_the_schedule_is_stored_normalised(client, profile):
    """"9:5" and "09:05" mean the same thing and must not become two different
    strings in `agent_settings_history`, which is read by eye."""
    client.patch(
        f"/api/profiles/{profile['name']}/settings",
        json={"cycle_times": "17:40, 9:5"},
    )
    settings = client.get(f"/api/profiles/{profile['name']}/settings").json()["settings"]

    assert settings["cycle_times"] == "09:05,17:40"


def test_the_market_sets_the_liquidity_floor(profile):
    """FE.11 from the API too: with 'us''s 20 M the European screener would
    discard 15 of the 89 without saying anything."""
    assert profile["settings"]["screener_min_turnover"] == 5_000_000


def test_the_legacy_dashboard_endpoint_no_longer_exists(client, db_path, profile):
    """F4.11: `/api/dashboard` was retired with the old dashboard it served.

    It is checked that it answers **404 in JSON** and not the SPA's `index.html`.
    It is F3.7's exception put to the test exactly where it matters: if the
    fallback to `index.html` swallowed this route, the endpoint would look alive
    with a 200 and the symptom would be a `JSON.parse` failing in the browser.
    """
    seed_history(db_path, profile["id"])
    respuesta = client.get("/api/dashboard?profile=europa-01")

    assert respuesta.status_code == 404
    assert respuesta.headers["content-type"].startswith("application/json")


def test_positions_are_valued_with_the_live_quote(client, db_path, profile):
    """And **where** the price comes from is stated.

    A position valued with the close from two days ago and another with a price
    from a minute ago cannot be added together without knowing it, so the origin
    travels with the datum.
    """
    seed_history(db_path, profile["id"])
    row = client.get(f"/api/positions?profile={profile['id']}").json()["items"][0]

    assert row["symbol"] == "SAN.MC"
    assert row["price_source"] == "live"
    assert row["last_price"] == 4.8
    assert row["unrealized_pnl"] == pytest.approx(30.0)
    assert row["stop_distance_pct"] == pytest.approx(20.0)


def test_decisions_bring_the_risk_verdict(client, db_path, profile):
    seed_history(db_path, profile["id"])
    pagina = client.get(f"/api/decisions?profile={profile['id']}").json()

    assert pagina["total"] == 1
    decision = pagina["items"][0]
    assert decision["action"] == "buy"
    assert decision["verdict"] == "approved"
    assert decision["order_status"] == "filled"


def test_the_lists_paginate(client, db_path, profile):
    seed_history(db_path, profile["id"])
    pagina = client.get(f"/api/orders?profile={profile['id']}&limit=1&offset=1").json()

    assert pagina["total"] == 1
    assert pagina["limit"] == 1 and pagina["offset"] == 1
    assert pagina["items"] == []


def test_the_list_filters(client, db_path, profile):
    seed_history(db_path, profile["id"])
    pid = profile["id"]

    assert client.get(f"/api/decisions?profile={pid}&action=sell").json()["total"] == 0
    assert client.get(f"/api/decisions?profile={pid}&action=buy").json()["total"] == 1
    assert client.get(f"/api/risk-events?profile={pid}&verdict=rejected").json()["total"] == 0
    assert client.get(f"/api/positions?profile={pid}&status=closed").json()["total"] == 0


def test_the_cycle_detail_brings_the_settings_it_ran_with(
    client, db_path, profile
):
    """F6.3: without that copy, an experiment whose settings are edited midway
    stops being interpretable."""
    seed = seed_history(db_path, profile["id"])
    detalle = client.get(f"/api/cycles/{seed['cycle_id']}").json()

    assert detalle["status"] == "completed"
    assert detalle["equity_delta"] == pytest.approx(120.0)
    assert detalle["settings"]["market"] == "eu"
    assert detalle["symbols_scanned"] == ["SAN.MC", "ITX.MC"]


def test_a_cycle_that_does_not_exist_gives_404(client):
    assert client.get("/api/cycles/no-existe").status_code == 404


def test_quotes_state_their_age(client, db_path, profile):
    """`age_seconds` is F2.1c's measurement: 'every minute' only holds if the
    datum is a minute old, and Yahoo serves Europe with a lag."""
    seed_history(db_path, profile["id"])
    quotes = client.get("/api/quotes").json()

    assert quotes[0]["symbol"] == "SAN.MC"
    assert quotes[0]["age_seconds"] is not None


def test_the_ingestor_status(client, db_path, profile):
    seed_history(db_path, profile["id"])
    estado = client.get("/api/ingest-status").json()

    assert estado["bars_stored"] == 0
    assert estado["quotes_stored"] == 1
    assert estado["consecutive_failures"] == 0
    assert estado["avg_latency_ms"] == 850
    assert estado["message"]


def test_with_no_active_profiles_the_ingestor_is_healthy(client):
    """Sleeping outside the operating window is not a breakage. If the panel were
    red every night, the red would stop meaning anything."""
    estado = client.get("/api/ingest-status").json()
    assert estado["healthy"] is True


def test_mercados(client):
    mercados = {m["code"]: m for m in client.get("/api/markets").json()}

    assert mercados["eu"]["currency"] == "EUR"
    assert mercados["eu"]["session_open"] == "09:00"
    # The operating window is not the session (FE.13): 09:15-17:45 over 09:00-17:30.
    assert mercados["eu"]["operating_open"] == "09:15"
    assert mercados["eu"]["operating_close"] == "17:45"
    assert mercados["us"]["operating_open"] == mercados["us"]["session_open"]


def test_a_profile_that_does_not_exist_gives_404_listing_the_ones_that_do(client, profile):
    respuesta = client.get("/api/positions?profile=no-existe")
    assert respuesta.status_code == 404
    assert "europa-01" in respuesta.json()["detail"]


def test_a_profile_can_be_asked_for_by_name_or_by_id(client, profile):
    por_nombre = client.get("/api/profiles/europa-01").json()
    por_id = client.get(f"/api/profiles/{profile['id']}").json()
    assert por_nombre["id"] == por_id["id"]


# ======================================================================
# F3.3 -- Endpoints de escritura
# ======================================================================

def test_two_profiles_with_the_same_name_cannot_be_created(client, profile):
    respuesta = client.post("/api/profiles", json={"name": "europa-01", "market": "eu"})
    assert respuesta.status_code == 409


def test_the_whole_sp500_is_refused_without_an_explicit_cap(client):
    """R2: these are requests per minute against Yahoo from a domestic IP. The
    same rule `run.py new-profile` applies, because it is the same code."""
    respuesta = client.post("/api/profiles", json={"name": "us-01", "market": "us"})
    assert respuesta.status_code == 422
    assert "--watch" in respuesta.json()["detail"]

    con_tope = client.post(
        "/api/profiles", json={"name": "us-01", "market": "us", "watch": 50}
    )
    assert con_tope.status_code == 201
    assert con_tope.json()["watched_symbols"] == 50


def test_updating_settings_returns_only_what_changed(client, profile):
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


def test_the_settings_history_records_the_change(client, profile):
    pid = profile["id"]
    client.patch(f"/api/profiles/{pid}/settings", json={"diversification": 9})
    historial = client.get(f"/api/profiles/{pid}/settings/history").json()

    cambio = next(f for f in historial["items"] if f["field"] == "diversification")
    assert cambio["old_value"] == "5" and cambio["new_value"] == "9"
    assert cambio["source"] == "ui"


def test_switching_advanced_mode_off_hands_control_back_to_the_sliders(client, profile):
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


def test_an_unknown_setting_is_refused_by_the_api(client, profile):
    respuesta = client.patch(
        f"/api/profiles/{profile['id']}/settings", json={"inventado": 1}
    )
    assert respuesta.status_code == 422


def test_a_setting_out_of_range_is_refused(client, profile):
    respuesta = client.patch(
        f"/api/profiles/{profile['id']}/settings", json={"risk_profile": 42}
    )
    assert respuesta.status_code == 422


def test_a_profile_cannot_be_left_with_symbols_from_another_exchange(client, profile):
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


def test_the_live_universe_refuses_foreign_symbols(client, profile):
    respuesta = client.put(
        f"/api/profiles/{profile['id']}/universe", json={"symbols": ["AAPL", "SAN.MC"]}
    )
    assert respuesta.status_code == 422
    assert "AAPL" in respuesta.json()["detail"]


def test_duplicating_copies_settings_and_universe_but_not_history(
    client, db_path, profile
):
    """The experiment's central gesture (F5.4): clone, change one parameter and
    compare. Inheriting the history would be exactly what makes them
    incomparable.
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


def test_deleting_demands_repeating_the_name(client, profile):
    """It is the only API call that destroys data that took weeks to generate,
    and a DELETE to the wrong URL is a one-second gesture."""
    sin_confirmar = client.delete(f"/api/profiles/{profile['id']}")
    assert sin_confirmar.status_code == 400

    mal = client.delete(f"/api/profiles/{profile['id']}?confirm=otro-nombre")
    assert mal.status_code == 400

    bien = client.delete(f"/api/profiles/{profile['id']}?confirm=europa-01")
    assert bien.status_code == 200
    assert client.get("/api/profiles").json() == []


def test_a_profile_that_already_has_history_is_not_renamed(client, db_path, profile):
    """The book is named after the profile: renaming it would leave the history
    hanging off a name that no longer exists."""
    seed_history(db_path, profile["id"])
    respuesta = client.patch(
        f"/api/profiles/{profile['id']}", json={"name": "otro-nombre"}
    )
    assert respuesta.status_code == 409

    # With no history it is allowed.
    nuevo = client.post("/api/profiles", json={"name": "europa-03", "market": "eu"}).json()
    assert client.patch(
        f"/api/profiles/{nuevo['id']}", json={"name": "europa-renombrado"}
    ).status_code == 200


def test_activating_and_pausing_a_profile(client, profile):
    pid = profile["id"]
    assert client.patch(f"/api/profiles/{pid}", json={"status": "active"}).json()["status"] == "active"
    assert client.patch(f"/api/profiles/{pid}", json={"status": "paused"}).json()["status"] == "paused"
    assert client.patch(f"/api/profiles/{pid}", json={"status": "inventado"}).status_code == 422


def test_an_active_profile_enters_the_ingestors_universe(client, db_path, profile):
    """FE.7's trap: `universe_file` is what the screener sifts and
    `profile_universe` is what the ingestor follows. They are different things."""
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


def test_launching_a_cycle_passes_the_profile(client, profile):
    runner = StubRunner()
    client.app_ref.state.runner = runner

    respuesta = client.post(
        "/api/cycles/run", json={"profile": profile["id"], "dry_run": True}
    )
    assert respuesta.status_code == 200
    # The **name** is passed, which is what `run.py --profile` understands.
    assert runner.started == [("europa-01", True)]


def test_a_cycle_is_not_launched_if_one_is_already_running(client, db_path, profile):
    """The `cycles` table is checked too, not just this process's own subprocess:
    the scheduler may have one running that this process knows nothing about, and
    two cycles over the same book would step on each other's positions."""
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


def test_stopping_with_no_cycle_running(client):
    client.app_ref.state.runner = StubRunner()
    assert client.post("/api/cycles/stop").status_code == 409


def test_with_the_controls_off_there_is_no_way_to_fire_anything(db_path):
    """F3.8: the API serves data all the same, but with no button."""
    app = create_app(ApiConfig(db_path=db_path, controls=False))
    with TestClient(app) as sin_controles:
        assert sin_controles.post("/api/cycles/run", json={}).status_code == 403
        assert sin_controles.post("/api/cycles/stop").status_code == 403
        estado = sin_controles.get("/api/cycles/control/status").json()
        assert estado["enabled"] is False
        # And what only reads goes on working.
        assert sin_controles.get("/api/profiles").status_code == 200


# ======================================================================
# F3.5 -- SSE
# ======================================================================

def _parse_sse(text: str) -> dict[str, dict]:
    eventos: dict[str, dict] = {}
    name = None
    for line in text.splitlines():
        if line.startswith("event: "):
            name = line[7:].strip()
        elif line.startswith("data: ") and name:
            eventos[name] = json.loads(line[6:])
    return eventos


@pytest.fixture
def stream_client(db_path):
    """A client with a stream that closes itself almost immediately.

    `stream_max_seconds` exists in production out of hygiene —EventSource
    reconnects by itself, so cutting now and then returns resources— and here it
    also makes the test possible: an infinite generator leaves `TestClient`
    hanging on close, because in memory there is never a disconnection to detect.
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


def test_the_stream_sends_the_initial_state(stream_client, db_path):
    """A client connecting with the market stopped has to see something.

    Without the initial dump there would be no way to tell "nothing has changed"
    from "the connection is broken".
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
    # And it closes by age, warning that a reconnection is needed.
    assert eventos["bye"]["reconnect"] is True


def test_the_stream_filters_by_symbol(stream_client, db_path):
    creado = stream_client.post(
        "/api/profiles", json={"name": "europa-01", "market": "eu"}
    ).json()
    seed_history(db_path, creado["id"])

    eventos = _parse_sse(stream_client.get("/api/stream?symbols=ITX.MC").text)
    assert eventos["quotes"]["quotes"] == []


# ======================================================================
# F3.6 -- Modelos
# ======================================================================

def test_settings_update_covers_the_real_columns_of_agent_settings(db_path):
    """F6.8's form has to be able to touch everything in the table.

    Without this test, a new column in `agent_settings` would be unreachable from
    the interface and nobody would find out until looking for it on the screen.
    """
    with Database(path=db_path) as db:
        columnas = db._columns("agent_settings") - {"profile_id", "updated_at"}

    del_modelo = set(SettingsUpdate.model_fields)
    assert del_modelo == columnas, (
        f"sobran en el modelo: {sorted(del_modelo - columnas)}; "
        f"faltan: {sorted(columnas - del_modelo)}"
    )


def test_agent_settings_covers_the_real_columns_too(db_path):
    """The read model is checked the same way as the write one.

    Until F6.8 this endpoint answered a plain `dict` and reached the frontend as
    `Record<string, unknown>`, which is what F4.11 said no longer happened
    anywhere: a change in the backend would not break the build, it would break
    the 41-field form at runtime.
    """
    with Database(path=db_path) as db:
        columnas = db._columns("agent_settings") - {"profile_id"}

    del_modelo = set(AgentSettings.model_fields)
    assert del_modelo == columnas, (
        f"sobran en el modelo: {sorted(del_modelo - columnas)}; "
        f"faltan: {sorted(columnas - del_modelo)}"
    )


def test_the_settings_booleans_arrive_as_booleans(client, profile):
    """SQLite has no boolean and gives back 0/1.

    They are converted once, in the model, and not in each screen deciding
    whether `0` is false: `dry_run: 0` read as truthy would say an experiment is
    in dry run when it is trading.
    """
    settings = client.get(f"/api/profiles/{profile['name']}/settings").json()["settings"]
    for field in ("dry_run", "allow_shorts", "skip_when_market_closed", "advanced_overrides"):
        assert isinstance(settings[field], bool), f"{field} llega como {type(settings[field])}"


def test_a_derived_limit_stays_null_when_read(client, profile):
    """NULL is the datum: it means "recompute it from the sliders" (F6.5).

    A read model that filled it in with a number would erase the difference
    between a limit that was chosen and one that was inherited, and the advanced
    mode of the form would have nothing to switch on.
    """
    body = client.get(f"/api/profiles/{profile['name']}/settings").json()
    assert body["settings"]["risk_per_trade_pct"] is None
    # And the effective one does have a value, which is the whole point of
    # sending both together.
    assert body["limits"]["risk_per_trade_pct"] is not None


def test_the_writable_tables_really_exist(db_path):
    """`WRITABLE` is written by hand; a typo would leave it without effect."""
    with Database(path=db_path) as db:
        reales = {
            row["name"]
            for row in db.query("select name from sqlite_master where type = 'table'")
        }
    assert set(WRITABLE) <= reales


def test_the_openapi_is_published_and_the_types_are_generated(client):
    esquema = client.get("/openapi.json").json()
    assert "/api/profiles" in esquema["paths"]

    from tools.gen_api_types import render

    typescript = render(esquema)
    assert "export interface ProfileSummary" in typescript
    assert "export interface ApiOperations" in typescript
    # The API key comes out masked in the contract too.
    assert "llm_api_key_masked" in typescript


# ======================================================================
# F3.7 / F3.8 -- Estaticos y escucha
# ======================================================================

def test_an_api_route_that_does_not_exist_gives_404_in_json(client):
    """Without this exception, the fallback to index.html would return the
    application's HTML with a 200 and the symptom would be a JSON.parse failing
    three layers down."""
    respuesta = client.get("/api/inventado")
    assert respuesta.status_code == 404
    assert respuesta.headers["content-type"].startswith("application/json")


def test_the_spa_routes_fall_through_to_the_frontend(client):
    respuesta = client.get("/perfiles/europa-01")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/html")


def test_with_no_frontend_build_it_says_it_is_missing(tmp_path, db_path):
    """A bare 404 would read as a breakage; this says it arrives in F4.

    `app_dist` is pointed at an empty directory on purpose. It used to use the
    default client, that is, the real `app/dist`, and the test told the truth only
    while nobody had compiled the frontend: as soon as `npm run build` was run
    (F4, stretch A) it started failing on the development machine and passing in
    CI, which is the worst possible combination.
    """
    vacio = tmp_path / "sin-build"
    vacio.mkdir()

    app = create_app(ApiConfig(db_path=db_path, app_dist=vacio))
    with TestClient(app) as sin_build:
        assert "app/dist" in sin_build.get("/").text


def test_the_spa_serves_the_builds_files(tmp_path, db_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    app = create_app(ApiConfig(db_path=db_path, app_dist=dist))
    with TestClient(app) as con_build:
        assert con_build.get("/assets/app.js").text == "console.log(1)"
        # A route of the SPA's router is not a file: it gets the index.
        assert con_build.get("/decisiones").text == "<html>app</html>"


def test_by_default_it_listens_on_loopback_only(monkeypatch):
    """F3.8: this is data from an investment account on someone's own machine."""
    monkeypatch.delenv("API_HOST", raising=False)
    monkeypatch.delenv("API_CONTROLS", raising=False)
    config = ApiConfig.load(db_path="data/trading.db")

    assert config.host == "127.0.0.1"
    assert config.controls is True
