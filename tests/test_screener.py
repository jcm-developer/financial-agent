"""Tests del screener y de la carga del universo.

Lo que mas importa comprobar son los descartes duros: si un valor ilíquido se
cuela, el broker simulado supondra que se puede comprar al precio de apertura sin
mover el mercado, y eso es una mentira que contamina el experimento entero.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.indicators import Bar
from src.screener import (
    ScreenerLimits,
    load_universe,
    score_symbol,
    screen,
)


def make_bars(
    count: int = 80,
    *,
    start: float = 100.0,
    step: float = 0.4,
    volume: float = 1_000_000.0,
    spread_pct: float = 0.02,
) -> list[Bar]:
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(count):
        close = start + index * step
        half = close * spread_pct / 2
        bars.append(
            Bar(
                timestamp=origin + timedelta(days=index),
                open=close - half,
                high=close + half,
                low=close - half,
                close=close,
                volume=volume,
            )
        )
    return bars


LIMITS = ScreenerLimits(
    top_n=3, min_dollar_volume=1_000_000.0, min_price=5.0,
    max_volatility_pct=120.0, min_bars=60,
)


# -- Descartes duros ---------------------------------------------------------

def test_a_symbol_with_too_few_bars_is_rejected():
    report = screen({"CORTO": make_bars(30)}, LIMITS)

    assert report.candidates == []
    assert report.rejected["datos_insuficientes"] == 1


def test_a_penny_stock_is_rejected():
    report = screen({"CHICHARRO": make_bars(80, start=1.0, step=0.01)}, LIMITS)

    assert report.candidates == []
    assert report.rejected["precio_bajo"] == 1


def test_an_illiquid_symbol_is_rejected():
    """El motivo real: el simulador supone ejecucion al precio de apertura sin
    impacto de mercado, y en un ilíquido eso no se sostiene."""
    report = screen({"ILIQUIDO": make_bars(80, volume=100.0)}, LIMITS)

    assert report.candidates == []
    assert report.rejected["iliquido"] == 1


def choppy_bars(count: int = 80, *, base: float = 100.0, swing: float = 0.10):
    """Serie que alterna subidas y bajadas fuertes: volatilidad alta de verdad.
    Una serie que sube en linea recta tiene volatilidad casi nula, aunque suba
    mucho — la volatilidad mide dispersion de retornos, no recorrido."""
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(count):
        close = base * (1 + swing if index % 2 else 1 - swing)
        bars.append(
            Bar(
                timestamp=origin + timedelta(days=index),
                open=close, high=close * 1.02, low=close * 0.98,
                close=close, volume=1_000_000.0,
            )
        )
    return bars


def test_an_extremely_volatile_symbol_is_rejected():
    report = screen({"LOCO": choppy_bars(80)}, LIMITS)

    assert report.candidates == []
    assert "demasiado_volatil" in report.rejected


def test_a_steadily_rising_series_is_not_considered_volatile():
    """Contraparte del anterior: recorrido no es lo mismo que volatilidad."""
    report = screen({"TRANQUILO": make_bars(80)}, LIMITS)

    assert "demasiado_volatil" not in report.rejected


def test_a_healthy_symbol_passes():
    report = screen({"BUENO": make_bars(80)}, LIMITS)

    assert [c.symbol for c in report.candidates] == ["BUENO"]
    assert report.evaluated == 1


# -- Puntuacion --------------------------------------------------------------

def test_the_score_stays_between_zero_and_one():
    from src.indicators import compute_snapshot

    for bars in (make_bars(80), make_bars(80, step=-0.3), make_bars(80, step=0.0)):
        score, _, _ = score_symbol(compute_snapshot(bars))
        assert 0.0 <= score <= 1.0


def test_an_uptrend_scores_higher_than_a_downtrend():
    from src.indicators import compute_snapshot

    up, _, _ = score_symbol(compute_snapshot(make_bars(250, step=0.4)))
    down, _, _ = score_symbol(compute_snapshot(make_bars(250, start=200.0, step=-0.4)))

    assert up > down


def test_the_components_add_up_to_the_score():
    """Si dejaran de cuadrar, el informe del screener estaria mintiendo sobre por
    que entro un candidato."""
    from src.indicators import compute_snapshot

    score, components, _ = score_symbol(compute_snapshot(make_bars(250)))

    assert sum(components.values()) == pytest.approx(score, abs=1e-4)


def indicators(**overrides):
    """Indicadores de un activo en tendencia sana, para variar uno a uno.

    Se construyen a mano en lugar de derivarlos de series sinteticas porque lo que
    se quiere probar es la logica de puntuacion, no el calculo de indicadores —que
    ya tiene sus propios tests.
    """
    base = {
        "price": 100.0, "sma_50": 95.0, "sma_200": 90.0, "rsi_14": 45.0,
        "return_60d_pct": 15.0, "volume_ratio": 1.2, "atr_pct": 2.0,
        "pct_from_52w_high": -8.0,
    }
    base.update(overrides)
    return base


def test_overbought_scores_lower_than_a_pullback():
    """El objetivo declarado del screener: no perseguir el precio. Misma tendencia,
    mismo momento, mismo volumen; solo cambia el RSI."""
    pullback = score_symbol(indicators(rsi_14=45))[0]
    overbought = score_symbol(indicators(rsi_14=85))[0]

    assert pullback > overbought


def test_a_perfect_trend_does_not_rescue_an_overbought_setup():
    """Este es el caso que fallaba cuando el RSI era un sumando: tendencia
    impecable y momento maximo compensaban el castigo por sobrecompra."""
    overbought_perfect = score_symbol(indicators(
        rsi_14=90, return_60d_pct=40.0, volume_ratio=2.5,
    ))[0]
    modest_pullback = score_symbol(indicators(
        rsi_14=45, return_60d_pct=8.0, volume_ratio=1.0,
    ))[0]

    assert modest_pullback > overbought_perfect


def test_oversold_scores_below_a_pullback_too():
    """Sobreventa puede ser un retroceso o una caida estructural; se penaliza,
    pero menos que la sobrecompra."""
    pullback = score_symbol(indicators(rsi_14=45))[0]
    oversold = score_symbol(indicators(rsi_14=20))[0]
    overbought = score_symbol(indicators(rsi_14=85))[0]

    assert pullback > oversold > overbought


def test_breaking_the_trend_lowers_the_score():
    intact = score_symbol(indicators(price=100.0, sma_50=95.0))[0]
    broken = score_symbol(indicators(price=100.0, sma_50=105.0))[0]

    assert intact > broken


def test_a_missing_rsi_is_penalised_but_not_zeroed():
    """Sin RSI no se puede juzgar la situacion, pero el activo sigue siendo
    analizable: se le baja la nota en lugar de descartarlo aqui."""
    known = score_symbol(indicators(rsi_14=45))[0]
    unknown = score_symbol(indicators(rsi_14=None))[0]

    assert 0 < unknown < known


# -- Orden y recorte --------------------------------------------------------

def test_only_the_top_n_are_returned():
    universe = {f"SYM{i}": make_bars(80, start=50 + i) for i in range(10)}

    report = screen(universe, LIMITS)

    assert len(report.candidates) == LIMITS.top_n
    assert report.evaluated == 10


def test_candidates_come_sorted_by_descending_score():
    universe = {f"SYM{i}": make_bars(80, start=50 + i * 3) for i in range(8)}

    report = screen(universe, LIMITS)
    scores = [c.score for c in report.candidates]

    assert scores == sorted(scores, reverse=True)


def test_ties_break_by_symbol_so_the_result_is_reproducible():
    universe = {sym: make_bars(80) for sym in ("ZZZ", "AAA", "MMM")}

    first = screen(universe, LIMITS).candidates
    second = screen(universe, LIMITS).candidates

    assert [c.symbol for c in first] == [c.symbol for c in second]
    assert [c.symbol for c in first] == ["AAA", "MMM", "ZZZ"]


# -- Modo control ------------------------------------------------------------

def test_random_mode_ignores_the_score_but_keeps_the_hard_filters():
    """Es el grupo de control: si el agente rinde igual con candidatos arbitrarios,
    el filtro no aporta nada. Los descartes duros siguen aplicandose."""
    universe = {f"SYM{i}": make_bars(80, start=50 + i) for i in range(6)}
    universe["ILIQUIDO"] = make_bars(80, volume=10.0)

    report = screen(universe, LIMITS, mode="random")

    assert len(report.candidates) == LIMITS.top_n
    assert "ILIQUIDO" not in {c.symbol for c in report.candidates}
    assert report.rejected["iliquido"] == 1
    assert all("control" in " ".join(c.reasons) for c in report.candidates)


def test_random_mode_is_stable_across_calls():
    universe = {f"SYM{i}": make_bars(80, start=50 + i) for i in range(6)}

    first = [c.symbol for c in screen(universe, LIMITS, mode="random").candidates]
    second = [c.symbol for c in screen(universe, LIMITS, mode="random").candidates]

    assert first == second


# -- Universo ----------------------------------------------------------------

def test_load_universe_ignores_comments_and_blank_lines(tmp_path):
    path = tmp_path / "u.txt"
    path.write_text(
        "# comentario\n\nAAPL\nmsft\n\n#  otro\nNVDA\n", encoding="utf-8"
    )

    assert load_universe(str(path)) == ["AAPL", "MSFT", "NVDA"]


def test_load_universe_deduplicates_and_sorts(tmp_path):
    path = tmp_path / "u.txt"
    path.write_text("MSFT\nAAPL\nMSFT\n", encoding="utf-8")

    assert load_universe(str(path)) == ["AAPL", "MSFT"]


def test_load_universe_explains_what_to_do_when_the_file_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="fetch_universe"):
        load_universe(str(tmp_path / "no-existe.txt"))


def test_the_bundled_sp500_file_is_usable():
    """El fichero que viaja con el proyecto tiene que estar bien formado, en
    notacion de Yahoo (guion, no punto)."""
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "universe" / "sp500.txt"
    if not path.is_file():
        pytest.skip("universe/sp500.txt no generado")

    symbols = load_universe(str(path))

    assert len(symbols) > 400
    assert all("." not in symbol for symbol in symbols)
    assert "AAPL" in symbols
