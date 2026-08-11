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


def make_bars(count: int, *, start_price: float = 100.0, step: float = 0.5,
              spread: float = 2.0):
    """An ascending series with the open below the previous close, so the open
    and the close never coincide and the tests can tell them apart.

    `spread` is each bar's high-to-low range, and it is a parameter because it is
    what the ATR measures: the two clocks of F9.14 differ in exactly this, and a
    fixed range would give the hourly and the daily series the same ATR.
    """
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(count):
        close = start_price + index * step
        bars.append(
            Bar(
                timestamp=start + timedelta(days=index),
                open=close - spread / 2,
                high=close + spread / 4,
                low=close - spread * 3 / 4,
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


# -- Los dos relojes: precio contra indicadores (F9.14) -----------------------

def test_the_price_comes_from_the_price_series_and_the_indicators_from_the_other():
    """The change of F9.14, and the one thing that must never drift back.

    With `bar_interval=1h` the indicators used to be computed on the same hourly
    series, so `atr_14` measured 14 hours: four times smaller than the 14 days the
    risk table of F6.5 is calibrated on.
    """
    hourly = make_bars(80, start_price=200.0, step=0.05, spread=0.5)
    daily = make_bars(80, start_price=100.0, step=1.0, spread=2.0)

    snapshot = build_snapshot("AIR.PA", hourly, indicator_bars=daily)

    assert snapshot.price == pytest.approx(hourly[-2].close)
    assert snapshot.fill_price == pytest.approx(hourly[-1].open)
    assert snapshot.as_of == hourly[-2].timestamp
    assert snapshot.indicators["price"] == pytest.approx(daily[-2].close)
    assert snapshot.recent_bars[-1]["close"] == pytest.approx(daily[-2].close)


def test_the_atr_comes_from_the_daily_series():
    """It is the figure the whole thing was about: `risk.py` places the stop at
    `price - atr * stop_atr_multiple`, and it reads the ATR from here."""
    # The measured ratio of F9.15 is 4,08x in median, so the fixture reproduces
    # it: an hourly range of 0,5 and a daily one of 2,0 over the same price.
    hourly = make_bars(80, start_price=100.0, step=0.05, spread=0.5)
    daily = make_bars(80, start_price=100.0, step=1.0, spread=2.0)

    mixed = build_snapshot("AIR.PA", hourly, indicator_bars=daily)
    only_hourly = build_snapshot("AIR.PA", hourly)

    assert mixed.indicators["atr_14"] == pytest.approx(
        only_hourly.indicators["atr_14"] * 4, rel=0.05
    )


def test_the_execution_bar_of_the_daily_series_is_reserved_too():
    """The session in progress is half-formed on both clocks."""
    daily = make_bars(80)

    snapshot = build_snapshot("AIR.PA", make_bars(80), indicator_bars=daily)

    assert snapshot.indicators["bars_available"] == len(daily) - 1


def test_without_a_second_series_nothing_changes():
    """`None` means "one clock", which is what a daily profile and the
    no-universe provider get. The old behaviour has to be preserved exactly."""
    bars = make_bars(80)

    assert build_snapshot("AAPL", bars) == build_snapshot(
        "AAPL", bars, indicator_bars=bars
    )


def test_the_sixty_bar_floor_applies_to_the_indicator_series():
    """The floor was always about the long indicators, so it follows the series
    they are computed on. The price series only needs a decision and an
    execution bar."""
    assert build_snapshot(
        "AIR.PA", make_bars(2), indicator_bars=make_bars(MIN_BARS + 1)
    ) is not None
    assert build_snapshot(
        "AIR.PA", make_bars(80), indicator_bars=make_bars(MIN_BARS)
    ) is None


def test_a_price_series_of_one_bar_yields_none():
    """There is no execution bar, so there is nothing to buy at."""
    assert build_snapshot(
        "AIR.PA", make_bars(1), indicator_bars=make_bars(80)
    ) is None


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
