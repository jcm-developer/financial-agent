"""Tests del broker simulado.

El simulador es el que sustituye a la cuenta de broker, asi que si es optimista
todo el experimento queda invalidado. Lo que mas se prueba aqui es precisamente
eso: que no se pueda gastar dinero que no hay, que el deslizamiento vaya siempre
en contra, y que se ejecute a la apertura siguiente y no al cierre con el que se
decidio.
"""

from __future__ import annotations

import pytest

from src.broker import BrokerError
from src.db import Database
from src.sim_broker import Quote, SimBroker


@pytest.fixture
def db(tmp_path):
    with Database(path=tmp_path / "sim.db") as database:
        yield database


@pytest.fixture
def portfolio(db):
    return db.ensure_portfolio(name="test", mode="paper", initial_budget=10_000.0)


@pytest.fixture
def broker(db, portfolio):
    """Sin deslizamiento ni comision: los tests que los miden los activan."""
    return SimBroker(
        database=db, portfolio_id=portfolio, initial_cash=10_000.0,
        slippage_bps=0.0, commission_per_order=0.0,
    )


def quote(fill=100.0, mark=None):
    return Quote(fill_price=fill, mark_price=mark if mark is not None else fill)


# -- Estado inicial ----------------------------------------------------------

def test_new_account_starts_with_the_configured_cash(broker):
    account = broker.get_account_state()

    assert account.cash == pytest.approx(10_000.0)
    assert account.equity == pytest.approx(10_000.0)
    assert account.positions == ()


def test_buying_power_equals_cash_because_there_is_no_leverage(broker):
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.buy_market("AAPL", 50)

    account = broker.get_account_state()

    assert account.cash == pytest.approx(5_000.0)
    assert account.buying_power == pytest.approx(account.cash)


def test_reopening_the_account_keeps_its_state(db, portfolio):
    first = SimBroker(database=db, portfolio_id=portfolio, initial_cash=10_000.0,
                      slippage_bps=0.0)
    first.set_quotes({"AAPL": quote(100.0)})
    first.buy_market("AAPL", 10)

    # Un ciclo posterior construye el broker de nuevo: el estado vive en SQLite.
    second = SimBroker(database=db, portfolio_id=portfolio, initial_cash=10_000.0,
                       slippage_bps=0.0)
    second.set_quotes({"AAPL": quote(100.0)})
    account = second.get_account_state()

    assert account.cash == pytest.approx(9_000.0)
    assert account.positions[0].symbol == "AAPL"
    assert account.positions[0].qty == pytest.approx(10.0)


# -- Compras -----------------------------------------------------------------

def test_buy_fills_at_the_next_open_not_at_the_decision_close(broker):
    """El punto central de la simulacion: se decide con el cierre (105) y se
    ejecuta con la apertura siguiente (100). Ejecutar a 105 regalaria el hueco."""
    broker.set_quotes({"AAPL": Quote(fill_price=100.0, mark_price=105.0)})

    order = broker.buy_market("AAPL", 10)

    assert order.filled_avg_price == pytest.approx(100.0)
    assert broker.get_account_state().cash == pytest.approx(9_000.0)


def test_buy_slippage_works_against_the_buyer(db, portfolio):
    """50 pb sobre 100 son 100.50: al comprar se paga mas, nunca menos."""
    broker = SimBroker(database=db, portfolio_id=portfolio, initial_cash=10_000.0,
                       slippage_bps=50.0)
    broker.set_quotes({"AAPL": quote(100.0)})

    order = broker.buy_market("AAPL", 10)

    assert order.filled_avg_price == pytest.approx(100.50)
    assert broker.get_account_state().cash == pytest.approx(10_000.0 - 1005.0)


def test_commission_is_charged_on_top(db, portfolio):
    broker = SimBroker(database=db, portfolio_id=portfolio, initial_cash=10_000.0,
                       slippage_bps=0.0, commission_per_order=1.5)
    broker.set_quotes({"AAPL": quote(100.0)})

    broker.buy_market("AAPL", 10)

    assert broker.get_account_state().cash == pytest.approx(10_000.0 - 1000.0 - 1.5)


def test_cannot_spend_more_cash_than_available(broker):
    broker.set_quotes({"AAPL": quote(100.0)})

    with pytest.raises(BrokerError, match="Efectivo insuficiente"):
        broker.buy_market("AAPL", 200)

    assert broker.get_account_state().cash == pytest.approx(10_000.0)


def test_buying_without_a_price_is_refused(broker):
    """Sin precio de ejecucion no se inventa uno."""
    broker.set_quotes({})

    with pytest.raises(BrokerError, match="No hay precio"):
        broker.buy_market("AAPL", 10)


def test_fractional_quantities_are_truncated_to_whole_shares(broker):
    broker.set_quotes({"AAPL": quote(100.0)})

    order = broker.buy_market("AAPL", 10.9)

    assert order.qty == pytest.approx(10.0)


def test_buying_less_than_one_share_is_refused(broker):
    broker.set_quotes({"AAPL": quote(100.0)})

    with pytest.raises(BrokerError, match="Cantidad invalida"):
        broker.buy_market("AAPL", 0.5)


# -- Ventas ------------------------------------------------------------------

def test_sell_slippage_also_works_against_the_seller(db, portfolio):
    broker = SimBroker(database=db, portfolio_id=portfolio, initial_cash=10_000.0,
                       slippage_bps=50.0)
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.buy_market("AAPL", 10)          # entra a 100.50

    broker.set_quotes({"AAPL": quote(120.0)})
    order = broker.sell_market("AAPL", 10)  # sale a 119.40

    assert order.filled_avg_price == pytest.approx(119.40)


def test_closing_a_position_realizes_the_profit_in_cash(broker):
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.buy_market("AAPL", 10)

    broker.set_quotes({"AAPL": quote(120.0)})
    broker.close_position("AAPL")

    account = broker.get_account_state()
    assert account.cash == pytest.approx(10_200.0)
    assert account.positions == ()


def test_closing_a_losing_position_realizes_the_loss(broker):
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.buy_market("AAPL", 10)

    broker.set_quotes({"AAPL": quote(90.0)})
    broker.close_position("AAPL")

    assert broker.get_account_state().cash == pytest.approx(9_900.0)


def test_realized_pnl_is_recorded_on_the_fill(db, portfolio, broker):
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.buy_market("AAPL", 10)
    broker.set_quotes({"AAPL": quote(115.0)})
    broker.close_position("AAPL")

    fills = db.query(
        "select side, price, realized_pnl from sim_fills where account_id = ? "
        "order by id", (portfolio,)
    )
    assert fills[0]["side"] == "buy"
    assert fills[0]["realized_pnl"] is None
    assert fills[1]["side"] == "sell"
    assert fills[1]["realized_pnl"] == pytest.approx(150.0)


def test_no_short_selling(broker):
    """Vender lo que no se tiene abriria un corto, que el simulador no modela."""
    broker.set_quotes({"AAPL": quote(100.0)})

    with pytest.raises(BrokerError, match="No hay posicion abierta"):
        broker.sell_market("AAPL", 10)


def test_cannot_sell_more_than_held(broker):
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.buy_market("AAPL", 10)

    with pytest.raises(BrokerError, match="corto"):
        broker.sell_market("AAPL", 20)


def test_partial_sale_leaves_the_rest_open(broker):
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.buy_market("AAPL", 10)

    broker.sell_market("AAPL", 4)

    position = broker.get_account_state().positions[0]
    assert position.qty == pytest.approx(6.0)


def test_closing_a_position_that_does_not_exist_is_refused(broker):
    broker.set_quotes({"AAPL": quote(100.0)})

    with pytest.raises(BrokerError, match="que cerrar"):
        broker.close_position("AAPL")


# -- Valoracion --------------------------------------------------------------

def test_positions_are_valued_at_the_mark_price(broker):
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.buy_market("AAPL", 10)

    # Nueva sesion: el precio de valoracion sube a 130.
    broker.set_quotes({"AAPL": Quote(fill_price=128.0, mark_price=130.0)})
    account = broker.get_account_state()

    position = account.positions[0]
    assert position.current_price == pytest.approx(130.0)
    assert position.unrealized_pl == pytest.approx(300.0)
    assert position.unrealized_pl_pct == pytest.approx(30.0)
    assert account.equity == pytest.approx(9_000.0 + 1_300.0)


def test_a_position_without_a_price_falls_back_to_its_entry(broker):
    """Sin cotizacion se vale al precio de entrada: no se inventa una valoracion
    ni se cuenta como cero, que seria peor."""
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.buy_market("AAPL", 10)

    broker.set_quotes({})
    account = broker.get_account_state()

    assert account.positions[0].current_price == pytest.approx(100.0)
    assert account.equity == pytest.approx(10_000.0)


# -- Sesiones y P&L diario ---------------------------------------------------

def test_day_pnl_is_measured_against_the_previous_session_close(broker):
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.roll_session("2026-08-06")
    broker.buy_market("AAPL", 10)

    # Sesion nueva: la referencia se fija con el equity de cierre anterior.
    broker.set_quotes({"AAPL": Quote(fill_price=110.0, mark_price=110.0)})
    broker.roll_session("2026-08-07")
    account = broker.get_account_state()

    assert account.last_equity == pytest.approx(10_100.0)
    assert account.day_pnl == pytest.approx(0.0)


def test_rolling_the_same_session_twice_does_not_move_the_reference(broker):
    """Si la referencia se reiniciase en cada ciclo del mismo dia, el kill switch
    de perdida diaria nunca llegaria a dispararse."""
    broker.set_quotes({"AAPL": quote(100.0)})
    broker.roll_session("2026-08-07")
    broker.buy_market("AAPL", 10)

    broker.set_quotes({"AAPL": Quote(fill_price=80.0, mark_price=80.0)})
    broker.roll_session("2026-08-07")      # mismo dia, no debe recalibrar
    account = broker.get_account_state()

    assert account.last_equity == pytest.approx(10_000.0)
    assert account.day_pnl == pytest.approx(-200.0)


# -- Cumplimiento del contrato de broker -------------------------------------

def test_is_tradable_only_with_a_price(broker):
    broker.set_quotes({"AAPL": quote(100.0)})

    assert broker.is_tradable("AAPL")
    assert not broker.is_tradable("MSFT")


def test_market_is_open_when_there_are_prices(broker):
    assert not broker.is_market_open()

    broker.set_quotes({"AAPL": quote(100.0)})

    assert broker.is_market_open()


def test_held_symbols_reports_open_positions(broker):
    broker.set_quotes({"AAPL": quote(100.0), "MSFT": quote(50.0)})
    broker.buy_market("AAPL", 5)
    broker.buy_market("MSFT", 5)

    assert broker.held_symbols() == {"AAPL", "MSFT"}


def test_sim_broker_cumple_el_protocolo_de_broker():
    """`cycle.py` solo conoce el protocolo de `broker.py`.

    Si el simulador se desvia de el, el dia que se anada un broker real el ciclo
    dejaria de funcionar con uno de los dos y el test que lo dice es este.
    """
    from src.broker import Broker

    for name in ("get_account_state", "is_market_open", "is_tradable",
                 "buy_market", "sell_market", "close_position"):
        assert callable(getattr(SimBroker, name, None)), name
        assert callable(getattr(Broker, name, None)), name
