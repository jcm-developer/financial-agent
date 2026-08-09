"""Tests of the dashboard's data assembly.

They matter because the frontend and the `report` command consume this same
payload: a mistake here shows in both views at once. The derived metrics (profit
factor, maximum drawdown, hit rate) are checked against numbers worked out by
hand, not against whatever the code returns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.dashboard import build_dashboard, list_portfolios
from src.db import Database, DatabaseError
from src.models import MarketSnapshot, Proposal, RiskVerdict


@pytest.fixture
def db(tmp_path):
    with Database(path=tmp_path / "dash.db") as database:
        yield database


@pytest.fixture
def portfolio(db):
    return db.ensure_portfolio(name="test", mode="paper", initial_budget=10_000.0)


def a_cycle(db, portfolio_id, *, equity=10_000.0):
    return db.start_cycle(
        portfolio_id=portfolio_id, equity_start=equity, cash_start=equity,
        market_open=True, symbols=["AAPL"], llm_model="m",
    )


def a_closed_trade(db, portfolio_id, *, symbol, pnl, entry=100.0, qty=10.0, days=3):
    """Creates a closed position with exactly the P&L it is asked for."""
    position_id = db.open_position(
        portfolio_id=portfolio_id, symbol=symbol, qty=qty, entry_price=entry,
        stop_price=entry * 0.96, target_price=entry * 1.1, thesis="t",
    )
    exit_price = entry + pnl / qty
    db.close_position(
        position_id, exit_price=exit_price, realized_pnl=pnl, exit_reason="test"
    )
    opened = datetime.now(timezone.utc) - timedelta(days=days)
    db.execute(
        "update positions set opened_at = ? where id = ?",
        (opened.isoformat(), position_id),
    )
    return position_id


def an_equity_point(db, portfolio_id, cycle_id, *, equity, when=None):
    db.save_equity_snapshot(
        portfolio_id=portfolio_id, cycle_id=cycle_id, equity=equity, cash=equity,
        positions_value=0.0, open_positions=0, day_pnl=0.0, day_pnl_pct=0.0,
    )
    if when is not None:
        db.execute(
            "update equity_snapshots set as_of = ? where cycle_id = ?",
            (when.isoformat(), cycle_id),
        )


# -- Cartera ausente ---------------------------------------------------------

def test_unknown_portfolio_returns_a_message_not_an_exception(db):
    payload = build_dashboard(db, portfolio_name="no-existe")

    assert payload["portfolio"] is None
    assert "run.py cycle" in payload["message"]


def test_list_portfolios_counts_cycles(db, portfolio):
    a_cycle(db, portfolio)
    a_cycle(db, portfolio)

    rows = list_portfolios(db)

    assert rows[0]["name"] == "test"
    assert rows[0]["cycles"] == 2


# -- Estructura del payload --------------------------------------------------

def test_payload_exposes_every_section_the_frontend_reads(db, portfolio):
    """The frontend indexes these keys directly; if one is missing, the page
    blows up while rendering instead of showing a gap."""
    cycle = a_cycle(db, portfolio)
    an_equity_point(db, portfolio, cycle, equity=10_000.0)

    payload = build_dashboard(db, portfolio_name="test")

    for key in (
        "portfolio", "summary", "equity_curve", "cycles", "open_positions",
        "closed_positions", "performance_by_symbol", "calibration",
        "rejections", "decisions", "orders", "conviction_histogram",
    ):
        assert key in payload, key


def test_empty_portfolio_yields_null_metrics_not_crashes(db, portfolio):
    payload = build_dashboard(db, portfolio_name="test")

    assert payload["summary"]["equity"] is None
    assert payload["summary"]["win_rate_pct"] is None
    assert payload["equity_curve"] == []


# -- Metricas ----------------------------------------------------------------

def test_win_rate_and_realized_pnl(db, portfolio):
    a_closed_trade(db, portfolio, symbol="AAPL", pnl=100.0)
    a_closed_trade(db, portfolio, symbol="MSFT", pnl=-40.0)
    a_closed_trade(db, portfolio, symbol="NVDA", pnl=-10.0)

    summary = build_dashboard(db, portfolio_name="test")["summary"]

    assert summary["closed_trades"] == 3
    assert summary["wins"] == 1
    assert summary["losses"] == 2
    assert summary["win_rate_pct"] == pytest.approx(33.3, abs=0.1)
    assert summary["realized_pnl"] == pytest.approx(50.0)


def test_profit_factor_is_gross_win_over_gross_loss(db, portfolio):
    """100 de beneficio bruto contra 50 de perdida bruta -> 2.0."""
    a_closed_trade(db, portfolio, symbol="AAPL", pnl=100.0)
    a_closed_trade(db, portfolio, symbol="MSFT", pnl=-50.0)

    summary = build_dashboard(db, portfolio_name="test")["summary"]

    assert summary["profit_factor"] == pytest.approx(2.0)
    assert summary["avg_win"] == pytest.approx(100.0)
    assert summary["avg_loss"] == pytest.approx(-50.0)


def test_profit_factor_below_one_signals_a_losing_system(db, portfolio):
    """Two wins out of three and money still lost: it is exactly the case the
    profit factor catches and the hit rate hides."""
    a_closed_trade(db, portfolio, symbol="AAPL", pnl=10.0)
    a_closed_trade(db, portfolio, symbol="MSFT", pnl=10.0)
    a_closed_trade(db, portfolio, symbol="NVDA", pnl=-100.0)

    summary = build_dashboard(db, portfolio_name="test")["summary"]

    assert summary["win_rate_pct"] == pytest.approx(66.7, abs=0.1)
    assert summary["profit_factor"] == pytest.approx(0.2)


def test_profit_factor_is_none_without_trades(db, portfolio):
    assert build_dashboard(db, portfolio_name="test")["summary"]["profit_factor"] is None


def test_max_drawdown_measures_the_worst_fall_from_a_peak(db, portfolio):
    """10000 -> 12000 -> 9000: the drop from the peak is 25%, not the 10%
    comparing only against the start would suggest."""
    now = datetime.now(timezone.utc)
    for index, equity in enumerate([10_000.0, 12_000.0, 9_000.0, 11_000.0]):
        cycle = a_cycle(db, portfolio)
        an_equity_point(db, portfolio, cycle, equity=equity,
                        when=now + timedelta(hours=index))

    summary = build_dashboard(db, portfolio_name="test")["summary"]

    assert summary["max_drawdown_pct"] == pytest.approx(-25.0)


def test_total_return_is_measured_from_the_first_recorded_cycle(db, portfolio):
    now = datetime.now(timezone.utc)
    for index, equity in enumerate([10_000.0, 11_500.0]):
        cycle = a_cycle(db, portfolio)
        an_equity_point(db, portfolio, cycle, equity=equity,
                        when=now + timedelta(hours=index))

    summary = build_dashboard(db, portfolio_name="test")["summary"]

    assert summary["equity"] == pytest.approx(11_500.0)
    assert summary["total_return_pct"] == pytest.approx(15.0)


# -- Posiciones abiertas -----------------------------------------------------

def test_open_position_is_valued_with_the_last_recorded_price(db, portfolio):
    cycle = a_cycle(db, portfolio)
    db.save_snapshot(cycle_id=cycle, snapshot=MarketSnapshot(
        symbol="AAPL", as_of=datetime(2026, 6, 1, tzinfo=timezone.utc),
        price=110.0, indicators={"atr_14": 2.0},
    ))
    db.open_position(
        portfolio_id=portfolio, symbol="AAPL", qty=10, entry_price=100.0,
        stop_price=96.0, target_price=120.0, thesis="t",
    )

    position = build_dashboard(db, portfolio_name="test")["open_positions"][0]

    assert position["last_price"] == pytest.approx(110.0)
    assert position["market_value"] == pytest.approx(1100.0)
    assert position["unrealized_pnl"] == pytest.approx(100.0)
    assert position["unrealized_pnl_pct"] == pytest.approx(10.0)
    # 110 is 14.58% above the stop at 96.
    assert position["stop_distance_pct"] == pytest.approx(14.58, abs=0.01)


def test_open_position_without_a_price_reports_nulls_not_zeros(db, portfolio):
    """A zero would read as "it is worth nothing"; None is shown as a dash."""
    db.open_position(
        portfolio_id=portfolio, symbol="ZZZZ", qty=10, entry_price=100.0,
        stop_price=96.0, target_price=120.0, thesis="t",
    )

    position = build_dashboard(db, portfolio_name="test")["open_positions"][0]

    assert position["last_price"] is None
    assert position["unrealized_pnl"] is None
    assert position["market_value"] is None


# -- Decisiones --------------------------------------------------------------

def test_decisions_join_their_risk_verdict(db, portfolio):
    cycle = a_cycle(db, portfolio)
    decision_id = db.save_decision(
        cycle_id=cycle, portfolio_id=portfolio,
        proposal=Proposal(
            symbol="AAPL", kind="entry", action="buy", conviction=80,
            thesis="Ruptura.", reference_price=100.0,
        ),
    )
    db.save_risk_event(
        cycle_id=cycle, portfolio_id=portfolio, symbol="AAPL", decision_id=decision_id,
        verdict=RiskVerdict(
            approved=False, reason="Sin efectivo.", rule="insufficient_cash"
        ),
    )

    decision = build_dashboard(db, portfolio_name="test")["decisions"][0]

    assert decision["action"] == "buy"
    assert decision["verdict"] == "rejected"
    assert decision["rule"] == "insufficient_cash"


def test_conviction_histogram_separates_buys_from_holds(db, portfolio):
    cycle = a_cycle(db, portfolio)
    for action, conviction in [("buy", 82), ("hold", 41), ("hold", 45)]:
        db.save_decision(
            cycle_id=cycle, portfolio_id=portfolio,
            proposal=Proposal(
                symbol="AAPL", kind="entry", action=action, conviction=conviction,
                thesis="t", reference_price=100.0,
            ),
        )

    buckets = {
        row["bucket"]: row
        for row in build_dashboard(db, portfolio_name="test")["conviction_histogram"]
    }

    assert buckets[80]["buys"] == 1
    assert buckets[40]["holds"] == 2


def test_cycles_expose_the_scanned_symbol_list_as_a_list(db, portfolio):
    """It is stored as JSON in a TEXT column; the frontend counts its length."""
    a_cycle(db, portfolio)

    cycle = build_dashboard(db, portfolio_name="test")["cycles"][0]

    assert cycle["symbols_scanned"] == ["AAPL"]


# -- Solo lectura ------------------------------------------------------------

def test_read_only_connection_refuses_writes(tmp_path):
    path = tmp_path / "ro.db"
    with Database(path=path) as writable:
        writable.ensure_portfolio(name="p", mode="paper", initial_budget=1000.0)

    with Database(path=path, read_only=True) as reader:
        assert reader.query("select count(*) as n from portfolios")[0]["n"] == 1
        with pytest.raises(DatabaseError):
            reader.execute("delete from portfolios")


def test_read_only_on_a_missing_file_explains_what_to_do(tmp_path):
    with pytest.raises(DatabaseError, match="run.py cycle"):
        Database(path=tmp_path / "nope.db", read_only=True)
