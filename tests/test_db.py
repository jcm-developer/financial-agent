"""Tests of the persistence layer and of the reconciliation with the broker.

Being SQLite, it is tested against a real database in a temporary file: no
doubles are needed and schema.sql itself gets validated too.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.db import Database, DatabaseError
from src.models import BrokerPosition, MarketSnapshot, Proposal, RiskVerdict


@pytest.fixture
def db(tmp_path):
    with Database(path=tmp_path / "test.db") as database:
        yield database


@pytest.fixture
def portfolio(db):
    return db.ensure_portfolio(name="test", mode="paper", initial_budget=10_000.0)


@pytest.fixture
def cycle(db, portfolio):
    return db.start_cycle(
        portfolio_id=portfolio,
        equity_start=10_000.0,
        cash_start=10_000.0,
        market_open=True,
        symbols=["AAPL", "MSFT"],
        llm_model="test-model",
    )


def broker_position(symbol="AAPL", qty=10.0, entry=100.0, price=105.0):
    return BrokerPosition(
        symbol=symbol,
        qty=qty,
        avg_entry_price=entry,
        current_price=price,
        market_value=qty * price,
        unrealized_pl=(price - entry) * qty,
        unrealized_pl_pct=(price / entry - 1) * 100,
    )


# -- Esquema y carteras ------------------------------------------------------

def test_schema_is_created_on_open(db):
    tables = {
        row["name"]
        for row in db.query("select name from sqlite_master where type = 'table'")
    }
    assert {
        "portfolios", "cycles", "market_snapshots", "decisions",
        "risk_events", "orders", "positions", "equity_snapshots",
    } <= tables


def test_views_are_created_on_open(db):
    views = {
        row["name"]
        for row in db.query("select name from sqlite_master where type = 'view'")
    }
    assert {
        "v_performance_by_symbol", "v_conviction_calibration",
        "v_risk_rejections", "v_decision_mix",
    } <= views


def test_reopening_the_database_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    with Database(path=path) as first:
        portfolio_id = first.ensure_portfolio(
            name="p", mode="paper", initial_budget=1000.0
        )
    with Database(path=path) as second:
        assert second.ensure_portfolio(
            name="p", mode="paper", initial_budget=1000.0
        ) == portfolio_id


def test_ensure_portfolio_is_stable_across_calls(db):
    first = db.ensure_portfolio(name="p", mode="paper", initial_budget=1000.0)
    second = db.ensure_portfolio(name="p", mode="paper", initial_budget=1000.0)
    assert first == second


def test_switching_a_portfolio_from_paper_to_live_is_refused(db):
    db.ensure_portfolio(name="p", mode="paper", initial_budget=1000.0)

    with pytest.raises(DatabaseError, match="PORTFOLIO_NAME"):
        db.ensure_portfolio(name="p", mode="live", initial_budget=1000.0)


# -- Escrituras del ciclo ----------------------------------------------------

def test_snapshot_round_trip_preserves_indicators(db, cycle):
    snapshot = MarketSnapshot(
        symbol="AAPL",
        as_of=datetime(2024, 6, 1, tzinfo=timezone.utc),
        price=150.25,
        indicators={"rsi_14": 62.5, "atr_14": 3.1, "sma_200": None},
    )
    snapshot_id = db.save_snapshot(cycle_id=cycle, snapshot=snapshot)

    rows = db.query(
        "select symbol, price, json_extract(indicators_json, '$.rsi_14') as rsi, "
        "json_extract(indicators_json, '$.sma_200') as sma200 "
        "from market_snapshots where id = ?",
        (snapshot_id,),
    )
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["price"] == pytest.approx(150.25)
    assert rows[0]["rsi"] == pytest.approx(62.5)
    assert rows[0]["sma200"] is None


def test_decision_stores_the_raw_model_output(db, portfolio, cycle):
    proposal = Proposal(
        symbol="AAPL", kind="entry", action="buy", conviction=77,
        thesis="Ruptura de resistencia.", risks="Resultados la semana que viene.",
        reference_price=150.0, model="test-model",
        raw_response={"raw_text": "<think>...</think>", "parsed": {"action": "buy"}},
    )
    decision_id = db.save_decision(
        cycle_id=cycle, portfolio_id=portfolio, proposal=proposal
    )

    rows = db.query(
        "select action, conviction, "
        "json_extract(raw_response_json, '$.parsed.action') as parsed_action "
        "from decisions where id = ?",
        (decision_id,),
    )
    assert rows[0]["action"] == "buy"
    assert rows[0]["conviction"] == 77
    assert rows[0]["parsed_action"] == "buy"


def test_rejected_risk_events_are_recorded(db, portfolio, cycle):
    verdict = RiskVerdict(
        approved=False, reason="Conviccion insuficiente.", rule="min_conviction"
    )
    db.save_risk_event(
        cycle_id=cycle, portfolio_id=portfolio, symbol="AAPL", verdict=verdict
    )

    rows = db.query("select verdict, rule from risk_events")
    assert rows[0]["verdict"] == "rejected"
    assert rows[0]["rule"] == "min_conviction"


def test_only_one_open_position_per_symbol_is_allowed(db, portfolio):
    db.open_position(
        portfolio_id=portfolio, symbol="AAPL", qty=10, entry_price=100.0,
        stop_price=96.0, target_price=110.0, thesis="t",
    )

    with pytest.raises(DatabaseError):
        db.open_position(
            portfolio_id=portfolio, symbol="AAPL", qty=5, entry_price=101.0,
            stop_price=97.0, target_price=111.0, thesis="t",
        )


def test_a_symbol_can_be_reopened_after_closing(db, portfolio):
    first = db.open_position(
        portfolio_id=portfolio, symbol="AAPL", qty=10, entry_price=100.0,
        stop_price=96.0, target_price=110.0, thesis="t",
    )
    db.close_position(
        first, exit_price=110.0, realized_pnl=100.0, exit_reason="objetivo"
    )

    second = db.open_position(
        portfolio_id=portfolio, symbol="AAPL", qty=5, entry_price=111.0,
        stop_price=105.0, target_price=120.0, thesis="t2",
    )
    assert second != first
    assert set(db.get_open_positions(portfolio)) == {"AAPL"}


def test_stop_can_be_updated_without_touching_the_target(db, portfolio):
    position_id = db.open_position(
        portfolio_id=portfolio, symbol="AAPL", qty=10, entry_price=100.0,
        stop_price=96.0, target_price=110.0, thesis="t",
    )
    db.update_position_levels(position_id, stop_price=99.0)

    row = db.get_open_positions(portfolio)["AAPL"]
    assert row["stop_price"] == pytest.approx(99.0)
    assert row["target_price"] == pytest.approx(110.0)


# -- Reconciliacion ----------------------------------------------------------

def test_reconcile_closes_positions_absent_from_the_broker(db, portfolio):
    db.open_position(
        portfolio_id=portfolio, symbol="AAPL", qty=10, entry_price=100.0,
        stop_price=96.0, target_price=110.0, thesis="t",
    )

    report = db.reconcile(portfolio_id=portfolio, broker_positions={})

    assert report.closed_missing == ["AAPL"]
    assert db.get_open_positions(portfolio) == {}


def test_reconcile_adopts_orphan_broker_positions(db, portfolio):
    report = db.reconcile(
        portfolio_id=portfolio, broker_positions={"MSFT": broker_position("MSFT")}
    )

    assert [symbol for symbol, _ in report.adopted_orphans] == ["MSFT"]
    row = db.get_open_positions(portfolio)["MSFT"]
    # Adopted with no stop: the cycle assigns one by ATR right afterwards.
    assert row["stop_price"] is None


def test_reconcile_resyncs_quantity_from_the_broker(db, portfolio):
    db.open_position(
        portfolio_id=portfolio, symbol="AAPL", qty=10, entry_price=100.0,
        stop_price=96.0, target_price=110.0, thesis="t",
    )

    report = db.reconcile(
        portfolio_id=portfolio,
        broker_positions={"AAPL": broker_position("AAPL", qty=7.0, entry=101.5)},
    )

    assert report.resynced == ["AAPL"]
    row = db.get_open_positions(portfolio)["AAPL"]
    assert row["qty"] == pytest.approx(7.0)
    assert row["entry_price"] == pytest.approx(101.5)


def test_reconcile_leaves_matching_positions_untouched(db, portfolio):
    db.open_position(
        portfolio_id=portfolio, symbol="AAPL", qty=10, entry_price=100.0,
        stop_price=96.0, target_price=110.0, thesis="t",
    )

    report = db.reconcile(
        portfolio_id=portfolio,
        broker_positions={"AAPL": broker_position("AAPL", qty=10.0, entry=100.0)},
    )

    assert not report.had_discrepancies


# -- Vistas de analitica -----------------------------------------------------

def test_performance_view_aggregates_closed_positions(db, portfolio):
    winner = db.open_position(
        portfolio_id=portfolio, symbol="AAPL", qty=10, entry_price=100.0,
        stop_price=96.0, target_price=110.0, thesis="t",
    )
    db.close_position(winner, exit_price=110.0, realized_pnl=100.0, exit_reason="objetivo")

    loser = db.open_position(
        portfolio_id=portfolio, symbol="AAPL", qty=10, entry_price=110.0,
        stop_price=106.0, target_price=120.0, thesis="t",
    )
    db.close_position(loser, exit_price=106.0, realized_pnl=-40.0, exit_reason="stop")

    rows = db.query(
        "select * from v_performance_by_symbol where portfolio_id = ?", (portfolio,)
    )
    assert rows[0]["trades"] == 2
    assert rows[0]["wins"] == 1
    assert rows[0]["win_rate_pct"] == pytest.approx(50.0)
    assert rows[0]["total_pnl"] == pytest.approx(60.0)


def test_risk_rejection_view_counts_by_rule(db, portfolio, cycle):
    for rule in ("min_conviction", "min_conviction", "insufficient_cash"):
        db.save_risk_event(
            cycle_id=cycle, portfolio_id=portfolio, symbol="AAPL",
            verdict=RiskVerdict(approved=False, reason="r", rule=rule),
        )

    rows = db.query(
        "select * from v_risk_rejections where portfolio_id = ?", (portfolio,)
    )
    counts = {row["rule"]: row["rejections"] for row in rows}
    assert counts == {"min_conviction": 2, "insufficient_cash": 1}


def test_equity_snapshots_are_appended(db, portfolio, cycle):
    for equity in (10_000.0, 10_150.0):
        db.save_equity_snapshot(
            portfolio_id=portfolio, cycle_id=cycle, equity=equity, cash=5_000.0,
            positions_value=equity - 5_000.0, open_positions=1,
            day_pnl=equity - 10_000.0, day_pnl_pct=(equity / 10_000.0 - 1) * 100,
        )

    rows = db.query(
        "select equity from equity_snapshots where portfolio_id = ? order by id",
        (portfolio,),
    )
    assert [row["equity"] for row in rows] == [10_000.0, 10_150.0]
