"""Tests of the commission tariff.

There are two things worth guarding here, and only one of them is arithmetic.

The first is that **the tariff depends on the exchange and not on the profile**:
a single European portfolio holds Spanish names at 4,11 and the rest at 3,00, so
any code path that resolves one number for the whole portfolio is wrong.

The second is that **an exchange nobody priced must fail loudly**. The default
of a missing tariff would be trading for free, which is the one error that never
shows up as an error: it shows up as a strategy that looks better than it is.
"""

from __future__ import annotations

import pytest

from src import fees
from src.db import Database
from src.market_calendar import MARKETS
from src.sim_broker import SimBroker


@pytest.fixture
def db(tmp_path):
    with Database(path=tmp_path / "fees.db") as database:
        yield database


# -- La tarifa ---------------------------------------------------------------

def test_a_spanish_share_pays_the_madrid_tariff():
    assert fees.standard_commission("SAN.MC") == pytest.approx(4.11)


@pytest.mark.parametrize("symbol", ["AIR.PA", "SAP.DE", "ASML.AS", "ISP.MI",
                                    "ABI.BR", "NOKIA.HE"])
def test_the_rest_of_europe_pays_three_euros(symbol):
    assert fees.standard_commission(symbol) == pytest.approx(3.00)


def test_an_american_share_pays_nothing():
    """No suffix means the United States, and there the broker charges no fee."""
    assert fees.standard_commission("AAPL") == pytest.approx(0.0)


def test_a_hyphenated_american_symbol_is_not_mistaken_for_an_exchange():
    """`BRK-B` carries a hyphen where the index puts a dot, and no suffix."""
    assert fees.standard_commission("BRK-B") == pytest.approx(0.0)


def test_the_symbol_is_read_regardless_of_case_and_padding():
    assert fees.standard_commission("  san.mc  ") == pytest.approx(4.11)


def test_every_exchange_the_project_trades_has_a_decided_tariff():
    """The guarantee that adding an exchange cannot mean trading it for free.

    If a suffix is added to `MARKETS` and not to `COMMISSION_BY_SUFFIX`, this
    fails here instead of in six weeks in a P&L that came out flattering.
    """
    priced = set(fees.COMMISSION_BY_SUFFIX)
    for market in MARKETS.values():
        missing = market.symbol_suffixes - priced
        assert not missing, (
            f"El mercado {market.code} opera {sorted(missing)} y nadie ha decidido "
            f"lo que cuestan."
        )


def test_an_unpriced_exchange_raises_instead_of_trading_for_free():
    with pytest.raises(KeyError, match="No hay tarifa"):
        fees.standard_commission("VOD.L")


# -- Agrupacion para mostrarla ------------------------------------------------

def test_the_european_tariffs_come_grouped_by_amount():
    """Madrid on its own and the other six together: it is how `check` prints it."""
    grouped = fees.tariffs_for_market("eu")

    assert grouped[4.11] == [".MC"]
    assert len(grouped[3.00]) == 6
    assert ".MC" not in grouped[3.00]


def test_the_american_market_has_a_single_tariff_and_no_suffixes():
    assert fees.tariffs_for_market("us") == {0.0: []}


# -- Como lo ve el broker -----------------------------------------------------

def test_the_broker_resolves_the_tariff_per_symbol_not_per_portfolio(db):
    """The same broker charges 4,11 on a Spanish name and 3,00 on a French one."""
    portfolio = db.ensure_portfolio(name="t", mode="paper", initial_budget=10_000.0)
    broker = SimBroker(database=db, portfolio_id=portfolio, initial_cash=10_000.0)

    assert broker.commission_for("SAN.MC") == pytest.approx(4.11)
    assert broker.commission_for("AIR.PA") == pytest.approx(3.00)


def test_the_profiles_surcharge_adds_to_the_tariff_it_does_not_replace_it(db):
    """`sim_commission` is a deviation from the standard, not the standard."""
    portfolio = db.ensure_portfolio(name="t", mode="paper", initial_budget=10_000.0)
    broker = SimBroker(
        database=db, portfolio_id=portfolio, initial_cash=10_000.0,
        extra_commission=0.89,
    )

    assert broker.commission_for("SAN.MC") == pytest.approx(5.00)
