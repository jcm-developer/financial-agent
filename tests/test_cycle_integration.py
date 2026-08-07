"""Ciclo completo de punta a punta, sin red.

Se sustituyen solo las dos fronteras externas — el modelo y la descarga de barras
— y se deja correr todo lo demas de verdad: analista, Risk Manager, broker
simulado y base de datos. Es la prueba de que las piezas encajan, que es
justamente lo que los tests unitarios no cubren.
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
from src.config import RiskLimits
from src.db import Database



# ----------------------------------------------------------------------
# Casos
# ----------------------------------------------------------------------

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

    # Se registro en las dos contabilidades: la del broker y la del bot.
    assert len(db.query("select * from sim_positions")) == 2
    assert len(db.get_open_positions(report.cycle_id and _portfolio(db))) == 2
    # Y quedo rastro de decisiones, veredictos, ordenes y curva de capital.
    assert len(db.query("select * from decisions where action = 'buy'")) == 2
    assert len(db.query("select * from risk_events where verdict = 'approved'")) == 2
    assert len(db.query("select * from orders where status = 'filled'")) == 2
    assert len(db.query("select * from equity_snapshots")) == 1


def test_execution_happens_at_the_next_open_not_the_decision_close(db):
    """La comprobacion que sostiene todo el experimento: la orden se llena al
    precio de apertura siguiente, que es inferior al cierre con el que se decidio."""
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
    # Sin apalancamiento, el equity no puede crecer solo por comprar.
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
    # La orden queda registrada como no enviada, con su motivo.
    orders = db.query("select status from orders")
    assert {o["status"] for o in orders} == {"dry_run"}


def test_a_crash_triggers_the_stop_without_consulting_the_model(db):
    """Primer ciclo compra; despues el precio se hunde por debajo del stop y el
    cierre se ejecuta sin preguntar al analista.

    El desplome se anade DOS veces: la primera barra nueva es la sesion de
    decision (donde el agente ve la caida) y la segunda es la de ejecucion.
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
    """Contrapartida del anterior, y propiedad central del diseno: una caida que
    solo aparece en la barra de ejecucion todavia no la ha visto el agente, asi
    que el stop no puede saltar. Reaccionar antes seria operar con informacion
    del futuro, que es exactamente lo que invalida un backtest."""
    settings = make_settings(watchlist=("AAPL",))
    llm = StubLLM(entry=BUY, exit_=HOLD_EXIT)

    make_cycle(db, settings, llm, StubMarketData({"AAPL": rising()})).run()

    # Una sola barra nueva: es la de ejecucion, no la de decision.
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
    """La referencia del P&L diario se eleva a mano para simular una sesion que
    ya va muy perdida. Se repite la MISMA sesion a proposito: si cambiara,
    `roll_session` recalibraria la referencia y la perdida desapareceria."""
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
    """Complemento del anterior: el kill switch es diario, no acumulado. Al
    empezar sesion nueva la referencia se mueve y el agente vuelve a operar."""
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
    """Cierra el circulo: lo que el ciclo escribe es lo que el dashboard lee."""
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
