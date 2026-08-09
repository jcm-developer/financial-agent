"""Technical indicators as pure functions over lists of floats.

Deliberately without numpy/pandas/TA-Lib: it is a few hundred bars per symbol,
the cost is irrelevant, and in exchange the module is trivial to test and drags
in no binary dependencies that break between Python versions.

Convention: every function returns `None` when there is not enough data for an
honest calculation, instead of a degraded value. The rest of the system treats
`None` as "indicator not available".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Bar:
    """One daily OHLCV bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def sma(values: list[float], period: int) -> float | None:
    """Simple moving average of the last `period` observations."""
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def ema(values: list[float], period: int) -> float | None:
    """Exponential moving average, seeded with the SMA of the first `period`."""
    if period <= 0 or len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = value * alpha + result * (1 - alpha)
    return result


def _wilder_smooth(values: list[float], period: int) -> float | None:
    """Wilder smoothing: seed = simple mean, then (prev*(n-1) + x)/n."""
    if period <= 0 or len(values) < period:
        return None
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (result * (period - 1) + value) / period
    return result


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI. >70 reads as overbought, <30 as oversold."""
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        delta = current - previous
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = _wilder_smooth(gains, period)
    avg_loss = _wilder_smooth(losses, period)
    if avg_gain is None or avg_loss is None:
        return None
    if avg_loss == 0:
        # No losses in the window: the RSI is saturated.
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def true_ranges(bars: list[Bar]) -> list[float]:
    """True Range of each bar from the second one on."""
    ranges: list[float] = []
    for previous, current in zip(bars, bars[1:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return ranges


def atr(bars: list[Bar], period: int = 14) -> float | None:
    """Wilder's Average True Range. It is the volatility measure used to place
    the stop and to size the position."""
    if len(bars) < period + 1:
        return None
    return _wilder_smooth(true_ranges(bars), period)


def macd(closes: list[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[float, float, float] | None:
    """Returns (macd, signal, histogram), or None when data is missing."""
    if len(closes) < slow + signal:
        return None
    # The full MACD series: needed before the signal EMA can be applied.
    macd_series: list[float] = []
    for end in range(slow, len(closes) + 1):
        window = closes[:end]
        fast_ema = ema(window, fast)
        slow_ema = ema(window, slow)
        if fast_ema is None or slow_ema is None:
            continue
        macd_series.append(fast_ema - slow_ema)

    signal_line = ema(macd_series, signal)
    if signal_line is None or not macd_series:
        return None
    macd_line = macd_series[-1]
    return macd_line, signal_line, macd_line - signal_line


def bollinger(closes: list[float], period: int = 20,
              num_std: float = 2.0) -> tuple[float, float, float] | None:
    """Returns (lower_band, mean, upper_band)."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = variance ** 0.5
    return mean - num_std * std, mean, mean + num_std * std


def pct_change(values: list[float], periods: int) -> float | None:
    """Percentage change over `periods` bars."""
    if len(values) < periods + 1:
        return None
    past = values[-(periods + 1)]
    if past == 0:
        return None
    return (values[-1] / past - 1.0) * 100.0


def annualized_volatility(closes: list[float], period: int = 20) -> float | None:
    """Annualised volatility in %, from daily returns (252 sessions)."""
    if len(closes) < period + 1:
        return None
    window = closes[-(period + 1):]
    returns = [
        (current / previous - 1.0)
        for previous, current in zip(window, window[1:])
        if previous != 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return (variance ** 0.5) * (252 ** 0.5) * 100.0


def compute_snapshot(bars: list[Bar]) -> dict[str, float | None]:
    """The bundle of indicators handed to the analyst.

    The keys are stable because they end up serialised in
    `market_snapshots.indicators` and queried later from SQL.
    """
    if not bars:
        return {}

    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    last = closes[-1]

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    atr14 = atr(bars, 14)
    macd_values = macd(closes)
    bands = bollinger(closes)

    window_52w = closes[-252:] if len(closes) >= 2 else closes
    high_52w = max(window_52w)
    low_52w = min(window_52w)

    avg_volume_20 = sma(volumes, 20)

    snapshot: dict[str, float | None] = {
        "price": round(last, 4),
        "sma_20": _round(sma20),
        "sma_50": _round(sma50),
        "sma_200": _round(sma200),
        "rsi_14": _round(rsi(closes, 14), 2),
        "atr_14": _round(atr14),
        "atr_pct": _round(atr14 / last * 100 if atr14 and last else None, 2),
        "macd": _round(macd_values[0] if macd_values else None),
        "macd_signal": _round(macd_values[1] if macd_values else None),
        "macd_hist": _round(macd_values[2] if macd_values else None),
        "bb_lower": _round(bands[0] if bands else None),
        "bb_upper": _round(bands[2] if bands else None),
        "return_5d_pct": _round(pct_change(closes, 5), 2),
        "return_20d_pct": _round(pct_change(closes, 20), 2),
        "return_60d_pct": _round(pct_change(closes, 60), 2),
        "volatility_20d_pct": _round(annualized_volatility(closes, 20), 2),
        "high_52w": _round(high_52w),
        "low_52w": _round(low_52w),
        "pct_from_52w_high": _round(
            (last / high_52w - 1.0) * 100 if high_52w else None, 2
        ),
        "pct_from_52w_low": _round(
            (last / low_52w - 1.0) * 100 if low_52w else None, 2
        ),
        "volume": round(volumes[-1], 2),
        "avg_volume_20": _round(avg_volume_20, 2),
        "volume_ratio": _round(
            volumes[-1] / avg_volume_20 if avg_volume_20 else None, 2
        ),
        "bars_available": len(bars),
    }

    # Derived signals, precomputed so the LLM is not forced to do arithmetic
    # (which is where it gets things wrong most often).
    snapshot["above_sma_50"] = _bool_to_float(last > sma50 if sma50 else None)
    snapshot["above_sma_200"] = _bool_to_float(last > sma200 if sma200 else None)
    snapshot["golden_cross"] = _bool_to_float(
        sma50 > sma200 if (sma50 and sma200) else None
    )
    return snapshot


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _bool_to_float(value: bool | None) -> float | None:
    """jsonb does not tell them apart, but 1/0 is kept so SQL can aggregate."""
    return None if value is None else float(value)
