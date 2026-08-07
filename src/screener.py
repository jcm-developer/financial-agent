"""Primera etapa del embudo: 500 activos -> 20 candidatos, sin IA.

Por que existe: a 45 segundos por llamada al modelo, analizar 500 activos costaria
seis horas por ciclo. El filtro reduce el universo con aritmetica —segundos, cero
cuota— y el LLM solo ve lo que ya parece interesante.

**Advertencia honesta sobre la puntuacion.** Los pesos de abajo son una heuristica
razonable, no una ventaja demostrada. No estan validados contra nada: son una
forma de ordenar el universo mejor que al azar, y de que el modelo reciba
candidatos con algo que mirar en lugar de diez megacaps elegidas a dedo. Si el
experimento acaba mostrando algo, sera imposible saber cuanto viene del filtro y
cuanto del modelo. Para separarlo habria que comparar contra un filtro aleatorio,
y `SCREENER_MODE=random` existe justo para eso.

Los descartes duros (liquidez, precio minimo, datos insuficientes) si son
defendibles: en un valor ilíquido el simulador mentiria, porque supone que puedes
comprar al precio de apertura sin mover el mercado.
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
    # Volumen medio en dolares de las ultimas 20 sesiones. Por debajo de esto la
    # simulacion de ejecucion no es creible.
    min_dollar_volume: float = 20_000_000.0
    min_price: float = 5.0
    # Volatilidad anualizada maxima en %. Por encima, el stop por ATR seria tan
    # ancho que la posicion resultante seria irrelevante.
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
    """Resultado completo, para poder auditar por que entro cada candidato."""

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
    """Multiplicador segun donde este el RSI. Es lo que evita perseguir el precio.

    Se aplica como factor y no como sumando por una razon concreta: sumando, un
    activo con tendencia perfecta y momento maximo compensaba de sobra el castigo
    por estar sobrecomprado y acababa ganando el ranking, que es exactamente lo
    contrario de lo que se pretende. Como factor, un RSI extremo hunde la nota por
    buenos que sean los demas componentes.
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
    """Puntua un activo entre 0 y 1.

    Cuatro componentes que suman como maximo 1.0, multiplicados por un factor de
    situacion derivado del RSI. La intencion es premiar tendencia establecida con
    un retroceso reciente —donde un analista tiene algo que decir— y castigar al
    activo que ya se ha disparado.

    `components` sale ya multiplicado por el factor, de modo que sus valores suman
    exactamente la puntuacion y el informe del screener no miente sobre por que
    entro un candidato.
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

    # --- Momento a medio plazo (0.25) ------------------------------------
    # Se normaliza a 20% de subida en 60 sesiones; por encima no puntua mas, para
    # que un activo disparado no acumule ventaja indefinidamente.
    momentum = _clamp((return_60 or 0.0) / 20.0) if return_60 is not None else 0.0
    components["momento"] = momentum * 0.25
    if momentum > 0.5 and return_60 is not None:
        reasons.append(f"momento 60 barras {return_60:+.1f}%")

    # --- Interes por volumen (0.20) --------------------------------------
    interest = _clamp(((volume_ratio or 1.0) - 0.8) / 1.2)
    components["volumen"] = interest * 0.20
    if (volume_ratio or 0) > 1.5:
        reasons.append(f"volumen {volume_ratio:.1f}x la media")

    # --- Volatilidad utilizable (0.15) -----------------------------------
    # Se premia el rango medio: sin movimiento no hay operacion posible, y con
    # demasiado el stop por ATR obliga a una posicion diminuta.
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
    """Aplica filtros duros y ordena por puntuacion.

    `mode="random"` sustituye la puntuacion por un orden estable pero arbitrario
    (hash del simbolo). Sirve como grupo de control: si el agente rinde igual con
    candidatos arbitrarios, el filtro no aporta nada.
    """
    report = ScreenerReport(candidates=[])
    accepted: list[Candidate] = []

    for symbol, bars in bars_by_symbol.items():
        report.evaluated += 1

        if len(bars) < limits.min_bars + 1:
            report.rejected["datos_insuficientes"] = report.rejected.get("datos_insuficientes", 0) + 1
            continue

        # La ultima barra se reserva para ejecutar, igual que en `market_data`.
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
            # Sin ATR el Risk Manager rechazaria la entrada de todos modos.
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

    # Desempate por simbolo para que el resultado sea reproducible.
    accepted.sort(key=lambda c: (-c.score, c.symbol))
    report.candidates = accepted[:limits.top_n]

    log.info("Screener: %s", report.summary())
    if report.candidates:
        top = ", ".join(f"{c.symbol}({c.score:.2f})" for c in report.candidates[:8])
        log.info("Mejores candidatos: %s", top)
    return report


def load_universe(path: str) -> list[str]:
    """Lee un fichero de simbolos, uno por linea. `#` es comentario."""
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
