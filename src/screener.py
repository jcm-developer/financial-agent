"""First stage of the funnel: 500 assets -> 20 candidates, with no AI.

Why it exists: at 45 seconds per model call, analysing 500 assets would cost six
hours per cycle. The filter shrinks the universe with arithmetic —seconds, zero
quota— and the LLM only sees what already looks interesting.

**An honest warning about the scoring.** The weights below are a reasonable
heuristic, not a demonstrated edge. They are validated against nothing: they are
a way of ordering the universe better than at random, and of getting the model
candidates with something to look at instead of ten megacaps picked by hand. If
the experiment ends up showing something, it will be impossible to know how much
comes from the filter and how much from the model. Separating the two would mean
comparing against a random filter, and `SCREENER_MODE=random` exists for exactly
that.

The hard discards (liquidity, minimum price, insufficient data) are defensible:
in an illiquid name the simulator would lie, because it assumes you can buy at
the opening price without moving the market.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .indicators import Bar, compute_snapshot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScreenerLimits:
    """Filtros duros y tamano de la salida."""

    top_n: int = 20
    # Average dollar volume over the last 20 sessions. Below this the execution
    # simulation is not credible.
    min_dollar_volume: float = 20_000_000.0
    min_price: float = 5.0
    # Maximum annualised volatility in %. Above it, the ATR stop would be so wide
    # that the resulting position would be irrelevant.
    max_volatility_pct: float = 120.0
    min_bars: int = 60


@dataclass
class Candidate:
    symbol: str
    score: float
    price: float
    indicators: dict[str, Any]
    reasons: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)


@dataclass
class ScreenerReport:
    """The full result, so it can be audited why each candidate got in."""

    candidates: list[Candidate]
    evaluated: int = 0
    rejected: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        motivos = ", ".join(f"{k}={v}" for k, v in sorted(self.rejected.items()))
        return (
            f"{self.evaluated} evaluados, {len(self.candidates)} seleccionados"
            + (f". Descartes: {motivos}" if motivos else "")
        )


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _setup_factor(rsi: float | None) -> tuple[float, str]:
    """Multiplier based on where the RSI sits. It is what stops price chasing.

    It is applied as a factor and not as an addend for a concrete reason: as an
    addend, an asset with perfect trend and maximum momentum more than made up
    for the overbought penalty and ended up winning the ranking, which is exactly
    the opposite of the intention. As a factor, an extreme RSI sinks the score
    however good the other components are.
    """
    if rsi is None:
        return 0.70, "RSI no disponible"
    if 35 <= rsi <= 55:
        return 1.00, f"RSI {rsi:.0f} en zona de retroceso"
    if 30 <= rsi < 35 or 55 < rsi <= 62:
        return 0.85, f"RSI {rsi:.0f} aceptable"
    if 62 < rsi <= 70:
        return 0.60, f"RSI {rsi:.0f} algo alto"
    if rsi > 70:
        return 0.35, f"RSI {rsi:.0f} sobrecomprado: perseguir el precio"
    return 0.50, f"RSI {rsi:.0f} sobrevendido: puede ser caida estructural"


def score_symbol(indicators: dict[str, Any]) -> tuple[float, dict[str, float], list[str]]:
    """Scores an asset between 0 and 1.

    Four components adding to at most 1.0, multiplied by a situation factor
    derived from the RSI. The intention is to reward an established trend with a
    recent pullback —where an analyst has something to say— and to penalise the
    asset that has already shot up.

    `components` comes out already multiplied by the factor, so its values add up
    to exactly the score and the screener's report does not lie about why a
    candidate got in.
    """
    components: dict[str, float] = {}
    reasons: list[str] = []

    price = indicators.get("price") or 0.0
    sma50 = indicators.get("sma_50")
    sma200 = indicators.get("sma_200")
    rsi = indicators.get("rsi_14")
    return_20 = indicators.get("return_20d_pct")
    return_60 = indicators.get("return_60d_pct")
    volume_ratio = indicators.get("volume_ratio")
    atr_pct = indicators.get("atr_pct")
    from_high = indicators.get("pct_from_52w_high")

    # --- Tendencia (0.40) -------------------------------------------------
    trend = 0.0
    if sma50 and price > sma50:
        trend += 0.5
    if sma200 and price > sma200:
        trend += 0.3
    if sma50 and sma200 and sma50 > sma200:
        trend += 0.2
    components["tendencia"] = trend * 0.40
    if trend >= 0.8:
        reasons.append("tendencia alcista establecida")

    # --- Medium-term momentum (0.25) -------------------------------------
    # Normalised to a 20% rise over 60 sessions; above that it scores no more, so
    # an asset that has shot up does not accumulate an unbounded advantage.
    momentum = _clamp((return_60 or 0.0) / 20.0) if return_60 is not None else 0.0
    components["momento"] = momentum * 0.25
    if momentum > 0.5 and return_60 is not None:
        reasons.append(f"momento 60 barras {return_60:+.1f}%")

    # --- Interes por volumen (0.20) --------------------------------------
    interest = _clamp(((volume_ratio or 1.0) - 0.8) / 1.2)
    components["volumen"] = interest * 0.20
    if (volume_ratio or 0) > 1.5:
        reasons.append(f"volumen {volume_ratio:.1f}x la media")

    # --- Usable volatility (0.15) ----------------------------------------
    # The middle range is rewarded: with no movement there is no trade to make,
    # and with too much the ATR stop forces a tiny position.
    usable = 0.0
    if atr_pct is not None:
        if 1.0 <= atr_pct <= 4.0:
            usable = 1.0
        elif 0.5 <= atr_pct < 1.0 or 4.0 < atr_pct <= 6.0:
            usable = 0.5
    components["volatilidad"] = usable * 0.15

    # --- Factor de situacion, por RSI ------------------------------------
    factor, factor_reason = _setup_factor(rsi)
    reasons.append(factor_reason)
    for key in components:
        components[key] = round(components[key] * factor, 6)

    if from_high is not None and from_high > -3:
        reasons.append("cerca del maximo de 52 semanas")

    return round(sum(components.values()), 4), components, reasons


def screen(
    bars_by_symbol: dict[str, list[Bar]],
    limits: ScreenerLimits,
    *,
    mode: str = "score",
) -> ScreenerReport:
    """Applies the hard filters and sorts by score.

    `mode="random"` replaces the score with a stable but arbitrary order (the
    symbol's hash). It serves as a control group: if the agent performs the same
    with arbitrary candidates, the filter adds nothing.
    """
    report = ScreenerReport(candidates=[])
    accepted: list[Candidate] = []

    for symbol, bars in bars_by_symbol.items():
        report.evaluated += 1

        if len(bars) < limits.min_bars + 1:
            report.rejected["datos_insuficientes"] = report.rejected.get("datos_insuficientes", 0) + 1
            continue

        # The last bar is reserved for execution, same as in `market_data`.
        indicators = compute_snapshot(bars[:-1])
        price = indicators.get("price") or 0.0

        if price < limits.min_price:
            report.rejected["precio_bajo"] = report.rejected.get("precio_bajo", 0) + 1
            continue

        avg_volume = indicators.get("avg_volume_20")
        dollar_volume = (avg_volume or 0.0) * price
        if dollar_volume < limits.min_dollar_volume:
            report.rejected["iliquido"] = report.rejected.get("iliquido", 0) + 1
            continue

        volatility = indicators.get("volatility_20d_pct")
        if volatility is not None and volatility > limits.max_volatility_pct:
            report.rejected["demasiado_volatil"] = report.rejected.get("demasiado_volatil", 0) + 1
            continue

        if indicators.get("atr_14") is None:
            # Without an ATR the Risk Manager would reject the entry anyway.
            report.rejected["sin_atr"] = report.rejected.get("sin_atr", 0) + 1
            continue

        if mode == "random":
            score = (hash(symbol) & 0xFFFF) / 0xFFFF
            components, reasons = {}, ["seleccion arbitraria (grupo de control)"]
        else:
            score, components, reasons = score_symbol(indicators)

        accepted.append(Candidate(
            symbol=symbol, score=score, price=price,
            indicators=indicators, reasons=reasons, components=components,
        ))

    # Tie-broken by symbol so the result is reproducible.
    accepted.sort(key=lambda c: (-c.score, c.symbol))
    report.candidates = accepted[:limits.top_n]

    log.info("Screener: %s", report.summary())
    if report.candidates:
        top = ", ".join(f"{c.symbol}({c.score:.2f})" for c in report.candidates[:8])
        log.info("Mejores candidatos: %s", top)
    return report


def load_universe(path: str) -> list[str]:
    """Reads a file of symbols, one per line. `#` is a comment."""
    from pathlib import Path

    file = Path(path).expanduser()
    if not file.is_file():
        raise FileNotFoundError(
            f"No se encontro el fichero de universo {file}. "
            "Generalo con: python tools/fetch_universe.py"
        )

    symbols: list[str] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        symbols.append(line.upper())

    # Orden estable y sin duplicados.
    return sorted(set(symbols))
