"""Tests of the Risk Manager.

It is the layer that decides how much money is put at risk, so it is the one that
most deserves testing. Every case is deterministic: no network, no LLM, no broker.
"""

from __future__ import annotations

import pytest

from src.config import RiskLimits
from src.models import AccountState, BrokerPosition, Proposal
from src.risk import RiskManager

# With 100k equity, 1% risk and ATR 2.0 x2 = a stop 4.0 away:
#   risk per share = 4.0  ->  1000 / 4 = 250 shares
LIMITS = RiskLimits(
    risk_per_trade_pct=1.0,
    max_position_pct=20.0,
    max_total_exposure_pct=80.0,
    max_open_positions=5,
    max_daily_loss_pct=5.0,
    min_conviction=65,
    stop_atr_multiple=2.0,
    min_reward_risk=1.5,
    min_order_notional=100.0,
)


def account(equity=100_000.0, cash=100_000.0, positions=(), last_equity=None):
    return AccountState(
        equity=equity,
        cash=cash,
        buying_power=cash * 2,
        last_equity=last_equity if last_equity is not None else equity,
        positions=tuple(positions),
    )


def position(symbol="AAPL", qty=10.0, entry=100.0, price=100.0):
    return BrokerPosition(
        symbol=symbol,
        qty=qty,
        avg_entry_price=entry,
        current_price=price,
        market_value=qty * price,
        unrealized_pl=(price - entry) * qty,
        unrealized_pl_pct=(price / entry - 1) * 100 if entry else 0.0,
    )


def proposal(**overrides):
    # Conviction 100 by default since F9.10, and it is a decision and not a round
    # number: conviction now scales the size, so any other value would multiply
    # every expected quantity below by a factor that has nothing to do with the
    # rule each test is about. At 100 the factor is exactly 1 and each cap can be
    # read in isolation. The scaling has its own tests further down.
    defaults = dict(
        symbol="AAPL",
        kind="entry",
        action="buy",
        conviction=100,
        thesis="Tendencia alcista intacta.",
        reference_price=100.0,
    )
    defaults.update(overrides)
    return Proposal(**defaults)


@pytest.fixture
def manager():
    return RiskManager(LIMITS)


# -- Dimensionado ------------------------------------------------------------

def test_position_size_comes_from_risk_budget_not_from_the_model(manager):
    """250 shares = (100k * 1%) / (20 - 16). The model plays no part.

    Price 20 so the max_position_pct cap does not bite and the risk budget's
    effect can be seen in isolation.
    """
    verdict = manager.evaluate_entry(proposal(reference_price=20.0), account(), atr=2.0)

    assert verdict.approved
    assert verdict.qty == 250
    assert verdict.stop_price == pytest.approx(16.0)
    assert verdict.rule == "risk_per_trade"
    assert verdict.details["risk_amount"] == pytest.approx(1000.0)


def test_position_cap_binds_before_the_risk_budget_on_typical_stocks(manager):
    """An important interaction between the defaults: a 2xATR stop is around 4%
    of the price, so risking 1% of equity would imply a 25% position. The 20% cap
    trims it first, and that is what is wanted: diversification wins, not the
    risk budget."""
    verdict = manager.evaluate_entry(proposal(reference_price=100.0), account(), atr=2.0)

    assert verdict.approved
    assert verdict.rule == "max_position_pct"
    assert verdict.qty == 200
    assert verdict.details["pct_of_equity"] == pytest.approx(20.0)
    # Trimming the size also risks less than what was budgeted.
    assert verdict.details["risk_amount"] < verdict.details["risk_budget"]


def test_higher_volatility_yields_a_smaller_position(manager):
    """Same capital and same conviction: the ATR is what changes the size, and
    the amount risked stays constant."""
    calm = manager.evaluate_entry(proposal(reference_price=20.0), account(), atr=1.0)
    volatile = manager.evaluate_entry(proposal(reference_price=20.0), account(), atr=5.0)

    assert calm.qty > volatile.qty
    assert calm.details["risk_amount"] == pytest.approx(1000.0, abs=10)
    assert volatile.details["risk_amount"] == pytest.approx(1000.0, abs=10)


def test_max_position_pct_caps_the_size(manager):
    """With a tiny ATR the risk would allow an enormous position; the 20%-of-
    equity cap trims it to 200 shares."""
    verdict = manager.evaluate_entry(proposal(), account(), atr=0.05)

    assert verdict.approved
    assert verdict.qty == 200
    assert verdict.rule == "max_position_pct"


def test_cash_limits_the_size(manager):
    verdict = manager.evaluate_entry(
        proposal(), account(equity=100_000.0, cash=5_000.0), atr=2.0
    )

    assert verdict.approved
    assert verdict.qty == 50
    assert verdict.rule == "insufficient_cash"


def test_total_exposure_limit_is_enforced(manager):
    """With 78k already invested and an 80% cap, only 2k of room is left."""
    held = position(symbol="MSFT", qty=780.0, entry=100.0, price=100.0)
    verdict = manager.evaluate_entry(
        proposal(), account(equity=100_000.0, cash=50_000.0, positions=[held]), atr=2.0
    )

    assert verdict.approved
    assert verdict.qty == 20
    assert verdict.rule == "max_total_exposure_pct"


# -- Rechazos ----------------------------------------------------------------

def test_low_conviction_is_rejected(manager):
    verdict = manager.evaluate_entry(proposal(conviction=64), account(), atr=2.0)

    assert not verdict.approved
    assert verdict.rule == "min_conviction"
    assert verdict.qty == 0


def test_hold_is_not_an_order(manager):
    verdict = manager.evaluate_entry(proposal(action="hold"), account(), atr=2.0)

    assert not verdict.approved
    assert verdict.rule == "action_not_buy"


def test_missing_atr_is_rejected_not_guessed(manager):
    """With no volatility there is no honest way to size: it is refused."""
    verdict = manager.evaluate_entry(proposal(), account(), atr=None)

    assert not verdict.approved
    assert verdict.rule == "atr_unavailable"


def test_no_averaging_down_on_an_open_position(manager):
    held = position(symbol="AAPL")
    verdict = manager.evaluate_entry(
        proposal(symbol="AAPL"), account(positions=[held]), atr=2.0
    )

    assert not verdict.approved
    assert verdict.rule == "already_open"


def test_max_open_positions_blocks_new_entries(manager):
    held = [position(symbol=s) for s in ("MSFT", "NVDA", "AMZN", "META", "TSLA")]
    verdict = manager.evaluate_entry(proposal(), account(positions=held), atr=2.0)

    assert not verdict.approved
    assert verdict.rule == "max_open_positions"


def test_poor_reward_risk_is_rejected(manager):
    """Stop en 96 (riesgo 4) y objetivo en 102 (beneficio 2) -> R/R 0.5."""
    verdict = manager.evaluate_entry(
        proposal(suggested_target=102.0), account(), atr=2.0
    )

    assert not verdict.approved
    assert verdict.rule == "min_reward_risk"


# -- El peso lo pide el analista (F9.13) -------------------------------------

def test_the_analyst_weight_decides_the_size_below_the_cap(manager):
    """The ceiling was behaving as the default, and that is what this fixes.

    With a 3 % risk and 1,2× ATR stops the risk budget never bound, so every
    approved position landed on `max_position_pct`. A 40 % ceiling means "never
    more than this", and nobody was deciding how much of it each idea deserved.
    """
    verdict = manager.evaluate_entry(
        proposal(reference_price=20.0, suggested_weight_pct=10.0),
        account(),  # 100k of equity
        atr=2.0,
    )

    assert verdict.approved
    # 10 % of 100k at 20 the share -> 500 shares, and the risk budget's 250 is
    # smaller, so the budget still wins. That is the point: it is a cap, not a target.
    assert verdict.qty == 250
    assert verdict.rule == "risk_per_trade"


def test_a_small_weight_shrinks_the_position(manager):
    """The case the report was about: "me gusta, pero no para un 20 %"."""
    # A price of 100 makes `max_position_pct` (20 %) bind at 200 shares, so the
    # weight is the only thing that can go below it.
    full = manager.evaluate_entry(proposal(), account(), atr=2.0)
    half = manager.evaluate_entry(
        proposal(suggested_weight_pct=10.0), account(), atr=2.0
    )

    assert full.qty == 200          # el tope del perfil: 20 % de 100k
    assert half.qty == 100          # lo que pidio el analista: 10 %
    assert half.rule == "suggested_weight"
    assert half.details["weight_pct_applied"] == pytest.approx(10.0)


def test_the_weight_can_only_ask_for_less_never_more(manager):
    """A model asking for 80 % in a profile that allows 20 gets 20.

    This is what keeps the premise intact: the analyst can shrink a position and
    can never enlarge one, exactly like the stop it may only widen.
    """
    verdict = manager.evaluate_entry(
        proposal(suggested_weight_pct=80.0), account(), atr=2.0
    )

    assert verdict.approved
    assert verdict.qty == 200       # el 20 % del perfil, no el 80 % pedido
    assert verdict.rule == "max_position_pct"
    # Lo pedido se registra sin recortar, para que un modelo que insiste se vea.
    assert verdict.details["suggested_weight_pct"] == pytest.approx(80.0)


def test_conviction_scaling_stands_down_when_a_weight_was_asked_for(manager):
    """Otherwise the same opinion would be counted twice.

    A 10 % weight halved again by a conviction of 65 gives a 5 % that nobody
    decided. The weight is the explicit answer to "how much"; the conviction
    factor is what happens when there is none.
    """
    with_weight = manager.evaluate_entry(
        proposal(conviction=LIMITS.min_conviction, suggested_weight_pct=10.0),
        account(),
        atr=2.0,
    )
    without = manager.evaluate_entry(
        proposal(conviction=LIMITS.min_conviction), account(), atr=2.0
    )

    assert with_weight.qty == 100      # el 10 % pedido, intacto
    assert without.qty == 100          # el 20 % del tope, escalado al 0,5 del suelo
    assert with_weight.rule == "suggested_weight"
    assert without.rule == "conviction"


# -- Conviccion y tamano (F9.10) ---------------------------------------------

def test_conviction_scales_the_size_within_the_caps(manager):
    """Same trade, three convictions, three sizes.

    Before F9.10 the three were identical: conviction was a gate and nothing
    else, so a 65 and a 100 got exactly the same money.
    """
    sizes = {}
    for conviction in (65, 80, 100):
        verdict = manager.evaluate_entry(
            proposal(conviction=conviction, reference_price=20.0), account(), atr=2.0
        )
        assert verdict.approved
        sizes[conviction] = verdict.qty

    # 250 shares is what the risk budget allows; conviction takes a fraction.
    assert sizes[100] == 250          # factor 1,0
    assert sizes[80] == 178           # factor 0,5 + 0,5 × (15/35) = 0,714
    assert sizes[65] == 125           # factor 0,5, the floor
    assert sizes[65] < sizes[80] < sizes[100]


def test_the_conviction_floor_is_not_zero(manager):
    """A proposal that just cleared the gate still gets a position.

    At a floor of zero the gate would stop meaning "this is worth trading" and
    start meaning "worth trading, but not really": the size would come out at
    zero shares and the trade would be rejected by the very threshold it passed.
    """
    verdict = manager.evaluate_entry(
        proposal(conviction=LIMITS.min_conviction, reference_price=20.0),
        account(),
        atr=2.0,
    )

    assert verdict.approved
    assert verdict.qty == 125
    assert verdict.details["conviction_factor"] == pytest.approx(0.5)


def test_conviction_can_only_shrink_never_cross_a_limit(manager):
    """It scales what the caps already allowed, so no limit can be raised by it.

    The cap is checked against the *unscaled* size: 20 % of 100k at 100 the share
    is 200 shares, and the highest conviction cannot buy 201.
    """
    verdict = manager.evaluate_entry(proposal(conviction=100), account(), atr=2.0)

    assert verdict.approved
    # max_position_pct 20 % over a price of 100 -> 200 shares, and not one more.
    assert verdict.qty == 200
    assert verdict.rule == "max_position_pct"


def test_the_binding_rule_says_conviction_when_conviction_is_what_cut(manager):
    """Otherwise the Riesgo screen cannot tell a position cut by a limit from one
    the analyst simply did not believe in much."""
    verdict = manager.evaluate_entry(
        proposal(conviction=70, reference_price=20.0), account(), atr=2.0
    )

    assert verdict.approved
    assert verdict.rule == "conviction"
    assert "limita: conviction" in verdict.reason


# -- Comisiones (F9.9) -------------------------------------------------------
#
# The symbols above are American and cost nothing, which is why none of the tests
# so far notice this. These use `.MC` (4,11 EUR a leg) and `.DE` (3,00 EUR), which
# is the tariff the experiment actually runs against.

def test_the_reward_risk_counts_both_legs_commission():
    """A ratio that clears the bar gross and does not clear it net.

    It is the case that was passing trades that were losers by construction: on
    the running experiment a paper 1,02 was a real 0,72.
    """
    # Risk budget 100k × 0,04% = 40 EUR over 4 of risk per share -> 10 shares,
    # 200 EUR of notional, well clear of the 100 minimum so that rule does not
    # bite first.
    small = RiskLimits(**{**LIMITS.__dict__, "risk_per_trade_pct": 0.04})
    manager = RiskManager(small, currency_symbol="€")

    # Gross: gain (26,40 − 20) × 10 = 64, loss 4 × 10 = 40 -> 1,60, over the 1,5
    # minimum. Net: (64 − 8,22) / (40 + 8,22) = 1,16, under it. Same trade, same
    # prices; the only thing that changed is that operating costs money.
    verdict = manager.evaluate_entry(
        proposal(symbol="SAN.MC", reference_price=20.0, suggested_target=26.4),
        account(),
        atr=2.0,
    )

    assert not verdict.approved
    assert verdict.rule == "min_reward_risk"
    assert verdict.details["round_trip_commission"] == pytest.approx(8.22)
    # The gross ratio travels too, so a rejection caused by friction can be told
    # from one caused by the thesis.
    assert verdict.details["reward_risk_gross"] == pytest.approx(1.6)
    assert "8.22" in verdict.reason


def test_the_same_trade_is_approved_once_it_is_big_enough():
    """The ratio depends on the size, and that is the economics, not a quirk.

    A fixed cost is ruinous on two shares and irrelevant on two hundred. It is
    the reason the check had to move after the sizing.
    """
    manager = RiskManager(LIMITS, currency_symbol="€")

    verdict = manager.evaluate_entry(
        proposal(symbol="SAN.MC", reference_price=20.0, suggested_target=30.0),
        account(),
        atr=2.0,
    )

    # 250 shares: gain 2500 − 8,22 over loss 1000 + 8,22 -> 2,47, still over 1,5.
    assert verdict.approved
    assert verdict.qty == 250
    assert verdict.details["reward_risk"] == pytest.approx(2.47, abs=0.01)
    # What the stop would really cost: 250 × 4 plus both commissions.
    assert verdict.details["risk_amount"] == pytest.approx(1008.22)


def test_a_derived_target_clears_the_net_minimum_exactly():
    """Otherwise every proposal without a target would now be rejected.

    The old derivation produced the minimum ratio **before** commissions, so
    under the net check it would land just below it — a change of behaviour
    disguised as a rounding error, and the analyst leaves the target out often.
    """
    manager = RiskManager(LIMITS, currency_symbol="€")

    verdict = manager.evaluate_entry(
        proposal(symbol="SAN.MC", reference_price=20.0), account(), atr=2.0
    )

    assert verdict.approved
    assert verdict.details["target_source"] == "derived"
    assert verdict.details["reward_risk"] == pytest.approx(LIMITS.min_reward_risk)


def test_the_cash_cap_reserves_the_commission():
    """`floor(cash / price)` approved orders the broker then refused.

    `sim_broker.buy_market` charges `qty × precio + comision` and raises if it
    does not fit, so the rejection appeared at execution, after the Risk Manager
    had already said yes.
    """
    manager = RiskManager(LIMITS, currency_symbol="€")

    # 402 EUR of cash at 20 the share: 20 shares would fit on the price alone and
    # cost 400 + 4,11 = 404,11, which does not. With the commission reserved,
    # (402 − 4,11) / 20 -> 19.
    verdict = manager.evaluate_entry(
        proposal(symbol="SAN.MC", reference_price=20.0, suggested_target=30.0),
        account(equity=100_000.0, cash=402.0),
        atr=2.0,
    )

    assert verdict.approved
    assert verdict.qty == 19
    assert verdict.rule == "insufficient_cash"
    assert verdict.qty * 20.0 + 4.11 <= 402.0


def test_an_american_symbol_behaves_exactly_as_before():
    """The tariff is zero there, so none of the above changes anything.

    It is asserted rather than assumed: the whole suite above runs on `AAPL`, and
    if the commission ever leaked in as a non-zero default every one of those
    numbers would shift at once.
    """
    manager = RiskManager(LIMITS, currency_symbol="$")

    verdict = manager.evaluate_entry(
        proposal(reference_price=20.0, suggested_target=30.0), account(), atr=2.0
    )

    assert verdict.approved
    assert verdict.details["round_trip_commission"] == 0.0
    assert verdict.details["reward_risk"] == pytest.approx(2.5)


def test_order_below_min_notional_is_rejected(manager):
    tight = RiskLimits(**{**LIMITS.__dict__, "min_order_notional": 100_000.0})
    verdict = RiskManager(tight).evaluate_entry(proposal(), account(), atr=2.0)

    assert not verdict.approved
    assert verdict.rule == "min_order_notional"


def test_insufficient_cash_for_one_share_is_rejected(manager):
    verdict = manager.evaluate_entry(
        proposal(reference_price=500.0), account(equity=100_000.0, cash=100.0), atr=2.0
    )

    assert not verdict.approved
    assert verdict.qty == 0


# -- El stop del modelo ------------------------------------------------------

def test_model_may_widen_the_stop(manager):
    """A stop wider than the ATR's is accepted: it implies fewer shares."""
    verdict = manager.evaluate_entry(
        proposal(reference_price=20.0, suggested_stop=10.0, suggested_target=40.0),
        account(), atr=2.0,
    )

    assert verdict.approved
    assert verdict.stop_price == pytest.approx(10.0)
    assert verdict.details["stop_source"] == "llm_wider"
    assert verdict.qty == 100  # 1000 / 10, against the 250 of the ATR stop


def test_model_cannot_tighten_the_stop_to_inflate_the_position(manager):
    """This is the attack to avoid: a stop hugging the price would allow buying
    an enormous position. The ATR wins and the size does not move."""
    baseline = manager.evaluate_entry(proposal(reference_price=20.0), account(), atr=2.0)
    attacked = manager.evaluate_entry(
        proposal(reference_price=20.0, suggested_stop=19.99), account(), atr=2.0
    )

    assert attacked.approved
    assert attacked.stop_price == pytest.approx(16.0)
    assert attacked.details["stop_source"] == "atr"
    assert attacked.qty == baseline.qty == 250


def test_absurd_stop_above_price_is_ignored(manager):
    verdict = manager.evaluate_entry(
        proposal(reference_price=20.0, suggested_stop=150.0), account(), atr=2.0
    )

    assert verdict.approved
    assert verdict.stop_price == pytest.approx(16.0)


def test_target_is_derived_when_the_model_omits_it(manager):
    verdict = manager.evaluate_entry(
        proposal(reference_price=20.0, suggested_target=None), account(), atr=2.0
    )

    assert verdict.approved
    assert verdict.details["target_source"] == "derived"
    # 20 + 4 * 1.5
    assert verdict.target_price == pytest.approx(26.0)


# -- Kill switch -------------------------------------------------------------

def test_kill_switch_triggers_on_daily_loss(manager):
    result = manager.check_kill_switch(account(equity=94_000.0, last_equity=100_000.0))

    assert result.triggered
    assert result.day_pnl_pct == pytest.approx(-6.0)


def test_kill_switch_stays_quiet_within_the_limit(manager):
    result = manager.check_kill_switch(account(equity=97_000.0, last_equity=100_000.0))

    assert not result.triggered


def test_kill_switch_ignores_gains(manager):
    result = manager.check_kill_switch(account(equity=110_000.0, last_equity=100_000.0))

    assert not result.triggered


# -- Salidas obligatorias ----------------------------------------------------

def test_stop_hit_forces_an_exit(manager):
    positions = {"AAPL": position(price=95.0)}
    levels = {"AAPL": {"stop_price": 96.0, "target_price": 120.0}}

    signals = manager.mandatory_exits(positions, levels)

    assert len(signals) == 1
    assert signals[0].rule == "stop_loss_hit"
    assert signals[0].forced


def test_target_hit_forces_an_exit(manager):
    positions = {"AAPL": position(price=121.0)}
    levels = {"AAPL": {"stop_price": 96.0, "target_price": 120.0}}

    signals = manager.mandatory_exits(positions, levels)

    assert len(signals) == 1
    assert signals[0].rule == "take_profit_hit"


def test_price_between_levels_produces_no_exit(manager):
    positions = {"AAPL": position(price=105.0)}
    levels = {"AAPL": {"stop_price": 96.0, "target_price": 120.0}}

    assert manager.mandatory_exits(positions, levels) == []


def test_position_without_levels_is_left_alone(manager):
    """An orphan with no stop is not liquidated by surprise; the cycle assigns it
    levels and from then on it is under watch."""
    positions = {"AAPL": position(price=50.0)}

    assert manager.mandatory_exits(positions, {"AAPL": {}}) == []


def test_stop_takes_precedence_over_target(manager):
    """A day with a wide range can touch both levels; with daily data we do not
    know the order, so the worst case is assumed."""
    positions = {"AAPL": position(price=95.0)}
    levels = {"AAPL": {"stop_price": 96.0, "target_price": 94.0}}

    signals = manager.mandatory_exits(positions, levels)

    assert len(signals) == 1
    assert signals[0].rule == "stop_loss_hit"


# -- Divisa en el texto del veredicto ----------------------------------------

def test_approval_text_carries_the_profile_currency():
    """`reason` is screen text, not a log line: it is stored in
    `risk_events.reason` and the Riesgo screen prints it verbatim, so a European
    profile that writes `$` invites comparing its figures with another book's as
    if they were the same unit (FE.8)."""
    verdict = RiskManager(LIMITS, currency_symbol="€").evaluate_entry(
        proposal(), account(), atr=2.0
    )

    assert verdict.approved
    assert "€20,000.00" in verdict.reason
    assert "$" not in verdict.reason


def test_rejection_text_carries_the_profile_currency():
    """The rejections carry figures too, and they are the ones read most: the
    Riesgo screen opens on all the verdicts."""
    tight = RiskLimits(**{**LIMITS.__dict__, "min_order_notional": 100_000.0})

    verdict = RiskManager(tight, currency_symbol="€").evaluate_entry(
        proposal(), account(), atr=2.0
    )

    assert not verdict.approved
    assert verdict.rule == "min_order_notional"
    assert "€100,000.00" in verdict.reason
    assert "$" not in verdict.reason


def test_manager_without_a_currency_writes_a_bare_figure(manager):
    """The default is the empty string and not `$`, which is what it used to be:
    with no market to ask, a bare figure says nothing and `$` says something
    false."""
    verdict = manager.evaluate_entry(proposal(), account(), atr=2.0)

    assert verdict.approved
    assert "por 20,000.00" in verdict.reason
    assert "$" not in verdict.reason


# -- Configuracion -----------------------------------------------------------

def test_risk_per_trade_above_max_position_is_rejected_at_construction():
    from src.config import ConfigError

    with pytest.raises(ConfigError):
        RiskLimits(risk_per_trade_pct=30.0, max_position_pct=20.0)
