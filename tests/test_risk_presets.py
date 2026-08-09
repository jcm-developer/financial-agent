"""F6.5: from the two sliders to the Risk Manager's limits.

What is tested here is not so much that the arithmetic works out as that **the
slider is good for something**. Three invariants, and all three can break without
anything failing visibly:

  * the three levels of the TASKS.md table are the contract, and changing them by
    accident would alter the meaning of every earlier experiment,
  * moving from 1 to 10 has to move each limit in a single direction: a flat
    stretch or a bounce makes the slider look broken,
  * switching advanced mode off has to hand control back to the sliders even when
    the columns still hold old numbers.
"""

from __future__ import annotations

import pytest

from src.config import ConfigError, RiskLimits
from src.risk_presets import (
    DERIVED_FIELDS,
    derive_limits,
    describe,
    is_derived,
    max_open_positions,
    resolve_limits,
    sector_cap,
)

# The F6.5 table, copied by hand from TASKS.md. Its being duplicated is
# deliberate: if somebody touches the module's anchors, this test has to
# complain, and it would not if it read the numbers from the module itself.
TABLA = {
    1:  {"risk_per_trade_pct": 0.25, "max_position_pct": 5.0,
         "max_total_exposure_pct": 30.0, "min_conviction": 85,
         "stop_atr_multiple": 3.0, "min_reward_risk": 2.5},
    5:  {"risk_per_trade_pct": 1.0, "max_position_pct": 20.0,
         "max_total_exposure_pct": 70.0, "min_conviction": 65,
         "stop_atr_multiple": 2.0, "min_reward_risk": 1.5},
    10: {"risk_per_trade_pct": 3.0, "max_position_pct": 40.0,
         "max_total_exposure_pct": 100.0, "min_conviction": 45,
         "stop_atr_multiple": 1.2, "min_reward_risk": 1.0},
}

# The direction each limit must move in as the risk profile goes up.
DIRECCION = {
    "risk_per_trade_pct": +1,
    "max_position_pct": +1,
    "max_total_exposure_pct": +1,
    "max_daily_loss_pct": +1,
    "min_conviction": -1,
    "stop_atr_multiple": -1,
    "min_reward_risk": -1,
}


# -- The table's contract ----------------------------------------------------


@pytest.mark.parametrize("nivel", sorted(TABLA))
def test_the_table_levels_come_out_exact(nivel):
    limites = derive_limits(nivel, 5)

    for campo, esperado in TABLA[nivel].items():
        assert limites[campo] == pytest.approx(esperado), campo


def test_diversification_sets_the_number_of_positions():
    assert max_open_positions(1) == 3
    assert max_open_positions(10) == 25


def test_risk_does_not_touch_the_number_of_positions():
    """The two sliders are independent: if the risk one also moved the number of
    positions, there would be no isolating what caused a result."""
    posiciones = {derive_limits(nivel, 7)["max_open_positions"] for nivel in range(1, 11)}

    assert len(posiciones) == 1


# -- Monotonia ---------------------------------------------------------------


@pytest.mark.parametrize("campo", sorted(DIRECCION))
def test_each_limit_always_moves_in_the_same_direction(campo):
    signo = DIRECCION[campo]
    serie = [derive_limits(nivel, 5)[campo] for nivel in range(1, 11)]

    for anterior, siguiente in zip(serie, serie[1:]):
        delta = (siguiente - anterior) * signo
        assert delta > 0, f"{campo}: {serie} tiene un tramo plano o invertido"


def test_positions_grow_with_diversification():
    serie = [max_open_positions(nivel) for nivel in range(1, 11)]

    assert serie == sorted(serie)
    assert len(set(serie)) == len(serie), f"hay niveles con el mismo tope: {serie}"


# -- Todo nivel produce limites validos --------------------------------------


@pytest.mark.parametrize("riesgo", range(1, 11))
@pytest.mark.parametrize("diversificacion", (1, 5, 10))
def test_any_combination_yields_a_valid_risklimits(riesgo, diversificacion):
    """`RiskLimits.__post_init__` refuses incoherent combinations.

    If an intermediate cell produced them, the error would fire while moving a
    slider in the interface and there would be no guessing why.
    """
    limites = RiskLimits(**derive_limits(riesgo, diversificacion))

    assert limites.risk_per_trade_pct <= limites.max_position_pct


def test_the_derived_fields_are_exactly_those_of_risklimits():
    """A guard against drift: adding a limit to `RiskLimits` and forgetting it
    here would leave a limit the sliders do not control."""
    assert set(DERIVED_FIELDS) == set(RiskLimits().__dataclass_fields__)


@pytest.mark.parametrize("nivel", (0, 11, -3, "cinco", None))
def test_a_level_out_of_range_is_refused(nivel):
    with pytest.raises(ConfigError):
        derive_limits(nivel, 5)


def test_the_rounding_is_not_bankers_rounding():
    """Python's `round()` rounds 12.5 to 12 and 13.5 to 14 depending on parity.

    These numbers are shown on screen and stored in the history: having the
    result depend on parity would be impossible to explain.
    """
    # diversification 5 lands on 12.77 -> 13; banker's rounding over 12.5 would give 12.
    assert max_open_positions(5) == 13


# -- Tope por sector ---------------------------------------------------------


def test_minimum_diversification_allows_concentrating():
    """Nivel 1 es "concentracion permitida": un tope aqui no rechazaria nada."""
    assert sector_cap(1) is None


def test_high_diversification_sets_a_cap():
    cap = sector_cap(10)

    assert cap is not None and cap < max_open_positions(10)


def test_the_sector_cap_respects_the_real_maximum():
    """In advanced mode the maximum number of positions may not be the derived one.

    A per-sector cap larger than the global maximum would be a meaningless number
    on the settings screen.
    """
    cap = sector_cap(5, max_open=4)

    assert cap is not None and cap < 4


# -- Modo avanzado -----------------------------------------------------------


def test_without_advanced_mode_the_sliders_win():
    limites = resolve_limits({"risk_profile": 1, "diversification": 1,
                              "advanced_overrides": 0})

    assert limites.risk_per_trade_pct == pytest.approx(0.25)
    assert limites.max_open_positions == 3


def test_switching_advanced_mode_off_discards_the_old_numbers():
    """The switch is what governs, not the presence of values.

    If the numbers from an earlier session kept winning, switching advanced mode
    off would do nothing visible: the user would conclude the switch is broken
    and, worse, would go on trading with limits they believe they discarded.
    """
    row = {"risk_profile": 1, "diversification": 1, "advanced_overrides": 0,
            "risk_per_trade_pct": 99.0, "max_open_positions": 42}

    limites = resolve_limits(row)

    assert limites.risk_per_trade_pct == pytest.approx(0.25)
    assert limites.max_open_positions == 3


def test_advanced_mode_overrides_only_what_is_not_null():
    """NULL sigue significando "derivalo": el modo avanzado es campo a campo."""
    row = {"risk_profile": 5, "diversification": 5, "advanced_overrides": 1,
            "risk_per_trade_pct": 2.5, "max_position_pct": None}

    limites = resolve_limits(row)

    assert limites.risk_per_trade_pct == pytest.approx(2.5)
    assert limites.max_position_pct == pytest.approx(20.0)  # el derivado de 5


def test_an_integer_limit_takes_no_decimals():
    row = {"risk_profile": 5, "diversification": 5, "advanced_overrides": 1,
            "max_open_positions": 4.5}

    with pytest.raises(ConfigError, match="entero"):
        resolve_limits(row)


def test_is_derived_tells_apart_what_was_touched_by_hand():
    row = {"risk_profile": 5, "diversification": 5, "advanced_overrides": 1,
            "risk_per_trade_pct": 2.5, "max_position_pct": None}

    assert not is_derived(row, "risk_per_trade_pct")
    assert is_derived(row, "max_position_pct")


def test_is_derived_refuses_a_field_that_is_not_a_limit():
    with pytest.raises(ConfigError):
        is_derived({"advanced_overrides": 0}, "llm_model")


# -- Texto para la interfaz --------------------------------------------------


def test_describe_names_the_effective_values():
    """It is the text of F6.8: moving a slider without seeing the consequence in
    concrete numbers is guesswork."""
    text = describe({"risk_profile": 10, "diversification": 10,
                      "advanced_overrides": 0})

    assert "25 posiciones" in text
    assert "3% de riesgo" in text
    assert "deslizadores" in text


def test_describe_warns_that_the_limits_are_manual():
    text = describe({"risk_profile": 5, "diversification": 5,
                      "advanced_overrides": 1, "max_open_positions": 2})

    assert "a mano" in text
    assert "2 posiciones" in text
