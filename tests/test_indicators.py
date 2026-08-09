"""Tests of the indicators.

The expected values are worked out by hand in the simple cases. What matters most
here is the "None when there is not enough data" contract: if an indicator
returned a degraded value, the Risk Manager would size positions over an invented
volatility.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.indicators import (
    Bar,
    annualized_volatility,
    atr,
    bollinger,
    compute_snapshot,
    ema,
    macd,
    pct_change,
    rsi,
    sma,
    true_ranges,
)


def make_bars(closes, *, spread=1.0):
    """Barras sinteticas con maximo y minimo simetricos alrededor del cierre."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(
            timestamp=start + timedelta(days=index),
            open=close,
            high=close + spread,
            low=close - spread,
            close=close,
            volume=1_000_000.0,
        )
        for index, close in enumerate(closes)
    ]


# -- Medias ------------------------------------------------------------------

def test_sma_averages_the_last_n_values():
    assert sma([1, 2, 3, 4, 5], 5) == pytest.approx(3.0)
    assert sma([1, 2, 3, 4, 5], 2) == pytest.approx(4.5)


def test_sma_returns_none_without_enough_data():
    assert sma([1, 2], 5) is None


def test_ema_of_a_constant_series_is_that_constant():
    assert ema([10.0] * 30, 10) == pytest.approx(10.0)


def test_ema_reacts_faster_than_sma_to_a_jump():
    series = [10.0] * 20 + [20.0] * 5
    assert ema(series, 10) > sma(series, 10)


# -- RSI ---------------------------------------------------------------------

def test_rsi_saturates_at_100_when_every_day_rises():
    assert rsi([float(i) for i in range(1, 40)], 14) == pytest.approx(100.0)


def test_rsi_saturates_at_zero_when_every_day_falls():
    assert rsi([float(i) for i in range(40, 1, -1)], 14) == pytest.approx(0.0, abs=1e-9)


def test_rsi_of_a_flat_series_is_neutral():
    assert rsi([100.0] * 30, 14) == pytest.approx(50.0)


def test_rsi_stays_inside_its_bounds():
    closes = [100 + (7 * i % 11) - 5 for i in range(60)]
    value = rsi([float(c) for c in closes], 14)
    assert 0.0 <= value <= 100.0


def test_rsi_needs_period_plus_one_observations():
    assert rsi([1.0] * 14, 14) is None
    assert rsi([1.0] * 15, 14) is not None


# -- ATR ---------------------------------------------------------------------

def test_true_range_uses_the_widest_of_the_three_measures():
    bars = [
        Bar(datetime(2024, 1, 1, tzinfo=timezone.utc), 100, 102, 98, 100, 1),
        # Hueco al alza: el maximo respecto al cierre anterior manda (110-100=10).
        Bar(datetime(2024, 1, 2, tzinfo=timezone.utc), 108, 110, 106, 109, 1),
    ]
    assert true_ranges(bars) == [pytest.approx(10.0)]


def test_atr_of_constant_range_bars_equals_that_range():
    """Cierres planos y spread de 1.0 -> rango verdadero constante de 2.0."""
    assert atr(make_bars([100.0] * 30, spread=1.0), 14) == pytest.approx(2.0)


def test_atr_returns_none_without_enough_bars():
    assert atr(make_bars([100.0] * 14), 14) is None


def test_atr_grows_with_volatility():
    calm = atr(make_bars([100.0] * 30, spread=0.5), 14)
    wild = atr(make_bars([100.0] * 30, spread=5.0), 14)
    assert wild > calm


# -- MACD y Bollinger --------------------------------------------------------

def test_macd_of_a_flat_series_is_zero():
    result = macd([100.0] * 80)
    assert result is not None
    macd_line, signal_line, histogram = result
    assert macd_line == pytest.approx(0.0)
    assert signal_line == pytest.approx(0.0)
    assert histogram == pytest.approx(0.0)


def test_macd_is_positive_in_an_uptrend():
    result = macd([float(i) for i in range(1, 90)])
    assert result is not None
    assert result[0] > 0


def test_macd_returns_none_without_enough_data():
    assert macd([100.0] * 20) is None


def test_bollinger_bands_straddle_the_mean():
    lower, mean, upper = bollinger([100.0, 102.0] * 10, 20)
    assert lower < mean < upper
    assert mean == pytest.approx(101.0)


def test_bollinger_of_a_flat_series_collapses_to_the_mean():
    lower, mean, upper = bollinger([100.0] * 20, 20)
    assert lower == pytest.approx(mean) == pytest.approx(upper)


# -- Retornos y volatilidad --------------------------------------------------

def test_pct_change_measures_the_right_window():
    assert pct_change([100.0, 110.0], 1) == pytest.approx(10.0)
    assert pct_change([100.0, 105.0, 110.0], 2) == pytest.approx(10.0)


def test_pct_change_returns_none_without_enough_history():
    assert pct_change([100.0], 5) is None


def test_annualized_volatility_of_a_flat_series_is_zero():
    assert annualized_volatility([100.0] * 30, 20) == pytest.approx(0.0)


def test_annualized_volatility_grows_with_dispersion():
    steady = [100.0 + i * 0.1 for i in range(40)]
    choppy = [100.0 + (10 if i % 2 else -10) for i in range(40)]
    assert annualized_volatility(choppy, 20) > annualized_volatility(steady, 20)


# -- Snapshot completo -------------------------------------------------------

def test_snapshot_has_stable_keys():
    """These keys end up in the database and are queried from SQL: if they
    change, the history stops being comparable."""
    snapshot = compute_snapshot(make_bars([100.0 + i * 0.5 for i in range(250)]))

    expected = {
        "price", "sma_20", "sma_50", "sma_200", "rsi_14", "atr_14", "atr_pct",
        "macd", "macd_signal", "macd_hist", "bb_lower", "bb_upper",
        "return_5d_pct", "return_20d_pct", "return_60d_pct", "volatility_20d_pct",
        "high_52w", "low_52w", "pct_from_52w_high", "pct_from_52w_low",
        "volume", "avg_volume_20", "volume_ratio", "bars_available",
        "above_sma_50", "above_sma_200", "golden_cross",
    }
    assert set(snapshot) == expected


def test_snapshot_marks_an_uptrend():
    snapshot = compute_snapshot(make_bars([100.0 + i * 0.5 for i in range(250)]))

    assert snapshot["above_sma_50"] == 1.0
    assert snapshot["above_sma_200"] == 1.0
    assert snapshot["golden_cross"] == 1.0
    assert snapshot["pct_from_52w_high"] == pytest.approx(0.0)


def test_snapshot_reports_none_for_long_indicators_on_short_history():
    """With 80 bars there is no SMA200: it must be None, not an approximation."""
    snapshot = compute_snapshot(make_bars([100.0] * 80))

    assert snapshot["sma_200"] is None
    assert snapshot["sma_50"] is not None
    assert snapshot["bars_available"] == 80


def test_snapshot_of_no_bars_is_empty():
    assert compute_snapshot([]) == {}
