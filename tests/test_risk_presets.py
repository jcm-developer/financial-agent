"""F6.5, rewritten on 2026-09-25: from the risk profile to the Risk Manager's limits.

What is tested here is not so much that the arithmetic works out as that **the
slider is good for something**. Four invariants, and all four can break without
anything failing visibly:

  * the three levels of the table are the contract, and changing them by accident
    would alter the meaning of every earlier experiment,
  * moving from 1 to 10 has to move each limit in a single direction: a flat
    stretch or a bounce makes the slider look broken,
  * the stop follows the horizon, so changing the plan's length never leaves a
    two-week stop under a six-month thesis,
  * switching advanced mode off has to hand control back to the slider even when
    the columns still hold old numbers.
"""

from __future__ import annotations

import math

import pytest

from src.config import ConfigError, RiskLimits
from src.risk import horizon_sigma
from src.risk_presets import (
    DERIVED_FIELDS,
    MIN_STOP_ATR,
    derive_limits,
    describe,
    is_derived,
    max_open_positions,
    resolve_limits,
    _round,
    stop_sigmas_for,
)

# The table, copied by hand from risk_presets.py and TASKS.md (decision nº 11).
# Its being duplicated is deliberate: if somebody touches the module's anchors,
# this test has to complain, and it would not if it read the numbers from the
# module itself.
TABLA = {
    1:  {"risk_per_trade_pct": 0.5, "max_position_pct": 8.0,
         "min_position_pct": 4.0, "max_total_exposure_pct": 50.0,
         "min_conviction": 75, "min_target_sigma": 0.7,
         "max_open_positions": 12, "min_reward_risk": 1.8},
    5:  {"risk_per_trade_pct": 1.5, "max_position_pct": 20.0,
         "min_position_pct": 10.0, "max_total_exposure_pct": 80.0,
         "min_conviction": 60, "min_target_sigma": 0.8,
         "max_open_positions": 8, "min_reward_risk": 1.44},
    10: {"risk_per_trade_pct": 5.0, "max_position_pct": 40.0,
         "min_position_pct": 20.0, "max_total_exposure_pct": 100.0,
         "min_conviction": 50, "min_target_sigma": 1.0,
         "max_open_positions": 5, "min_reward_risk": 1.29},
}

# The direction each limit must move in as the risk profile goes up.
DIRECCION = {
    "risk_per_trade_pct": +1,
    "max_position_pct": +1,
    "min_position_pct": +1,
    "max_total_exposure_pct": +1,
    "max_daily_loss_pct": +1,
    "min_conviction": -1,
    "min_target_sigma": +1,
    "stop_atr_multiple": +1,
    "min_reward_risk": -1,
}


# -- The table's contract ----------------------------------------------------


@pytest.mark.parametrize("nivel", sorted(TABLA))
def test_the_table_levels_come_out_exact(nivel):
    limites = derive_limits(nivel, 180)

    for campo, esperado in TABLA[nivel].items():
        assert limites[campo] == pytest.approx(esperado), campo


def test_the_number_of_positions_follows_from_the_sizes():
    """What replaced the diversification slider: the whole exposure in smallest
    positions. A cap the sizes can never reach would be a number nothing
    enforces."""
    for nivel in range(1, 11):
        limites = derive_limits(nivel, 180)
        cabe = limites["max_total_exposure_pct"] / limites["min_position_pct"]
        assert limites["max_open_positions"] == math.floor(cabe + 1e-9)


def test_an_aggressive_profile_concentrates():
    """The point of the 2026-09-25 change: more risk means fewer, larger bets,
    not the same number of slots decided by a second slider."""
    assert derive_limits(10, 180)["max_open_positions"] < derive_limits(1, 180)["max_open_positions"]


# -- The stop, in sigmas of the horizon --------------------------------------


@pytest.mark.parametrize("nivel", (1, 5, 10))
@pytest.mark.parametrize("horizonte", (45, 90, 180))
def test_the_stop_sits_at_the_same_sigmas_whatever_the_horizon(nivel, horizonte):
    """The stop is `stop_sigmas` of the horizon: converted back through
    `horizon_sigma`, the multiple has to land on exactly that many sigmas."""
    atr = 1.0
    stop = derive_limits(nivel, horizonte)["stop_atr_multiple"] * atr
    assert stop / horizon_sigma(atr, horizonte) == pytest.approx(
        stop_sigmas_for(nivel), abs=0.005
    )


def test_a_longer_horizon_widens_the_stop():
    """The bug this closes: 2× ATR under a six-month thesis is one bad week."""
    assert derive_limits(5, 180)["stop_atr_multiple"] > derive_limits(5, 45)["stop_atr_multiple"]


def test_half_a_sigma_reproduces_the_stop_chosen_by_hand_at_45_days():
    """Decision nº 9 chose 3× ATR for its 45-day experiment; the calibration
    point of the table (0,5 sigma at level 5) gives 2,8×. The rule reproduces a
    judgement that was made without it."""
    assert derive_limits(5, 45)["stop_atr_multiple"] == pytest.approx(2.83)


def test_a_very_short_horizon_does_not_put_the_stop_inside_one_session():
    assert derive_limits(1, 1)["stop_atr_multiple"] == MIN_STOP_ATR


def test_the_ratio_binds_at_the_same_point_as_the_target_floor():
    """Two rules that contradict each other are worse than one: at the floor
    target, the gross ratio has to clear `min_reward_risk` with room for the
    commission, not fall below it."""
    for nivel in range(1, 11):
        limites = derive_limits(nivel, 180)
        bruto = limites["min_target_sigma"] / stop_sigmas_for(nivel)
        assert limites["min_reward_risk"] < bruto
        assert limites["min_reward_risk"] == pytest.approx(0.9 * bruto, abs=0.01)


# -- Monotonia ---------------------------------------------------------------


@pytest.mark.parametrize("campo", sorted(DIRECCION))
def test_each_limit_always_moves_in_the_same_direction(campo):
    signo = DIRECCION[campo]
    serie = [derive_limits(nivel, 180)[campo] for nivel in range(1, 11)]

    for anterior, siguiente in zip(serie, serie[1:]):
        delta = (siguiente - anterior) * signo
        assert delta > 0, f"{campo}: {serie} tiene un tramo plano o invertido"


# -- Todo nivel produce limites validos --------------------------------------


@pytest.mark.parametrize("riesgo", range(1, 11))
@pytest.mark.parametrize("horizonte", (1, 10, 45, 180, 365))
def test_any_combination_yields_a_valid_risklimits(riesgo, horizonte):
    """`RiskLimits.__post_init__` refuses incoherent combinations.

    If an intermediate cell produced them, the error would fire while moving the
    slider in the interface and there would be no guessing why.
    """
    limites = RiskLimits(**derive_limits(riesgo, horizonte))

    assert limites.risk_per_trade_pct <= limites.max_position_pct
    assert limites.min_position_pct <= limites.max_position_pct


def test_the_derived_fields_are_exactly_those_of_risklimits():
    """A guard against drift: adding a limit to `RiskLimits` and forgetting it
    here would leave a limit the slider does not control."""
    assert set(DERIVED_FIELDS) == set(RiskLimits().__dataclass_fields__)


@pytest.mark.parametrize("nivel", (0, 11, -3, "cinco", None))
def test_a_level_out_of_range_is_refused(nivel):
    with pytest.raises(ConfigError):
        derive_limits(nivel, 180)


@pytest.mark.parametrize("horizonte", (0, -5, "seis meses"))
def test_a_horizon_that_is_not_a_positive_integer_is_refused(horizonte):
    with pytest.raises(ConfigError):
        derive_limits(5, horizonte)


def test_the_rounding_is_not_bankers_rounding():
    """Python's `round()` rounds 12.5 to 12 and 13.5 to 14 depending on parity.

    These numbers are shown on screen and stored in the history: having the
    result depend on parity would be impossible to explain.
    """
    assert _round(12.5, 0) == 13
    assert _round(0.125, 2) == 0.13
    # And the count of positions is a floor, not a rounding: 65 / 7 = 9,29.
    assert max_open_positions(65.0, 7.0) == 9


def test_max_open_positions_survives_a_zero_floor():
    assert max_open_positions(80.0, 0.0) == 1


# -- Modo avanzado -----------------------------------------------------------


def test_without_advanced_mode_the_slider_wins():
    limites = resolve_limits({"risk_profile": 1, "horizon_days": 180,
                              "advanced_overrides": 0})

    assert limites.risk_per_trade_pct == pytest.approx(0.5)
    assert limites.max_open_positions == 12


def test_the_diversification_column_is_no_longer_read():
    """It is still in `agent_settings`, and a row carrying any value must derive
    exactly what a row without it does."""
    base = {"risk_profile": 5, "horizon_days": 180, "advanced_overrides": 0}

    assert resolve_limits({**base, "diversification": 1}) == resolve_limits(base)
    assert resolve_limits({**base, "diversification": 10}) == resolve_limits(base)


def test_a_row_without_horizon_derives_at_the_schema_default():
    assert resolve_limits({"risk_profile": 5, "advanced_overrides": 0}) == RiskLimits(
        **derive_limits(5, 10)
    )


def test_switching_advanced_mode_off_discards_the_old_numbers():
    """The switch is what governs, not the presence of values.

    If the numbers from an earlier session kept winning, switching advanced mode
    off would do nothing visible: the user would conclude the switch is broken
    and, worse, would go on trading with limits they believe they discarded.
    """
    row = {"risk_profile": 1, "horizon_days": 180, "advanced_overrides": 0,
           "risk_per_trade_pct": 99.0, "max_open_positions": 42}

    limites = resolve_limits(row)

    assert limites.risk_per_trade_pct == pytest.approx(0.5)
    assert limites.max_open_positions == 12


def test_advanced_mode_overrides_only_what_is_not_null():
    """NULL sigue significando "derivalo": el modo avanzado es campo a campo."""
    row = {"risk_profile": 5, "horizon_days": 180, "advanced_overrides": 1,
           "risk_per_trade_pct": 2.5, "max_position_pct": None}

    limites = resolve_limits(row)

    assert limites.risk_per_trade_pct == pytest.approx(2.5)
    assert limites.max_position_pct == pytest.approx(20.0)  # el derivado de 5


def test_a_manual_stop_does_not_follow_the_horizon():
    """That is what writing it by hand means: moving the horizon afterwards
    leaves the chosen multiple alone."""
    row = {"risk_profile": 5, "advanced_overrides": 1, "stop_atr_multiple": 3.0}

    assert resolve_limits({**row, "horizon_days": 45}).stop_atr_multiple == 3.0
    assert resolve_limits({**row, "horizon_days": 180}).stop_atr_multiple == 3.0


def test_an_integer_limit_takes_no_decimals():
    row = {"risk_profile": 5, "advanced_overrides": 1, "max_open_positions": 4.5}

    with pytest.raises(ConfigError, match="entero"):
        resolve_limits(row)


def test_is_derived_tells_apart_what_was_touched_by_hand():
    row = {"risk_profile": 5, "advanced_overrides": 1,
           "risk_per_trade_pct": 2.5, "max_position_pct": None}

    assert not is_derived(row, "risk_per_trade_pct")
    assert is_derived(row, "max_position_pct")


def test_is_derived_refuses_a_field_that_is_not_a_limit():
    with pytest.raises(ConfigError):
        is_derived({"advanced_overrides": 0}, "llm_model")


# -- Texto para la interfaz --------------------------------------------------


def test_describe_names_the_effective_values():
    """It is the text of F6.8: moving a slider without seeing the consequence in
    concrete numbers is guesswork. And it is screen text: accents and decimal
    commas, like every figure the frontend prints next to it."""
    text = describe({"risk_profile": 10, "horizon_days": 180,
                     "advanced_overrides": 0})

    assert text == (
        "Riesgo 10/10 a 180 días: hasta 5 posiciones de entre el 20% y el 40%, "
        "exposición máxima del 100%, riesgo por operación del 5%, convicción mínima 50, "
        "stop a 7,94 veces el ATR (0,7 σ), objetivo mínimo de 1 σ, beneficio/riesgo mínimo 1,29 "
        "y sin operar el resto del día si la cartera cae un 10%."
    )


def test_describe_warns_that_the_limits_are_manual():
    text = describe({"risk_profile": 5, "advanced_overrides": 1,
                     "max_open_positions": 2})

    assert text.startswith("Límites fijados a mano.")
    assert "hasta 2 posiciones" in text
