"""Tests of how the market snapshot is built.

They do not touch the network: what is tested is `build_snapshot`, which is where
the decision that matters most lives — separating the bar the decision is made on
from the bar execution happens on. If that breaks, the agent starts trading with
information from the future and the results stop meaning anything.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.indicators import Bar
from src.market_data import MIN_BARS, build_snapshot


def make_bars(count: int, *, start_price: float = 100.0, step: float = 0.5):
    """An ascending series with the open below the previous close, so the open
    and the close never coincide and the tests can tell them apart."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(count):
        close = start_price + index * step
        bars.append(
            Bar(
                timestamp=start + timedelta(days=index),
                open=close - 1.0,
                high=close + 0.5,
                low=close - 1.5,
                close=close,
                volume=1_000_000.0,
            )
        )
    return bars


# -- Separacion de precios ---------------------------------------------------

def test_decision_price_is_the_last_complete_close():
    bars = make_bars(80)

    snapshot = build_snapshot("AAPL", bars)

    # The last bar is reserved for execution: the decision uses the one before.
    assert snapshot.price == pytest.approx(bars[-2].close)


def test_fill_price_is_the_next_session_open():
    bars = make_bars(80)

    snapshot = build_snapshot("AAPL", bars)

    assert snapshot.fill_price == pytest.approx(bars[-1].open)
    assert snapshot.fill_basis == "next_open"


def test_decision_and_fill_prices_are_different():
    """It is the property that avoids look-ahead bias. If somebody 'simplifies'
    the code and makes them equal, this test fails."""
    snapshot = build_snapshot("AAPL", make_bars(80))

    assert snapshot.price != snapshot.fill_price


def test_execution_price_prefers_the_fill_price():
    snapshot = build_snapshot("AAPL", make_bars(80))

    assert snapshot.execution_price == pytest.approx(snapshot.fill_price)


def test_mark_price_is_the_latest_known_close():
    bars = make_bars(80)

    snapshot = build_snapshot("AAPL", bars)

    assert snapshot.mark_price == pytest.approx(bars[-1].close)
    assert snapshot.valuation_price == pytest.approx(bars[-1].close)


def test_session_is_the_execution_bar_date():
    bars = make_bars(80)

    snapshot = build_snapshot("AAPL", bars)

    assert snapshot.session == bars[-1].timestamp.strftime("%Y-%m-%d")


# -- Indicadores -------------------------------------------------------------

def test_indicators_ignore_the_execution_bar():
    """The last bar may be half-formed if the market is still open: it must not
    enter the indicators' computation."""
    bars = make_bars(80)

    snapshot = build_snapshot("AAPL", bars)

    assert snapshot.indicators["bars_available"] == len(bars) - 1
    assert snapshot.indicators["price"] == pytest.approx(bars[-2].close)


def test_recent_bars_end_at_the_decision_bar():
    bars = make_bars(80)

    snapshot = build_snapshot("AAPL", bars)

    assert len(snapshot.recent_bars) == 10
    assert snapshot.recent_bars[-1]["close"] == pytest.approx(bars[-2].close)


# -- Datos insuficientes -----------------------------------------------------

def test_too_few_bars_yields_none_instead_of_a_degraded_snapshot():
    assert build_snapshot("AAPL", make_bars(MIN_BARS)) is None


def test_exactly_enough_bars_is_accepted():
    """MIN_BARS decision bars plus one execution bar are needed."""
    assert build_snapshot("AAPL", make_bars(MIN_BARS + 1)) is not None


def test_a_single_bar_yields_none():
    assert build_snapshot("AAPL", make_bars(1)) is None


def test_no_bars_yields_none():
    assert build_snapshot("AAPL", []) is None


# -- Extraction from yfinance's DataFrame ------------------------------------

def test_yahoo_extraction_drops_rows_without_prices():
    """Holidays and sessions before the IPO arrive as NaN."""
    pandas = pytest.importorskip("pandas")
    from src.market_data import YahooMarketData

    index = pandas.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    frame = pandas.DataFrame(
        {
            "Open": [100.0, float("nan"), 102.0],
            "High": [101.0, float("nan"), 103.0],
            "Low": [99.0, float("nan"), 101.0],
            "Close": [100.5, float("nan"), 102.5],
            "Volume": [1_000_000.0, float("nan"), 1_200_000.0],
        },
        index=index,
    )

    bars = YahooMarketData._extract_bars(frame, "AAPL", single=True)

    assert len(bars) == 2
    assert [b.close for b in bars] == pytest.approx([100.5, 102.5])


def test_yahoo_extraction_of_an_unknown_symbol_returns_nothing():
    pandas = pytest.importorskip("pandas")
    from src.market_data import YahooMarketData

    frame = pandas.DataFrame({"Open": [1.0], "Close": [1.0]})

    assert YahooMarketData._extract_bars(frame, "NOPE", single=False) == []


def test_yahoo_extraction_requires_the_ohlcv_columns():
    pandas = pytest.importorskip("pandas")
    from src.market_data import YahooMarketData

    frame = pandas.DataFrame({"Close": [100.0], "Open": [99.0]})  # faltan High/Low/Volume

    assert YahooMarketData._extract_bars(frame, "AAPL", single=True) == []


def test_yahoo_extraction_makes_timestamps_timezone_aware():
    pandas = pytest.importorskip("pandas")
    from src.market_data import YahooMarketData

    frame = pandas.DataFrame(
        {"Open": [99.0], "High": [101.0], "Low": [98.0], "Close": [100.0],
         "Volume": [1_000.0]},
        index=pandas.to_datetime(["2026-01-02"]),
    )

    bars = YahooMarketData._extract_bars(frame, "AAPL", single=True)

    assert bars[0].timestamp.tzinfo is not None
