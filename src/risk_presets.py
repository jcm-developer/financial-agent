"""From the risk profile to the Risk Manager's eleven hard limits.

The user moves `risk_profile` (1-10) and sets the experiment's horizon; the
limits [risk.py](risk.py) applies come from there. The translation lives here,
apart, for three reasons:

  1. **It is deterministic and free of effects.** No network, no database: the
     same risk level and horizon always give the same limits. That makes it
     trivial to test and means two experiments with the same slider ran with the
     same limits, without having to look anything up.
  2. **The interface needs the same arithmetic.** F6.8 shows live "with these
     settings: max. 8 positions, 1.5% risk per trade". If the interface
     recomputed on its own, it would end up lying the day an anchor is tweaked.
  3. **Advanced mode is resolved in one single place.** `resolve_limits` is the
     only function that decides whether the slider or the hand-written numbers
     win, so there are no two code paths with different criteria.

⚠️ **One slider since 2026-09-25, not two.** `diversification` fixed the maximum
number of positions on its own axis, and that was the wrong cut: concentrating is
taking risk, so a "very aggressive" profile that believes in three ideas was still
told how many slots to spread over by a second number. Now the risk level decides
how big a position may be and how much of the book may be invested, and **the
number of positions follows from those two** (`exposure / smallest position`).
The column is still in `agent_settings` —dropping a column under a live database
buys nothing— but nothing reads it any more.

⚠️ **And the stop is measured in sigmas of the horizon, not in ATRs.** The old
table fixed it at 1,2×–3× ATR whatever the plan was, which is right for two weeks
and absurd for six months: at 180 days one sigma is ~24 % of the price, and a
stop at 2× ATR (−4 %) is inside a single bad week. The risk level now says how
many sigmas of room a position gets, and the horizon says how big a sigma is —
the same yardstick `min_target_sigma` already used for the target, so stop and
target finally speak the same unit.

The anchors are the three rows of the table (levels 1, 5 and 10) and between them
it interpolates linearly, piecewise. It interpolates instead of storing ten
hand-written rows so that moving the slider by one point always changes
something: a table written by eye tends to repeat values and then the slider
looks broken.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .config import ConfigError, RiskLimits
from .formatting import compact, percent
from .risk import SESSIONS_PER_CALENDAR_DAY

# Anchors per risk level: (level 1, level 5, level 10) and the decimals to round
# to. Decimals = 0 means the field is an integer.
#
# Read horizontally the intention shows, and it is the one asked for on
# 2026-09-25: **risk is how the agent behaves**. The conservative profile spreads
# small positions, keeps cash back, cuts losers early and settles for moves that
# are merely outside the noise. The aggressive one concentrates, invests the whole
# book, gives each thesis room to breathe and only pays friction for a big move.
_BY_RISK: dict[str, tuple[tuple[float, float, float], int]] = {
    # What a trade may lose down to its stop, in % of equity. It is what makes
    # sizing volatility-aware: at the same budget a stock twice as volatile gets
    # half the position. Anchored so that it binds **around the middle of the
    # size band** at a six-month horizon and the universe's median volatility —
    # risk 5: 1,5 % against a stop at 0,5 sigma (~12 %) gives ~12 %, inside the
    # 10-20 % band. Below that the band would never be reached; far above it the
    # budget would never bind and sizing would stop looking at volatility.
    "risk_per_trade_pct":     ((0.5,   1.5,   5.0), 2),
    "max_position_pct":       ((8.0,  20.0,  40.0), 2),
    # F9.21. Suelo de la banda de tamaño, y por defecto **la mitad del techo** en
    # los tres niveles. Es una elección y se lee así: el analista puede reducir una
    # posición a la mitad cuando la idea le gusta menos, y no puede convertirla en
    # simbólica. Debajo de la mitad el peso deja de ser una gradación y pasa a ser
    # otra forma de decir «hold» sin decirlo, que es justo lo que el prompt le pide
    # que no haga.
    "min_position_pct":       ((4.0,  10.0,  20.0), 2),
    # Up from 30/70/100: a conservative book at 30 % invested spends six months
    # measuring its cash, not its analyst.
    "max_total_exposure_pct": ((50.0, 80.0, 100.0), 2),
    "max_daily_loss_pct":     ((2.0,   5.0,  10.0), 2),
    # Down from 85/65/45. **85 was never reached by any model measured** —four of
    # them, F9.20—, so level 1 was a profile that could not buy, not a cautious
    # one. The floor at 50 keeps "a coin toss" out at every level.
    "min_conviction":         ((75.0, 60.0,  50.0), 0),
    # F9.16. How much of the horizon's own volatility the target has to promise.
    # **Inverted on 2026-09-25**: it used to fall with risk ("the aggressive one
    # settles for a smaller edge"), and an aggressive profile is precisely the one
    # that goes for the big move. Now it rises, and the conservative end is
    # protected by its ratio instead (see `min_reward_risk` below).
    "min_target_sigma":       ((0.7,   0.8,   1.0), 2),
}

#: Room the stop gets, in sigmas of the horizon, per risk level. Not a column:
#: what the Risk Manager applies is `stop_atr_multiple`, derived from this and
#: the horizon in `_stop_atr_multiple`. It **grows with risk**, and that is the
#: other half of the behaviour: an aggressive profile holds a thesis through the
#: noise and takes a smaller position for it (the risk budget above does the
#: shrinking), a conservative one leaves early.
#:
#: 0,5 sigma is the calibration point, and it reproduces a decision taken by hand:
#: the 45-day experiment of decision nº 9 chose 3× ATR, and 0,5 sigma at 45 days
#: is 2,8× ATR.
_STOP_SIGMAS: tuple[float, float, float] = (0.35, 0.5, 0.7)

#: The stop never goes nearer than this many ATRs, whatever the horizon. One ATR
#: is one ordinary session: a stop inside it is taken by the next day's noise, so
#: with a two-day horizon the formula would otherwise produce a stop that means
#: nothing.
MIN_STOP_ATR = 1.0

#: `min_reward_risk` is derived so it binds **at the same point as the target
#: floor** —the rule decision nº 9 wrote by hand: two rules that contradict each
#: other are worse than one—. At the floor, the gross ratio is
#: `min_target_sigma / stop_sigmas`; the net one, after the round-trip commission,
#: is a little lower, so demanding the gross figure would quietly raise the floor.
#: 0,9 leaves that margin.
_RATIO_MARGIN = 0.9

# The per-order minimum is not risk appetite but execution friction: below this
# the commission eats the result. It does not depend on the slider, and that is
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
    *_BY_RISK, "stop_atr_multiple", "min_reward_risk",
    "max_open_positions", "min_order_notional",
)

_INTEGER_FIELDS = frozenset({"min_conviction", "max_open_positions"})

#: What a profile without a horizon is derived at. It matches the schema's
#: default so a settings dict built by hand gets the same limits as a fresh row.
DEFAULT_HORIZON_DAYS = 10


# ----------------------------------------------------------------------
# Derivacion
# ----------------------------------------------------------------------

def derive_limits(risk_profile: int, horizon_days: int = DEFAULT_HORIZON_DAYS) -> dict[str, Any]:
    """The eleven limits this risk level corresponds to at this horizon.

    The horizon only moves the stop (and, through it, nothing else): every other
    limit is a share of the book or a threshold, and those do not change with
    the plan's length.

    The result can be passed straight to `RiskLimits(**...)`.
    """
    risk = _level("risk_profile", risk_profile)
    horizon = _horizon(horizon_days)

    limits: dict[str, Any] = {
        field: _round(_interpolate(anchors, risk), decimals)
        for field, (anchors, decimals) in _BY_RISK.items()
    }
    stop_sigmas = stop_sigmas_for(risk)
    limits["stop_atr_multiple"] = _stop_atr_multiple(stop_sigmas, horizon)
    limits["min_reward_risk"] = _round(
        _RATIO_MARGIN * limits["min_target_sigma"] / stop_sigmas, 2
    )
    limits["max_open_positions"] = max_open_positions(
        limits["max_total_exposure_pct"], limits["min_position_pct"]
    )
    limits["min_order_notional"] = MIN_ORDER_NOTIONAL
    return limits


def stop_sigmas_for(risk_profile: int) -> float:
    """Room the stop gets at this risk level, in sigmas of the horizon."""
    return _round(_interpolate(_STOP_SIGMAS, _level("risk_profile", risk_profile)), 3)


def max_open_positions(exposure_pct: float, min_position_pct: float) -> int:
    """How many positions fit: the whole allowed exposure in smallest positions.

    **It follows from sizing instead of being set** (2026-09-25). A cap on the
    count that the sizes could never reach —13 slots of at least 10 % inside a
    70 % exposure, as the old diversification slider allowed— was a number on
    screen that nothing enforced; one the sizes overflow would leave cash idle.
    """
    if min_position_pct <= 0:
        return 1
    return max(1, math.floor(exposure_pct / min_position_pct + 1e-9))


def _stop_atr_multiple(stop_sigmas: float, horizon_days: int) -> float:
    """`stop_sigmas` of the horizon, expressed in daily ATRs.

    One sigma of the horizon is `ATR × sqrt(sessions)` (`risk.horizon_sigma`), so
    the ATR cancels out and the multiple depends on the horizon alone: that is
    what lets the Risk Manager keep working in ATRs, per symbol, while the profile
    thinks in sigmas.
    """
    sessions = max(1.0, horizon_days * SESSIONS_PER_CALENDAR_DAY)
    return _round(max(MIN_STOP_ATR, stop_sigmas * math.sqrt(sessions)), 2)


# ----------------------------------------------------------------------
# Modo avanzado
# ----------------------------------------------------------------------

def resolve_limits(settings: Mapping[str, Any]) -> RiskLimits:
    """Effective limits of a row of `agent_settings`.

    `advanced_overrides` is the master switch: with it off the slider wins **even
    when the columns still hold numbers from an earlier advanced-mode session**.
    That is deliberate. If the old numbers kept winning, switching advanced mode
    off would do nothing visible and the user would conclude the switch is
    broken; worse still, they would go on trading with limits they believe they
    have already discarded.
    """
    derived = derive_limits(
        settings.get("risk_profile", 5),
        settings.get("horizon_days") or DEFAULT_HORIZON_DAYS,
    )
    if not settings.get("advanced_overrides"):
        return RiskLimits(**derived)

    for field in DERIVED_FIELDS:
        value = settings.get(field)
        if value is not None:
            derived[field] = _cast(field, value)
    return RiskLimits(**derived)


def is_derived(settings: Mapping[str, Any], field: str) -> bool:
    """True if `field` comes from the slider and not from a hand-written value.

    The interface needs it to paint in grey what has not been touched.
    """
    if field not in DERIVED_FIELDS:
        raise ConfigError(f"{field!r} no es un limite derivable.")
    return not settings.get("advanced_overrides") or settings.get(field) is None


# ----------------------------------------------------------------------
# Text for the interface and the logs
# ----------------------------------------------------------------------

def describe(settings: Mapping[str, Any]) -> str:
    """One-line summary of what the current settings imply, as screen text.

    It is the text of F6.8: moving a slider without seeing the consequence in
    concrete numbers is guesswork. It is shown in Ajustes, on the profile card and
    in the cycle's log, so it is written for a reader and not for a developer —
    accents, decimal commas and no jargon. "Kill switch" became what it does: the
    agent stops opening positions for the day.
    """
    limits = resolve_limits(settings)
    risk = settings.get("risk_profile", 5)
    horizon = settings.get("horizon_days") or DEFAULT_HORIZON_DAYS
    sigmas = limits.stop_atr_multiple / math.sqrt(
        max(1.0, int(horizon) * SESSIONS_PER_CALENDAR_DAY)
    )
    manual = "Límites fijados a mano. " if settings.get("advanced_overrides") else ""
    return (
        f"{manual}Riesgo {risk}/10 a {horizon} días: "
        f"hasta {limits.max_open_positions} posiciones "
        # La banda entera y no solo el techo (F9.21): es la mitad que explica cuanto
        # capital se pone a trabajar.
        f"de entre el {percent(limits.min_position_pct)} y el {percent(limits.max_position_pct)}, "
        f"exposición máxima del {percent(limits.max_total_exposure_pct)}, "
        f"riesgo por operación del {percent(limits.risk_per_trade_pct)}, "
        f"convicción mínima {limits.min_conviction}, "
        f"stop a {compact(limits.stop_atr_multiple)} veces el ATR ({compact(sigmas)} σ), "
        f"objetivo mínimo de {compact(limits.min_target_sigma)} σ, "
        f"beneficio/riesgo mínimo {compact(limits.min_reward_risk)} "
        f"y sin operar el resto del día si la cartera cae un "
        f"{percent(limits.max_daily_loss_pct)}."
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


def _horizon(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"horizon_days debe ser un entero, no {value!r}.") from exc
    if days <= 0:
        raise ConfigError(f"horizon_days={days} tiene que ser positivo.")
    return days


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
