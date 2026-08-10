"""A whole cycle end to end, without network.

Only the two external boundaries are replaced — the model and the bar download —
and everything else is left to run for real: analyst, Risk Manager, simulated
broker and database. It is the proof that the pieces fit together, which is
exactly what the unit tests do not cover.
"""

from __future__ import annotations

import pytest

from helpers import (
    BUY,
    HOLD_EXIT,
    SELL_EXIT,
    WATCHLIST,
    StubLLM,
    StubMarketData,
    make_cycle,
    make_settings,
    rising,
)
from src import stop_signal
from src.config import RiskLimits
from src.cycle import CycleReport
from src.db import Database



# ----------------------------------------------------------------------
# Casos
# ----------------------------------------------------------------------

def test_the_summary_carries_the_profile_currency():
    """The summary is shown verbatim on the Ciclos screen, so it is screen text
    and the symbol travels with the figure (FE.8).

    It used to write `$` as a literal, so a European experiment reported
    "Equity: $10,000.00" — an amount you could compare with another book's as if
    they were the same unit. The two markets are asserted together because what
    broke was precisely assuming one of them.
    """
    numbers = dict(equity_start=10_000.0, equity_end=10_040.5)

    assert "Equity: €10,000.00 -> €10,040.50" in CycleReport(
        currency_symbol="€", **numbers
    ).summary()
    assert "Equity: $10,000.00 -> $10,040.50" in CycleReport(
        currency_symbol="$", **numbers
    ).summary()


def test_a_run_fills_the_currency_from_the_profiles_market(db):
    """The wiring, which is the half a direct test of `summary()` does not see:
    it is `run()` that has to look the market up."""
    report = make_cycle(
        db, make_settings(), StubLLM(entry=BUY, exit_=HOLD_EXIT),
        StubMarketData({s: rising() for s in WATCHLIST}),
    ).run()

    assert report.currency_symbol == "$"
    assert "$" in report.summary()


def test_a_full_cycle_opens_positions_and_records_everything(db):
    settings = make_settings()
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, llm, market).run()

    assert report.status == "completed"
    assert report.analyzed == 2
    assert report.proposals_buy == 2
    assert report.approved == 2
    assert report.orders_submitted == 2

    # Recorded in both sets of books: the broker's and the bot's.
    assert len(db.query("select * from sim_positions")) == 2
    assert len(db.get_open_positions(report.cycle_id and _portfolio(db))) == 2
    # Y quedo rastro de decisiones, veredictos, ordenes y curva de capital.
    assert len(db.query("select * from decisions where action = 'buy'")) == 2
    assert len(db.query("select * from risk_events where verdict = 'approved'")) == 2
    assert len(db.query("select * from orders where status = 'filled'")) == 2
    assert len(db.query("select * from equity_snapshots")) == 1


def test_execution_happens_at_the_next_open_not_the_decision_close(db):
    """The check that holds the whole experiment up: the order fills at the
    following open, which is below the close the decision was made on."""
    settings = make_settings(watchlist=("AAPL",))
    market = StubMarketData({"AAPL": rising()})
    make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    snapshot = market.fetch_snapshots(["AAPL"])["AAPL"]
    order = db.query("select * from orders where symbol = 'AAPL'")[0]

    assert order["filled_avg_price"] == pytest.approx(snapshot.fill_price, abs=0.01)
    assert order["filled_avg_price"] != pytest.approx(snapshot.price, abs=0.01)


def test_position_size_never_exceeds_the_configured_cash(db):
    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})

    make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    account_row = db.query("select cash from sim_accounts")[0]
    assert account_row["cash"] >= 0
    equity = db.query("select equity from equity_snapshots order by id desc limit 1")[0]
    # With no leverage, equity cannot grow just by buying.
    assert equity["equity"] == pytest.approx(10_000.0, abs=60.0)


def test_hold_produces_no_order(db):
    settings = make_settings()
    hold = {**BUY, "action": "hold"}
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, StubLLM(entry=hold, exit_=HOLD_EXIT), market).run()

    assert report.proposals_buy == 0
    assert report.orders_submitted == 0
    assert db.query("select * from sim_positions") == []


def test_low_conviction_is_rejected_by_risk_not_executed(db):
    settings = make_settings()
    timid = {**BUY, "conviction": 40}
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, StubLLM(entry=timid, exit_=HOLD_EXIT), market).run()

    assert report.rejected == 2
    assert report.orders_submitted == 0
    rules = {r["rule"] for r in db.query("select rule from risk_events")}
    assert rules == {"min_conviction"}


def test_dry_run_analyses_but_sends_nothing(db):
    settings = make_settings(dry_run=True)
    market = StubMarketData({s: rising() for s in WATCHLIST})

    report = make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    assert report.approved == 2
    assert report.orders_submitted == 0
    assert db.query("select * from sim_positions") == []
    # The order is recorded as not sent, with its reason.
    orders = db.query("select status from orders")
    assert {o["status"] for o in orders} == {"dry_run"}


def test_a_crash_triggers_the_stop_without_consulting_the_model(db):
    """The first cycle buys; then the price sinks below the stop and the close
    executes without asking the analyst.

    The crash is added TWICE: the first new bar is the decision session (where the
    agent sees the fall) and the second is the execution one.
    """
    settings = make_settings(watchlist=("AAPL",))
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)

    make_cycle(db, settings, llm, StubMarketData({"AAPL": rising()})).run()
    assert len(db.query("select * from sim_positions")) == 1

    crash_price = rising()[-1] * 0.7
    crashed = rising() + [crash_price, crash_price]
    report = make_cycle(db, settings, llm, StubMarketData({"AAPL": crashed})).run()

    assert report.exits_forced == 1
    assert report.exits_discretionary == 0   # el modelo no ha intervenido
    assert db.query("select * from sim_positions") == []
    closed = db.query("select * from positions where status = 'closed'")
    assert len(closed) == 1
    assert "stop_loss_hit" in closed[0]["exit_reason"]
    assert closed[0]["realized_pnl"] < 0


def test_a_crash_in_the_execution_bar_is_not_visible_yet(db):
    """Counterpart of the previous one, and a central property of the design: a
    fall that only appears in the execution bar has not been seen by the agent
    yet, so the stop cannot fire. Reacting earlier would be trading with
    information from the future, which is exactly what invalidates a backtest."""
    settings = make_settings(watchlist=("AAPL",))
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)

    make_cycle(db, settings, llm, StubMarketData({"AAPL": rising()})).run()

    # A single new bar: it is the execution one, not the decision one.
    crashed = rising() + [rising()[-1] * 0.7]
    report = make_cycle(db, settings, llm, StubMarketData({"AAPL": crashed})).run()

    assert report.exits_forced == 0
    assert len(db.query("select * from sim_positions")) == 1


def test_the_analyst_can_close_a_position_on_a_degraded_thesis(db):
    settings = make_settings(watchlist=("AAPL",))
    market = StubMarketData({"AAPL": rising()})

    make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    # Segundo ciclo: mismo precio, pero el analista pide salir.
    longer = rising(81)
    report = make_cycle(
        db, settings, StubLLM(entry=BUY, exit_=SELL_EXIT), StubMarketData({"AAPL": longer})
    ).run()

    assert report.exits_discretionary == 1
    assert db.query("select * from sim_positions") == []


def test_the_kill_switch_stops_new_entries_after_a_daily_loss(db):
    """The daily P&L reference is raised by hand to simulate a session already
    deep in the red. The SAME session is repeated on purpose: if it changed,
    `roll_session` would recalibrate the reference and the loss would vanish."""
    settings = make_settings(
        watchlist=("AAPL", "MSFT"),
        risk=RiskLimits(min_conviction=65, max_daily_loss_pct=3.0),
    )
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    closes = rising()
    make_cycle(db, settings, llm, StubMarketData({"AAPL": closes})).run()

    db.execute("update sim_accounts set last_equity = 20000")

    report = make_cycle(db, settings, llm, StubMarketData({"AAPL": closes})).run()

    assert report.status == "halted"
    assert report.halted_reason is not None
    assert report.approved == 0
    # Y quedo registrado el motivo, para poder verlo despues en el dashboard.
    rules = {r["rule"] for r in db.query("select rule from risk_events")}
    assert "max_daily_loss_pct" in rules


def test_a_new_session_resets_the_daily_loss_reference(db):
    """Complement of the previous one: the kill switch is daily, not cumulative.
    On a new session the reference moves and the agent trades again."""
    settings = make_settings(
        watchlist=("AAPL",), risk=RiskLimits(min_conviction=65, max_daily_loss_pct=3.0),
    )
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    make_cycle(db, settings, llm, StubMarketData({"AAPL": rising()})).run()

    db.execute("update sim_accounts set last_equity = 20000")

    # Sesion siguiente: una barra mas.
    report = make_cycle(db, settings, llm, StubMarketData({"AAPL": rising(81)})).run()

    assert report.status == "completed"
    assert report.halted_reason is None


def test_max_open_positions_is_respected_across_the_watchlist(db):
    settings = make_settings(
        watchlist=("AAPL", "MSFT", "NVDA"),
        risk=RiskLimits(min_conviction=65, max_open_positions=2),
    )
    market = StubMarketData({s: rising() for s in ("AAPL", "MSFT", "NVDA")})

    make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    assert len(db.query("select * from sim_positions")) == 2


def test_a_second_cycle_does_not_reopen_the_same_symbol(db):
    settings = make_settings(watchlist=("AAPL",))
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)

    make_cycle(db, settings, llm, StubMarketData({"AAPL": rising()})).run()
    report = make_cycle(db, settings, llm, StubMarketData({"AAPL": rising(81)})).run()

    assert len(db.query("select * from sim_positions")) == 1
    assert report.orders_submitted == 0


def test_symbols_without_data_are_skipped_not_guessed(db):
    settings = make_settings(watchlist=("AAPL", "SINDATOS"))
    market = StubMarketData({"AAPL": rising()})   # SINDATOS no aparece

    report = make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    assert report.analyzed == 1
    assert {p["symbol"] for p in db.query("select symbol from sim_positions")} == {"AAPL"}


def test_the_dashboard_payload_reflects_a_real_cycle(db):
    """It closes the circle: what the cycle writes is what the dashboard reads."""
    from src.dashboard import build_dashboard

    settings = make_settings()
    market = StubMarketData({s: rising() for s in WATCHLIST})
    make_cycle(db, settings, StubLLM(entry=BUY, exit_=HOLD_EXIT), market).run()

    payload = build_dashboard(db, portfolio_name="integracion")

    assert payload["portfolio"]["name"] == "integracion"
    assert payload["summary"]["open_positions"] == 2
    assert len(payload["equity_curve"]) == 1
    assert len(payload["decisions"]) == 2
    assert payload["summary"]["equity"] is not None


def _portfolio(db: Database) -> str:
    return db.query("select id from portfolios limit 1")[0]["id"]


# -- Una posicion que se queda sin precio ------------------------------------

def test_a_position_with_no_price_is_reported_and_not_left_silent(db):
    """The quietest failure of the cycle: an open position with no quote.

    Yahoo stops serving the symbol, a local holiday closes one of the six
    exchanges, a suffix goes bad — and three things break at once without a word:
    `SimBroker._mark` values it at its entry price, so `mandatory_exits` compares
    that frozen price against the stop and it can never breach it, and the
    discretionary review skips a symbol with no snapshot.

    The cycle used to finish `completed` with nothing said.
    """
    settings = make_settings()
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    market = StubMarketData({s: rising() for s in WATCHLIST})
    cycle = make_cycle(db, settings, llm, market)
    assert cycle.run().orders_submitted == 2

    # MSFT stops having bars: it is still held, but this cycle gets no price.
    mudo = StubMarketData({"AAPL": rising()})
    report = make_cycle(db, settings, llm, mudo).run()

    assert report.positions_without_price == ["MSFT"]
    assert "SIN PRECIO" in report.summary()
    assert "MSFT" in report.summary()
    # And it is not an error: the cycle did its job with everything else.
    assert report.status == "completed"


def test_the_absence_of_price_leaves_a_trace_in_the_history(db):
    """The screen already said it; the history did not, and the history is what
    gets read afterwards."""
    settings = make_settings()
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    market = StubMarketData({s: rising() for s in WATCHLIST})
    cycle = make_cycle(db, settings, llm, market)
    cycle.run()

    report = make_cycle(db, settings, llm, StubMarketData({"AAPL": rising()})).run()

    events = db.query(
        "select * from risk_events where cycle_id = ? and rule = 'no_price'",
        (report.cycle_id,),
    )
    assert len(events) == 1
    assert events[0]["symbol"] == "MSFT"
    # `rejected` so it shows up in the rejections-by-rule chart, which is where
    # an absence like this has to be visible.
    assert events[0]["verdict"] == "rejected"


def test_the_position_is_not_closed_blind(db):
    """Selling at a price we precisely do not have would be worse than holding.

    What changes is that it now shouts; the decision to close is left to whoever
    reads the warning.
    """
    settings = make_settings()
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    market = StubMarketData({s: rising() for s in WATCHLIST})
    cycle = make_cycle(db, settings, llm, market)
    cycle.run()

    make_cycle(db, settings, llm, StubMarketData({"AAPL": rising()})).run()

    assert "MSFT" in db.get_open_positions(cycle.portfolio_id)


def test_nothing_is_reported_when_every_position_has_its_price(db):
    """A warning that cries wolf stops being read."""
    settings = make_settings()
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    market = StubMarketData({s: rising() for s in WATCHLIST})
    cycle = make_cycle(db, settings, llm, market)
    cycle.run()

    report = make_cycle(db, settings, llm, market).run()

    assert report.positions_without_price == []
    assert "SIN PRECIO" not in report.summary()


# ----------------------------------------------------------------------
# F4.21 -- Parada pedida desde la interfaz
# ----------------------------------------------------------------------

def test_a_stop_asked_for_mid_cycle_is_honoured_and_the_row_is_closed(db, tmp_path):
    """The request is written while the analyst is being asked, which is where the
    twenty minutes of a cycle go and therefore where Parar is pressed.

    Three things are asserted together because they are the whole promise of a
    cooperative stop, and a signal gives none of them: **the candidates after the
    request are not analysed**, **the row is closed** —left in 'running' it would
    block the next cycle for the 90 minutes of `STALE_CYCLE_MINUTES`— and **the
    reason is recorded**, which is what tells this 'halted' from the kill switch's.
    """
    settings = make_settings(db_path=str(tmp_path / "test.db"))
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)
    answer = llm.complete_json

    def press_stop(**kwargs):
        running = db.query("select id from cycles where status = 'running'")
        stop_signal.request(settings.db_path, running[0]["id"])
        return answer(**kwargs)

    llm.complete_json = press_stop

    report = make_cycle(
        db, settings, llm, StubMarketData({s: rising() for s in WATCHLIST})
    ).run()

    assert report.stopped is True
    assert report.status == "halted"
    # "PARADA" and not "KILL SWITCH": the summary is read on screen and those are
    # opposite readings of the same short cycle.
    assert "PARADA: Parada solicitada desde la interfaz." in report.summary()
    # Only the first of the two candidates was asked about: the checkpoint before
    # the second call honoured the request.
    assert llm.calls == ["entry"]

    row = db.query("select status, finished_at, error from cycles")[0]
    assert row["status"] == "halted"
    assert row["finished_at"] is not None
    assert "Parada solicitada" in row["error"]

    # What it had already done stands, and that is deliberate: the position is
    # open, with its stop, and undoing it would be trading on nobody's decision.
    assert report.orders_submitted == 1
    assert len(db.get_open_positions(_portfolio(db))) == 1

    # And the request is gone, so the scheduler's next cycle is not stopped too.
    assert stop_signal.pending(settings.db_path) is None


def test_a_request_left_over_from_an_earlier_cycle_does_not_stop_this_one(db, tmp_path):
    """A Parar that arrives a second after the cycle ends leaves the file behind.
    Honouring it later would look like the scheduler skipping a session."""
    settings = make_settings(db_path=str(tmp_path / "test.db"))
    stop_signal.request(settings.db_path, "ciclo-de-ayer")

    report = make_cycle(
        db, make_settings(db_path=settings.db_path),
        StubLLM(entry=BUY, exit_=HOLD_EXIT),
        StubMarketData({s: rising() for s in WATCHLIST}),
    ).run()

    assert report.stopped is False
    assert report.status == "completed"
    # Cleared on registering: at that instant no pending request can be for this
    # cycle, so leaving it would only wait to stop the wrong one.
    assert stop_signal.pending(settings.db_path) is None
