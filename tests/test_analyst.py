"""Tests of what the analyst is actually told.

The prompt is the only place where a wrong datum reaches the model without
anything failing: no exception, no test in red, just a worse decision recorded as
if it were a good one. So what is checked here is the **content handed over**, not
the plumbing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.analyst import (
    INTERVAL_LABELS,
    _render_entry_prompt,
    _render_exit_prompt,
)
from src.models import AccountState, BrokerPosition, MarketSnapshot


@pytest.fixture
def snapshot():
    return MarketSnapshot(
        symbol="SAN.MC",
        as_of=datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc),
        price=4.83,
        indicators={"rsi_14": 52.1, "pct_from_52w_high": -3.2, "sma_200": 4.51},
        recent_bars=[],
        fill_price=4.84,
        mark_price=4.85,
    )


@pytest.fixture
def account():
    return AccountState(
        equity=10_000.0, last_equity=10_000.0, cash=10_000.0,
        buying_power=10_000.0, positions=[],
    )


@pytest.fixture
def position():
    return BrokerPosition(
        symbol="SAN.MC", qty=100.0, avg_entry_price=4.50,
        current_price=4.83, market_value=483.0,
        unrealized_pl=33.0, unrealized_pl_pct=7.33,
    )


# -- La divisa se pasa, nunca se asume (FE.8) --------------------------------

def test_the_entry_prompt_carries_the_profiles_currency(snapshot, account):
    """It said "USD" for every price, so a European experiment told the model
    that SAN.MC trades in dollars.

    It is the same invariant the interface obeys, broken in the one place where
    nobody would see it: inside the prompt.
    """
    prompt = _render_entry_prompt(snapshot, account, INTERVAL_LABELS["1h"], "EUR")

    assert "4.83 EUR" in prompt
    assert "USD" not in prompt


def test_the_exit_prompt_carries_it_too(snapshot, position):
    """Three prices travel here —entry, current and P&L— and all three said USD."""
    prompt = _render_exit_prompt(
        position, snapshot, "Tendencia intacta.", 4.2, 5.4,
        INTERVAL_LABELS["1h"], "EUR",
    )

    assert "USD" not in prompt
    assert prompt.count("EUR") == 3


# -- Las ventanas se cuentan en barras, no en dias ---------------------------

def test_the_units_warning_appears_with_hourly_bars(snapshot, account):
    """The indicator names carry the unit and the unit is wrong with 1h.

    `pct_from_52w_high` is the distance to the high of 252 **hours**, about six
    weeks, and not to the 52-week high. Saying "computed on hourly bars" was not
    enough: the key name invites reading it the other way, and the thesis the
    model writes rests on exactly these figures.
    """
    prompt = _render_entry_prompt(snapshot, account, INTERVAL_LABELS["1h"], "EUR")

    assert "ATENCION A LAS UNIDADES" in prompt
    assert "252 barras" in prompt
    assert "NO son dias ni semanas" in prompt


def test_there_is_no_warning_with_daily_bars(snapshot, account):
    """With daily bars the names do not lie, and noise in a prompt costs
    attention on the figures that do matter."""
    prompt = _render_entry_prompt(snapshot, account, INTERVAL_LABELS["1d"], "EUR")

    assert "ATENCION A LAS UNIDADES" not in prompt


def test_the_exit_prompt_warns_as_well(snapshot, position):
    """The exit decision reads the same indicators as the entry one."""
    prompt = _render_exit_prompt(
        position, snapshot, None, None, None, INTERVAL_LABELS["1h"], "EUR",
    )

    assert "ATENCION A LAS UNIDADES" in prompt


# -- Detalles de redaccion que lee el modelo ---------------------------------

@pytest.mark.parametrize(
    ("interval", "expected"),
    [("1d", "ULTIMA SESION COMPLETA"), ("1h", "ULTIMA HORA DE COTIZACION COMPLETA")],
)
def test_the_window_is_named_in_the_singular(snapshot, account, interval, expected):
    """It was `window_label.rstrip("S")`, which gave "SESIONE" and left "HORAS DE
    COTIZACION" in the plural. The model reads that line."""
    prompt = _render_entry_prompt(snapshot, account, INTERVAL_LABELS[interval], "EUR")

    assert expected in prompt


def test_the_interval_is_always_named(snapshot, account):
    """Without it the model cannot tell a 200-session average from a 200-hour one."""
    for interval, (bar_label, _) in INTERVAL_LABELS.items():
        prompt = _render_entry_prompt(snapshot, account, INTERVAL_LABELS[interval], "EUR")
        assert bar_label in prompt
