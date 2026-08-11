"""From two sliders to the Risk Manager's eleven hard limits.

The user moves `risk_profile` (1-10) and `diversification` (1-10); the limits
[risk.py](risk.py) applies come from there. The translation lives here, apart,
for three reasons:

  1. **It is deterministic and free of effects.** No network, no database: the
     same two numbers always give the same limits. That makes it trivial to test
     and means two experiments with the same slider ran with the same limits,
     without having to look anything up.
  2. **The interface needs the same arithmetic.** F6.8 shows live "with these
     settings: max. 8 positions, 1.5% risk per trade". If the interface
     recomputed on its own, it would end up lying the day an anchor is tweaked.
  3. **Advanced mode is resolved in one single place.** `resolve_limits` is the
     only function that decides whether the sliders or the hand-written numbers
     win, so there are no two code paths with different criteria.

The anchors are the three rows of the F6.4/F6.5 table (levels 1, 5 and 10) and
between them it interpolates linearly, piecewise. It interpolates instead of
storing ten hand-written rows so that moving the slider by one point always
changes something: a table written by eye tends to repeat values and then the
slider looks broken.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .config import ConfigError, RiskLimits

# Anchors per risk level: (level 1, level 5, level 10) and the decimals to round
# to. Decimals = 0 means the field is an integer.
#
# Read horizontally the intention shows: the conservative profile risks little
# per trade, demands a lot of conviction, puts the stop far away (so as not to be
# swept by noise) and asks for twice as much reward as risk. The aggressive one
# does the opposite on all five axes at once.
_BY_RISK: dict[str, tuple[tuple[float, float, float], int]] = {
    "risk_per_trade_pct":     ((0.25,  1.0,   3.0), 2),
    "max_position_pct":       ((5.0,  20.0,  40.0), 2),
    # F9.21. Suelo de la banda de tamaño, y por defecto **la mitad del techo** en
    # los tres niveles. Es una elección y se lee así: el analista puede reducir una
    # posición a la mitad cuando la idea le gusta menos, y no puede convertirla en
    # simbólica. Debajo de la mitad el peso deja de ser una gradación y pasa a ser
    # otra forma de decir «hold» sin decirlo, que es justo lo que el prompt le pide
    # que no haga.
    #
    # ⚠️ **En el nivel 10 el suelo derivado (20 %) no cabe siete veces**, así que
    # con los deslizadores a tope la exposición máxima ata antes y salen cinco
    # posiciones en vez de siete. No es un error: es lo que «muy agresivo» significa
    # en esta tabla —concentración—, y un perfil que quiera siete plazas llenas
    # escribe la banda a mano, que es para lo que existe el modo avanzado.
    "min_position_pct":       ((2.5,  10.0,  20.0), 2),
    "max_total_exposure_pct": ((30.0, 70.0, 100.0), 2),
    "max_daily_loss_pct":     ((2.0,   5.0,  10.0), 2),
    "min_conviction":         ((85.0, 65.0,  45.0), 0),
    "stop_atr_multiple":      ((3.0,   2.0,   1.2), 2),
    "min_reward_risk":        ((2.5,   1.5,   1.0), 2),
    # F9.16. How much of the horizon's own volatility the target has to promise,
    # and it reads the same way as the row above it: the conservative profile only
    # pays friction for a move that is clearly outside the noise, the aggressive
    # one settles for a smaller edge.
    #
    # ⚠️ **It never binds at the conservative end, and that is fine.** With a stop
    # at 3x ATR and a demanded ratio of 2,5, level 1 is already asking for 7,5 ATR
    # of travel — about 2,8 sigma over a ten-day horizon— so the floor is a
    # backstop there and the real filter at level 10, where the ratio is 1,00 and
    # nothing else looks at the size of the move.
    "min_target_sigma":       ((1.0,   0.8,   0.6), 2),
}

# Diversificacion 1 -> 3 posiciones (concentracion permitida); 10 -> 25.
POSITIONS_AT_MIN = 3
POSITIONS_AT_MAX = 25

# Percentage of the maximum positions that may fall in the same sector.
# At diversification 1 it is 100%: everything may be concentrated in one.
SECTOR_SHARE_AT_MIN = 100.0
SECTOR_SHARE_AT_MAX = 25.0

# The per-order minimum is not risk appetite but execution friction: below this
# the commission eats the result. It does not depend on the sliders, and that is
# why it is a constant and not an anchor.
#
# **It was 100 EUR until F9.16, and that was too low to mean anything.** The
# bank charges 4,11 EUR per leg on a Spanish stock, so a 100 EUR order pays 8,2 %
# of round trip: no target reachable in any horizon covers that. Measured on the
# first cycle of the new experiment, five proposals landed as ~105 EUR orders
# because the cash was already spent, and all five were rejected for
# `min_reward_risk` — the right answer reached by the wrong route, since the order
# should never have been considered. At 500 EUR the round trip is 1,6 %, which a
# 12 % target does cover.
MIN_ORDER_NOTIONAL = 500.0

# The fields this function produces. It matches exactly those of `RiskLimits` and
# the nullable columns of `agent_settings`: if someone adds a limit in one of the
# three places and not in the others, the tests in `test_risk_presets.py` catch
# it.
DERIVED_FIELDS: tuple[str, ...] = (
    *_BY_RISK, "max_open_positions", "min_order_notional",
)

_INTEGER_FIELDS = frozenset({"min_conviction", "max_open_positions"})


# ----------------------------------------------------------------------
# Derivacion
# ----------------------------------------------------------------------

def derive_limits(risk_profile: int, diversification: int) -> dict[str, Any]:
    """The eleven limits these two sliders correspond to.

    The result can be passed straight to `RiskLimits(**...)`.
    """
    risk = _level("risk_profile", risk_profile)
    diversity = _level("diversification", diversification)

    limits: dict[str, Any] = {
        field: _round(_interpolate(anchors, risk), decimals)
        for field, (anchors, decimals) in _BY_RISK.items()
    }
    limits["max_open_positions"] = max_open_positions(diversity)
    limits["min_order_notional"] = MIN_ORDER_NOTIONAL
    return limits


def max_open_positions(diversification: int) -> int:
    diversity = _level("diversification", diversification)
    span = (POSITIONS_AT_MAX - POSITIONS_AT_MIN) * (diversity - 1) / 9
    return int(_round(POSITIONS_AT_MIN + span, 0))


def sector_cap(diversification: int, max_open: int | None = None) -> int | None:
    """Maximum positions in a single sector, or None when there is no cap.

    `max_open` is the maximum number of positions that really governs. It is
    passed separately because in advanced mode it may not be the derived one, and
    a per-sector cap larger than the global maximum would be a meaningless number
    on screen.

    ⚠️ Informative: the Risk Manager **does not apply it yet** because there is no
    per-symbol sector datum at runtime (`universe/sp500.txt` only carries the
    breakdown in a comment). It is computed here so the interface can show it and
    so that the day the datum exists only the wiring is left, not the decision
    about the formula.
    """
    diversity = _level("diversification", diversification)
    share = SECTOR_SHARE_AT_MIN + (
        (SECTOR_SHARE_AT_MAX - SECTOR_SHARE_AT_MIN) * (diversity - 1) / 9
    )
    maximum = max_open_positions(diversity) if max_open is None else int(max_open)
    cap = max(1, int(_round(maximum * share / 100.0, 0)))
    # A cap equal to the global maximum is not a cap: it would reject nothing.
    return None if cap >= maximum else cap


# ----------------------------------------------------------------------
# Modo avanzado
# ----------------------------------------------------------------------

def resolve_limits(settings: Mapping[str, Any]) -> RiskLimits:
    """Effective limits of a row of `agent_settings`.

    `advanced_overrides` is the master switch: with it off the sliders win **even
    when the columns still hold numbers from an earlier advanced-mode session**.
    That is deliberate. If the old numbers kept winning, switching advanced mode
    off would do nothing visible and the user would conclude the switch is
    broken; worse still, they would go on trading with limits they believe they
    have already discarded.
    """
    derived = derive_limits(
        settings.get("risk_profile", 5), settings.get("diversification", 5)
    )
    if not settings.get("advanced_overrides"):
        return RiskLimits(**derived)

    for field in DERIVED_FIELDS:
        value = settings.get(field)
        if value is not None:
            derived[field] = _cast(field, value)
    return RiskLimits(**derived)


def is_derived(settings: Mapping[str, Any], field: str) -> bool:
    """True if `field` comes from the sliders and not from a hand-written value.

    The interface needs it to paint in grey what has not been touched.
    """
    if field not in DERIVED_FIELDS:
        raise ConfigError(f"{field!r} no es un limite derivable.")
    return not settings.get("advanced_overrides") or settings.get(field) is None


# ----------------------------------------------------------------------
# Text for the interface and the logs
# ----------------------------------------------------------------------

def describe(settings: Mapping[str, Any]) -> str:
    """One-line summary of what the current settings imply.

    It is the text of F6.8: moving a slider without seeing the consequence in
    concrete numbers is guesswork.
    """
    limits = resolve_limits(settings)
    diversity = _level("diversification", settings.get("diversification", 5))
    origin = "a mano" if settings.get("advanced_overrides") else "deslizadores"
    cap = sector_cap(diversity, limits.max_open_positions)
    sector = f"max. {cap} por sector" if cap else "sin tope por sector"
    return (
        f"riesgo {settings.get('risk_profile', 5)}/10, "
        f"diversificacion {diversity}/10 ({origin}): "
        f"max. {limits.max_open_positions} posiciones ({sector}), "
        f"{limits.risk_per_trade_pct:g}% de riesgo por operacion, "
        # La banda entera y no solo el techo (F9.21): con el suelo fuera, esta linea
        # decia «max. 14% por posicion» sobre un perfil cuyas posiciones no bajan
        # del 12%, que es la mitad de la informacion y justamente la que explica
        # cuanto capital se pone a trabajar.
        f"posiciones del {limits.min_position_pct:g}% al {limits.max_position_pct:g}% y "
        f"{limits.max_total_exposure_pct:g}% de exposicion, "
        f"conviccion minima {limits.min_conviction}, "
        f"stop a {limits.stop_atr_multiple:g}x ATR con R/R minimo "
        f"{limits.min_reward_risk:g}, "
        f"objetivo minimo {limits.min_target_sigma:g} sigma del horizonte, "
        f"kill switch a -{limits.max_daily_loss_pct:g}% diario"
    )


# ----------------------------------------------------------------------

def _interpolate(anchors: tuple[float, float, float], level: int) -> float:
    """Piecewise linear interpolation between the anchors 1, 5 and 10."""
    low, mid, high = anchors
    if level <= 5:
        return low + (mid - low) * (level - 1) / 4
    return mid + (high - mid) * (level - 5) / 5


def _round(value: float, decimals: int) -> float | int:
    """Half-up rounding, not `round`'s banker's rounding.

    It matters because these numbers are shown on screen and stored in the
    history: 12.5 falling to 12 or to 13 depending on parity would be impossible
    to explain.
    """
    factor = 10 ** decimals
    magnitude = math.floor(abs(value) * factor + 0.5) / factor
    result = magnitude if value >= 0 else -magnitude
    return int(result) if decimals == 0 else result


def _level(name: str, value: Any) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} debe ser un entero de 1 a 10, no {value!r}.") from exc
    if not 1 <= level <= 10:
        raise ConfigError(f"{name}={level} esta fuera del rango 1-10.")
    return level


def _cast(field: str, value: Any) -> float | int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"El limite {field} vale {value!r}, que no es un numero."
        ) from exc
    if field in _INTEGER_FIELDS:
        if number != int(number):
            raise ConfigError(f"El limite {field} debe ser entero, no {value!r}.")
        return int(number)
    return number
