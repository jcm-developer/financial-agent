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
    """Three prices travel here —entry, current and P&L— and all three said USD.

    Four amounts since F9.9: the cost of closing joined them, and it is money in
    the profile's currency like the rest.
    """
    prompt = _render_exit_prompt(
        position, snapshot, "Tendencia intacta.", 4.2, 5.4,
        INTERVAL_LABELS["1h"], "EUR", 4.11,
    )

    assert "USD" not in prompt
    assert prompt.count("EUR") == 4


# -- Las comisiones estan en el prompt (F9.9) --------------------------------

def test_the_entry_prompt_states_what_operating_costs(snapshot, account):
    """The model proposed targets whose whole gain was the commission.

    Measured before this: CABK.MC at 12,90 with a target at 13,20, which is
    +2,3 % on a 2.038 EUR position where the round trip eats 17 % of the gain. It
    was not a mistake by the model — it was optimising a ratio with a term
    missing.
    """
    prompt = _render_entry_prompt(
        snapshot, account, INTERVAL_LABELS["1h"], "EUR", 4.11
    )

    assert "4.11 EUR por orden" in prompt
    # Both legs, because a position that is opened gets closed.
    assert "8.22 EUR de ida y vuelta" in prompt


def test_the_exit_prompt_says_that_closing_costs_money(snapshot, position):
    """Leaving a flat position is losing the commission and nothing else.

    It is the discretionary exit of F9.11: the stop and the target are watched by
    a rule, but "the thesis has degraded" is a judgement, and it was being made
    without knowing it had a price.
    """
    prompt = _render_exit_prompt(
        position, snapshot, "Tendencia intacta.", 4.2, 5.4,
        INTERVAL_LABELS["1h"], "EUR", 4.11,
    )

    assert "Coste de cerrar: 4.11 EUR" in prompt


def test_the_analyst_never_tells_the_model_that_trading_is_free(snapshot, account):
    """The default tariff is the bank's and not zero (F9.9).

    A zero would be the one lie that matters here: the model would go back to
    proposing symbolic targets, and nothing downstream would flag it.
    """
    from src.analyst import Analyst

    analyst = Analyst(  # type: ignore[arg-type]
        llm=None, price_interval="1h", indicator_interval="1d", currency="EUR",
    )

    assert analyst._commission_for("SAN.MC") == pytest.approx(4.11)
    assert analyst._commission_for("ALV.DE") == pytest.approx(3.00)
    assert analyst._commission_for("AAPL") == 0.0


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


# -- Los dos relojes: precio horario, indicadores diarios (F9.14) ------------

@pytest.fixture
def mixed_snapshot():
    """A snapshot as the cycle builds one now: the price on the hourly clock, the
    indicators on the daily one, so the bundle carries a price of its own."""
    return MarketSnapshot(
        symbol="AIR.PA",
        as_of=datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc),
        price=216.40,
        indicators={"price": 214.25, "rsi_14": 52.1, "atr_14": 4.84,
                    "atr_pct": 2.26},
        recent_bars=[],
        fill_price=216.50,
        mark_price=216.60,
    )


def test_the_prompt_names_the_price_clock_and_the_indicator_clock(
    mixed_snapshot, account
):
    """Two intervals in one prompt, and neither may be misnamed: the reference
    price is an hour of trading, the indicators are daily bars."""
    prompt = _render_entry_prompt(
        mixed_snapshot, account, INTERVAL_LABELS["1d"], "EUR",
        price_labels=INTERVAL_LABELS["1h"],
    )

    assert "ULTIMA HORA DE COTIZACION COMPLETA: 216.40 EUR" in prompt
    assert "calculados sobre barras diarias" in prompt
    assert "ULTIMAS 10 SESIONES" in prompt


def test_the_prompt_precomputes_the_gap_between_the_two_prices(
    mixed_snapshot, account
):
    """The bundle carries its own `price` —the daily close every band in it refers
    to— and the reference price is the current one. Two figures called price and
    no explanation is arithmetic left to the model, which is where it goes wrong
    most often."""
    prompt = _render_entry_prompt(
        mixed_snapshot, account, INTERVAL_LABELS["1d"], "EUR",
        price_labels=INTERVAL_LABELS["1h"],
    )

    assert "ultimo cierre diario completo, 214.25 EUR" in prompt
    # 216,40 / 214,25 - 1 = +1,00 %
    assert "+1.00% respecto de ese cierre" in prompt


def test_there_is_no_gap_note_when_both_clocks_are_the_same(mixed_snapshot, account):
    """With one interval for both there is no gap to explain, and a line saying
    that 0,00 % is zero is noise in a prompt that is already long."""
    prompt = _render_entry_prompt(
        mixed_snapshot, account, INTERVAL_LABELS["1d"], "EUR",
        price_labels=INTERVAL_LABELS["1d"],
    )

    assert "CONTEXTO:" not in prompt


def test_the_gap_note_is_skipped_when_the_bundle_has_no_price(snapshot, account):
    """Writing a gap from a missing figure is worse than writing nothing."""
    prompt = _render_entry_prompt(
        snapshot, account, INTERVAL_LABELS["1d"], "EUR",
        price_labels=INTERVAL_LABELS["1h"],
    )

    assert "CONTEXTO:" not in prompt


def test_the_exit_prompt_carries_the_gap_too(mixed_snapshot, position):
    """The exit review reads the same bundle, so it has the same two prices."""
    prompt = _render_exit_prompt(
        position, mixed_snapshot, "Tendencia intacta.", 4.2, 5.4,
        INTERVAL_LABELS["1d"], "EUR", 3.00,
        price_labels=INTERVAL_LABELS["1h"],
    )

    assert "ultimo cierre diario completo, 214.25 EUR" in prompt


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


# -- El peso lo pide el analista (F9.13) -------------------------------------

def test_the_entry_prompt_asks_for_a_weight_and_gives_it_a_scale(snapshot, account):
    """A weight with no ceiling to compare against is noise, not a decision.

    Told the ceiling the model tends to ask for it, and that anchoring is the
    price paid; the prompt answers it head on and `risk.py` caps regardless. The
    alternative was a number with no units, which looks like a judgement and is a
    guess.
    """
    prompt = _render_entry_prompt(
        snapshot, account, INTERVAL_LABELS["1h"], "EUR", 4.11, 40.0
    )

    assert "40% del capital" in prompt
    assert "TOPE, no un objetivo" in prompt


def test_the_weight_is_read_and_clamped_to_something_a_portfolio_allows():
    """Over 100 there is no leverage, and at or below zero it is a "hold" written
    in the wrong field. Both come back None so the sizing falls back to the
    conviction factor instead of inventing a number."""
    from src.analyst import _coerce_weight

    assert _coerce_weight(12.5) == pytest.approx(12.5)
    assert _coerce_weight("30") == pytest.approx(30.0)
    assert _coerce_weight(None) is None
    assert _coerce_weight(0) is None
    assert _coerce_weight(-5) is None
    assert _coerce_weight(140) is None
    assert _coerce_weight("mucho") is None


def test_the_weight_is_not_clamped_to_the_profile_ceiling_here():
    """That is `risk.py`'s job, and F6.5 is why: the limits live in one place.

    Clamping here would also hide a model that keeps asking for more than it may
    have, which is what the verdict records `suggested_weight_pct` for.
    """
    from src.analyst import _coerce_weight

    assert _coerce_weight(80) == pytest.approx(80.0)
