"""Tests de la cache de barras.

No tocan la red: se sustituye `yf.download` por un doble que devuelve DataFrames
sinteticos. Lo que se comprueba es el comportamiento que hace la cache util —
idempotencia, refresco incremental y no insistir con simbolos muertos— porque un
error ahi se manifiesta como barras duplicadas o como un HTTP 429 de Yahoo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.bar_cache import MAX_FAILURES, BarCache, BarCacheError
from src.db import Database

pandas = pytest.importorskip("pandas")


@pytest.fixture
def db(tmp_path):
    with Database(path=tmp_path / "cache.db") as database:
        yield database


def frame_for(symbols: list[str], *, days: int = 5, start_price: float = 100.0):
    """DataFrame con el mismo aspecto que devuelve yfinance con group_by='ticker'."""
    index = pandas.to_datetime(
        [datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(days=i) for i in range(days)]
    )
    columns, data = [], {}
    for symbol in symbols:
        for field, offset in (
            ("Open", -0.5), ("High", 1.0), ("Low", -1.0), ("Close", 0.0),
        ):
            columns.append((symbol, field))
            data[(symbol, field)] = [start_price + i + offset for i in range(days)]
        columns.append((symbol, "Volume"))
        data[(symbol, "Volume")] = [1_000_000.0 + i for i in range(days)]
    return pandas.DataFrame(data, index=index, columns=pandas.MultiIndex.from_tuples(columns))


class FakeYFinance:
    """Doble de yfinance. Cuenta las peticiones y permite simular fallos."""

    def __init__(self, frames, *, fail_for: set[str] | None = None) -> None:
        self._frames = frames if isinstance(frames, list) else [frames]
        self.calls: list[list[str]] = []
        self.fail_for = fail_for or set()

    def download(self, *, tickers, start, interval, **kwargs):
        self.calls.append(list(tickers))
        if self.fail_for and set(tickers) & self.fail_for:
            raise RuntimeError("Yahoo devolvio 429")
        index = min(len(self.calls) - 1, len(self._frames) - 1)
        return self._frames[index]


@pytest.fixture
def patch_yf(monkeypatch):
    def apply(fake):
        import sys
        import types

        module = types.ModuleType("yfinance")
        module.download = fake.download
        monkeypatch.setitem(sys.modules, "yfinance", module)
        return fake
    return apply


# -- Construccion ------------------------------------------------------------

def test_an_unsupported_interval_is_refused(db):
    with pytest.raises(BarCacheError, match="1d o 1h"):
        BarCache(db, interval="5m")


def test_an_empty_cache_reports_nothing(db):
    cache = BarCache(db, interval="1d")

    assert cache.get_bars("AAPL") == []
    assert cache.stats() == {"simbolos": 0, "barras": 0, "caidos": 0}


# -- Refresco ----------------------------------------------------------------

def test_a_first_refresh_stores_the_bars(db, patch_yf):
    cache = BarCache(db, interval="1d")
    patch_yf(FakeYFinance(frame_for(["AAPL", "MSFT"])))

    summary = cache.refresh(["AAPL", "MSFT"], lookback_days=30)

    assert summary["barras"] == 10          # 5 barras x 2 simbolos
    assert summary["fallos"] == 0
    assert len(cache.get_bars("AAPL")) == 5
    assert cache.stats()["simbolos"] == 2


def test_bars_come_back_in_chronological_order(db, patch_yf):
    cache = BarCache(db, interval="1d")
    patch_yf(FakeYFinance(frame_for(["AAPL"])))
    cache.refresh(["AAPL"], lookback_days=30)

    bars = cache.get_bars("AAPL")
    timestamps = [b.timestamp for b in bars]

    assert timestamps == sorted(timestamps)


def test_refreshing_twice_does_not_duplicate_bars(db, patch_yf):
    """La idempotencia es lo que permite reescribir la ultima barra en cada ciclo
    mientras el mercado esta abierto, sin acumular filas."""
    cache = BarCache(db, interval="1d")
    fake = patch_yf(FakeYFinance(frame_for(["AAPL"])))

    cache.refresh(["AAPL"], lookback_days=30)
    cache.refresh(["AAPL"], lookback_days=30)

    assert len(cache.get_bars("AAPL")) == 5
    assert len(fake.calls) == 2      # si se pidio dos veces, no se duplico igual


def test_the_last_bar_is_overwritten_with_fresh_data(db, patch_yf):
    """Caso real: a media sesion la ultima barra cambia de cierre."""
    cache = BarCache(db, interval="1d")
    first = frame_for(["AAPL"], days=3, start_price=100.0)
    second = frame_for(["AAPL"], days=3, start_price=100.0)
    second[("AAPL", "Close")] = [100.0, 101.0, 999.0]   # el ultimo cierre cambia
    patch_yf(FakeYFinance([first, second]))

    cache.refresh(["AAPL"], lookback_days=30)
    cache.refresh(["AAPL"], lookback_days=30)

    assert cache.get_bars("AAPL")[-1].close == pytest.approx(999.0)
    assert len(cache.get_bars("AAPL")) == 3


def test_symbols_are_batched_into_few_requests(db, patch_yf):
    """El motivo de existir de la cache: 500 simbolos no pueden ser 500 peticiones."""
    symbols = [f"SYM{i:03d}" for i in range(150)]
    cache = BarCache(db, interval="1d")
    fake = patch_yf(FakeYFinance(frame_for(symbols, days=2)))

    cache.refresh(symbols, lookback_days=30)

    assert len(fake.calls) == 3          # 150 / BATCH_SIZE(60) -> 3 lotes
    assert sum(len(call) for call in fake.calls) == 150


def test_get_bars_respects_the_limit(db, patch_yf):
    cache = BarCache(db, interval="1d")
    patch_yf(FakeYFinance(frame_for(["AAPL"], days=10)))
    cache.refresh(["AAPL"], lookback_days=30)

    assert len(cache.get_bars("AAPL", limit=4)) == 4


def test_intervals_are_stored_separately(db, patch_yf):
    """Las barras diarias y horarias del mismo simbolo no deben mezclarse."""
    patch_yf(FakeYFinance(frame_for(["AAPL"], days=4)))
    BarCache(db, interval="1d").refresh(["AAPL"], lookback_days=30)

    assert len(BarCache(db, interval="1d").get_bars("AAPL")) == 4
    assert BarCache(db, interval="1h").get_bars("AAPL") == []


# -- Fallos ------------------------------------------------------------------

def test_a_failing_download_is_counted_not_swallowed(db, patch_yf):
    cache = BarCache(db, interval="1d")
    patch_yf(FakeYFinance(frame_for(["AAPL"]), fail_for={"MUERTO"}))

    summary = cache.refresh(["MUERTO"], lookback_days=30)

    assert summary["fallos"] == 1
    row = db.query("select failures, last_error from bar_cache_state")[0]
    assert row["failures"] == 1
    assert "429" in row["last_error"]


def test_a_symbol_yahoo_does_not_know_stops_being_requested(db, patch_yf):
    """Un ticker excluido del indice o fusionado dejaria de existir en Yahoo.
    Insistir en cada ciclo gastaria peticiones para nada."""
    cache = BarCache(db, interval="1d")
    fake = patch_yf(FakeYFinance(frame_for(["AAPL"]), fail_for={"MUERTO"}))

    for _ in range(MAX_FAILURES):
        cache.refresh(["MUERTO"], lookback_days=30)
    calls_before = len(fake.calls)

    summary = cache.refresh(["MUERTO"], lookback_days=30)

    assert summary["omitidos"] == 1
    assert len(fake.calls) == calls_before      # no se pidio de nuevo
    assert cache.stats()["caidos"] == 1


def test_force_full_retries_even_a_dropped_symbol(db, patch_yf):
    cache = BarCache(db, interval="1d")
    fake = patch_yf(FakeYFinance(frame_for(["AAPL"]), fail_for={"MUERTO"}))
    for _ in range(MAX_FAILURES):
        cache.refresh(["MUERTO"], lookback_days=30)
    calls_before = len(fake.calls)

    cache.refresh(["MUERTO"], lookback_days=30, force_full=True)

    assert len(fake.calls) == calls_before + 1


def test_a_successful_refresh_clears_previous_failures(db, patch_yf):
    cache = BarCache(db, interval="1d")
    fake = FakeYFinance(frame_for(["FLAKY"]), fail_for={"FLAKY"})
    patch_yf(fake)
    cache.refresh(["FLAKY"], lookback_days=30)
    assert db.query("select failures from bar_cache_state")[0]["failures"] == 1

    fake.fail_for = set()
    cache.refresh(["FLAKY"], lookback_days=30)

    assert db.query("select failures from bar_cache_state")[0]["failures"] == 0


def test_rows_without_prices_are_skipped(db, patch_yf):
    """Festivos y sesiones previas a la salida a bolsa llegan como NaN."""
    frame = frame_for(["AAPL"], days=4)
    frame.loc[frame.index[1], ("AAPL", "Close")] = float("nan")
    frame.loc[frame.index[1], ("AAPL", "Open")] = float("nan")
    cache = BarCache(db, interval="1d")
    patch_yf(FakeYFinance(frame))

    cache.refresh(["AAPL"], lookback_days=30)

    assert len(cache.get_bars("AAPL")) == 3
