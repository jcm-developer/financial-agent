"""Tests of the simulated broker.

The simulator is what replaces the broker account, so if it is optimistic the
whole experiment is invalidated. What gets tested most here is exactly that: that
money that is not there cannot be spent, that slippage always works against you,
and that execution happens at the following open and not at the close the
decision was made on.
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
    """No slippage and no commission: the tests that measure them switch them on."""
    return SimBroker(
        database=db, portfolio_id=portfolio, initial_cash=10_000.0,
        slippage_bps=0.0, extra_commission=0.0,
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

    # A later cycle builds the broker again: the state lives in SQLite.
    second = SimBroker(database=db, portfolio_id=portfolio, initial_cash=10_000.0,
                       slippage_bps=0.0)
    second.set_quotes({"AAPL": quote(100.0)})
    account = second.get_account_state()

    assert account.cash == pytest.approx(9_000.0)
    assert account.positions[0].symbol == "AAPL"
    assert account.positions[0].qty == pytest.approx(10.0)


# -- Compras -----------------------------------------------------------------

def test_buy_fills_at_the_next_open_not_at_the_decision_close(broker):
    """The core point of the simulation: it decides on the close (105) and
    executes at the following open (100). Executing at 105 would hand over the gap."""
    broker.set_quotes({"AAPL": Quote(fill_price=100.0, mark_price=105.0)})

    order = broker.buy_market("AAPL", 10)

    assert order.filled_avg_price == pytest.approx(100.0)
    assert broker.get_account_state().cash == pytest.approx(9_000.0)


def test_buy_slippage_works_against_the_buyer(db, portfolio):
    """50 bp over 100 is 100.50: buying pays more, never less."""
    broker = SimBroker(database=db, portfolio_id=portfolio, initial_cash=10_000.0,
                       slippage_bps=50.0)
    broker.set_quotes({"AAPL": quote(100.0)})

    order = broker.buy_market("AAPL", 10)

    assert order.filled_avg_price == pytest.approx(100.50)
    assert broker.get_account_state().cash == pytest.approx(10_000.0 - 1005.0)


def test_the_profiles_surcharge_is_charged_on_top_of_the_tariff(db, portfolio):
    """`sim_commission` no longer *is* the commission: it is added to the bank's.

    AAPL carries no suffix, so its tariff is zero and what is left in the cash is
    the surcharge alone. That the two add up is checked over a European symbol in
    `test_fees.py`.
    """
    broker = SimBroker(database=db, portfolio_id=portfolio, initial_cash=10_000.0,
                       slippage_bps=0.0, extra_commission=1.5)
    broker.set_quotes({"AAPL": quote(100.0)})

    broker.buy_market("AAPL", 10)

    assert broker.get_account_state().cash == pytest.approx(10_000.0 - 1000.0 - 1.5)


def test_cannot_spend_more_cash_than_available(broker):
    broker.set_quotes({"AAPL": quote(100.0)})

    with pytest.raises(BrokerError, match="Efectivo insuficiente"):
        broker.buy_market("AAPL", 200)

    assert broker.get_account_state().cash == pytest.approx(10_000.0)


def test_buying_without_a_price_is_refused(broker):
    """With no execution price, none is invented."""
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


# -- Comisiones y P&L realizado ----------------------------------------------

def test_a_round_trip_pays_the_tariff_on_both_legs(broker):
    """4,11 on the way in and 4,11 on the way out, on a Madrid name."""
    broker.set_quotes({"SAN.MC": quote(100.0)})
    broker.buy_market("SAN.MC", 10)
    assert broker.get_account_state().cash == pytest.approx(10_000.0 - 1000.0 - 4.11)

    broker.set_quotes({"SAN.MC": quote(120.0)})
    broker.close_position("SAN.MC")

    assert broker.get_account_state().cash == pytest.approx(10_000.0 + 200.0 - 8.22)


def test_the_realized_pnl_nets_the_opening_commission_too(db, portfolio, broker):
    """The regression this whole thing exists for.

    The sale used to subtract only its own commission: the opening one had left
    the cash but never reached `realized_pnl`, so every closed trade reported
    exactly one commission more than it made. With a commission of zero --the old
    default, and an American broker-- it was invisible; at 4,11 it is the
    difference between a strategy that pays for itself and one that does not.
    """
    broker.set_quotes({"SAN.MC": quote(100.0)})
    broker.buy_market("SAN.MC", 10)
    broker.set_quotes({"SAN.MC": quote(120.0)})
    broker.close_position("SAN.MC")

    realized = db.query(
        "select realized_pnl from sim_fills where account_id = ? and side = 'sell'",
        (portfolio,),
    )[0]["realized_pnl"]

    assert realized == pytest.approx(200.0 - 4.11 - 4.11)


def test_the_realized_pnl_agrees_with_what_the_cash_did(broker, db, portfolio):
    """The invariant, and the reason the previous test cannot be fudged.

    Over a complete round trip there is nothing left open, so the change in cash
    **is** the result. Any commission counted once, twice or not at all breaks
    the equality.
    """
    broker.set_quotes({"SAN.MC": quote(100.0)})
    broker.buy_market("SAN.MC", 7)
    broker.set_quotes({"SAN.MC": quote(93.0)})
    broker.close_position("SAN.MC")

    realized = db.query(
        "select realized_pnl from sim_fills where account_id = ? and side = 'sell'",
        (portfolio,),
    )[0]["realized_pnl"]

    assert realized == pytest.approx(broker.get_account_state().cash - 10_000.0)


def test_a_partial_sale_carries_only_its_share_of_the_opening_commission(
    broker, db, portfolio
):
    """Prorated by quantity, so the last sale does not come out free.

    Charging the opening commission whole on the first partial sale would make
    that trade look worse and every later one look better, and the sum would
    still come out right --which is what makes it hard to notice.
    """
    broker.set_quotes({"SAN.MC": quote(100.0)})
    broker.buy_market("SAN.MC", 10)

    broker.set_quotes({"SAN.MC": quote(120.0)})
    broker.sell_market("SAN.MC", 4)
    broker.sell_market("SAN.MC", 6)

    realized = [
        row["realized_pnl"] for row in db.query(
            "select realized_pnl from sim_fills where account_id = ? and side = 'sell' "
            "order by id", (portfolio,)
        )
    ]

    # 40 % of the 4,11 paid on entry goes with the first sale, 60 % with the second.
    assert realized[0] == pytest.approx(80.0 - 4.11 - 4.11 * 0.4)
    assert realized[1] == pytest.approx(120.0 - 4.11 - 4.11 * 0.6)
    # Y el total sigue siendo el bruto menos las tres comisiones que se pagaron.
    assert sum(realized) == pytest.approx(200.0 - 4.11 * 3)


def test_the_tariff_is_charged_by_exchange_within_the_same_portfolio(broker):
    """A Spanish name and a French one in the same book pay different amounts."""
    broker.set_quotes({"SAN.MC": quote(100.0), "AIR.PA": quote(100.0)})

    broker.buy_market("SAN.MC", 1)
    broker.buy_market("AIR.PA", 1)

    assert broker.get_account_state().cash == pytest.approx(
        10_000.0 - 100.0 - 4.11 - 100.0 - 3.00
    )


def test_no_short_selling(broker):
    """Selling what you do not hold would open a short, which the simulator does not model."""
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
    """With no quote it is valued at the entry price: no valuation is invented
    and it is not counted as zero, which would be worse."""
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

    # A new session: the reference is pinned to the previous closing equity.
    broker.set_quotes({"AAPL": Quote(fill_price=110.0, mark_price=110.0)})
    broker.roll_session("2026-08-07")
    account = broker.get_account_state()

    assert account.last_equity == pytest.approx(10_100.0)
    assert account.day_pnl == pytest.approx(0.0)


def test_rolling_the_same_session_twice_does_not_move_the_reference(broker):
    """If the reference reset on every cycle of the same day, the daily-loss kill
    switch would never get to fire."""
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


def test_sim_broker_satisfies_the_broker_protocol():
    """`cycle.py` only knows the protocol in `broker.py`.

    If the simulator drifts away from it, the day a real broker is added the cycle
    would stop working with one of the two, and this is the test that says so.
    """
    from src.broker import Broker

    for name in ("get_account_state", "is_market_open", "is_tradable",
                 "buy_market", "sell_market", "close_position"):
        assert callable(getattr(SimBroker, name, None)), name
        assert callable(getattr(Broker, name, None)), name
