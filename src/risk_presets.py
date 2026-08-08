"""De dos deslizadores a los nueve limites duros del Risk Manager.

El usuario mueve `risk_profile` (1-10) y `diversification` (1-10); de ahi salen
los limites que [risk.py](risk.py) aplica. La traduccion vive aqui, aparte, por
tres razones:

  1. **Es determinista y sin efectos.** Nada de red, nada de base de datos: los
     mismos dos numeros dan siempre los mismos limites. Eso la hace trivial de
     probar y significa que dos experimentos con el mismo deslizador corrieron
     con los mismos limites, sin tener que consultar nada.
  2. **La interfaz necesita las mismas cuentas.** F6.8 muestra en vivo "con
     estos ajustes: max. 8 posiciones, 1,5% de riesgo por operacion". Si la
     interfaz recalculara por su cuenta, acabaria mintiendo el dia que se
     retoque una ancla.
  3. **El modo avanzado se resuelve en un solo sitio.** `resolve_limits` es la
     unica funcion que decide si mandan los deslizadores o los numeros escritos
     a mano, asi que no hay dos codigos con criterios distintos.

Las anclas son las tres filas de la tabla de F6.4/F6.5 (niveles 1, 5 y 10) y
entre ellas se interpola linealmente por tramos. Se interpola en lugar de
guardar diez filas a mano porque asi mover el deslizador un punto siempre
cambia algo: una tabla escrita a ojo tiende a repetir valores y entonces el
deslizador parece roto.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .config import ConfigError, RiskLimits

# Anclas por nivel de riesgo: (nivel 1, nivel 5, nivel 10) y decimales a los que
# se redondea. Decimales = 0 significa que el campo es entero.
#
# Leidas en horizontal se ve la intencion: el perfil conservador arriesga poco
# por operacion, exige mucha conviccion, pone el stop lejos (para no ser barrido
# por ruido) y pide el doble de recompensa que de riesgo. El agresivo hace lo
# contrario en los cinco ejes a la vez.
_BY_RISK: dict[str, tuple[tuple[float, float, float], int]] = {
    "risk_per_trade_pct":     ((0.25,  1.0,   3.0), 2),
    "max_position_pct":       ((5.0,  20.0,  40.0), 2),
    "max_total_exposure_pct": ((30.0, 70.0, 100.0), 2),
    "max_daily_loss_pct":     ((2.0,   5.0,  10.0), 2),
    "min_conviction":         ((85.0, 65.0,  45.0), 0),
    "stop_atr_multiple":      ((3.0,   2.0,   1.2), 2),
    "min_reward_risk":        ((2.5,   1.5,   1.0), 2),
}

# Diversificacion 1 -> 3 posiciones (concentracion permitida); 10 -> 25.
POSITIONS_AT_MIN = 3
POSITIONS_AT_MAX = 25

# Porcentaje del maximo de posiciones que puede caer en un mismo sector.
# Con diversificacion 1 es el 100%: se permite concentrar todo en uno.
SECTOR_SHARE_AT_MIN = 100.0
SECTOR_SHARE_AT_MAX = 25.0

# El minimo por orden no es apetito de riesgo sino friccion de ejecucion: por
# debajo de esto la comision se come el resultado. No depende de los
# deslizadores, y por eso es una constante y no una ancla.
MIN_ORDER_NOTIONAL = 100.0

# Los campos que esta funcion produce. Coincide exactamente con los de
# `RiskLimits` y con las columnas anulables de `agent_settings`: si alguien
# anade un limite en uno de los tres sitios y no en los otros, los tests de
# `test_risk_presets.py` lo cazan.
DERIVED_FIELDS: tuple[str, ...] = (
    *_BY_RISK, "max_open_positions", "min_order_notional",
)

_INTEGER_FIELDS = frozenset({"min_conviction", "max_open_positions"})


# ----------------------------------------------------------------------
# Derivacion
# ----------------------------------------------------------------------

def derive_limits(risk_profile: int, diversification: int) -> dict[str, Any]:
    """Los nueve limites que corresponden a estos dos deslizadores.

    El resultado se puede pasar tal cual a `RiskLimits(**...)`.
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
    """Posiciones maximas en un mismo sector, o None si no hay tope.

    `max_open` es el maximo de posiciones que rige de verdad. Se pasa aparte
    porque en modo avanzado puede no ser el derivado, y un tope por sector mayor
    que el maximo global seria un numero sin sentido en pantalla.

    ⚠️ Informativo: el Risk Manager **todavia no lo aplica** porque no hay dato
    de sector por simbolo en tiempo de ejecucion (`universe/sp500.txt` solo
    lleva el reparto en un comentario). Se calcula aqui para que la interfaz
    pueda ensenarlo y para que el dia que exista el dato solo haya que
    conectarlo, no decidir la formula.
    """
    diversity = _level("diversification", diversification)
    share = SECTOR_SHARE_AT_MIN + (
        (SECTOR_SHARE_AT_MAX - SECTOR_SHARE_AT_MIN) * (diversity - 1) / 9
    )
    maximum = max_open_positions(diversity) if max_open is None else int(max_open)
    cap = max(1, int(_round(maximum * share / 100.0, 0)))
    # Un tope que iguala al maximo global no es un tope: no rechazaria nada.
    return None if cap >= maximum else cap


# ----------------------------------------------------------------------
# Modo avanzado
# ----------------------------------------------------------------------

def resolve_limits(settings: Mapping[str, Any]) -> RiskLimits:
    """Limites efectivos de una fila de `agent_settings`.

    `advanced_overrides` es el interruptor maestro: con el apagado mandan los
    deslizadores **aunque las columnas conserven numeros de una sesion anterior
    de modo avanzado**. Es deliberado. Si los numeros viejos siguieran ganando,
    apagar el modo avanzado no haria nada visible y el usuario concluiria que el
    interruptor esta roto; peor aun, seguiria operando con limites que ya cree
    haber descartado.
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
    """True si `field` sale de los deslizadores y no de un valor escrito a mano.

    La interfaz lo necesita para pintar en gris lo que no se ha tocado.
    """
    if field not in DERIVED_FIELDS:
        raise ConfigError(f"{field!r} no es un limite derivable.")
    return not settings.get("advanced_overrides") or settings.get(field) is None


# ----------------------------------------------------------------------
# Texto para la interfaz y los logs
# ----------------------------------------------------------------------

def describe(settings: Mapping[str, Any]) -> str:
    """Resumen en una linea de lo que implican los ajustes actuales.

    Es el texto de F6.8: mover un deslizador sin ver la consecuencia en numeros
    concretos es adivinar.
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
        f"max. {limits.max_position_pct:g}% por posicion y "
        f"{limits.max_total_exposure_pct:g}% de exposicion, "
        f"conviccion minima {limits.min_conviction}, "
        f"stop a {limits.stop_atr_multiple:g}x ATR con R/R minimo "
        f"{limits.min_reward_risk:g}, "
        f"kill switch a -{limits.max_daily_loss_pct:g}% diario"
    )


# ----------------------------------------------------------------------

def _interpolate(anchors: tuple[float, float, float], level: int) -> float:
    """Interpolacion lineal por tramos entre las anclas 1, 5 y 10."""
    low, mid, high = anchors
    if level <= 5:
        return low + (mid - low) * (level - 1) / 4
    return mid + (high - mid) * (level - 5) / 5


def _round(value: float, decimals: int) -> float | int:
    """Redondeo a la mitad hacia arriba, no el bancario de `round`.

    Importa porque estos numeros se ensenan en pantalla y se guardan en el
    historial: que 12,5 caiga a 12 o a 13 segun la paridad seria imposible de
    explicar.
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
