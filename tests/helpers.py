"""Dobles y utilidades compartidas por los tests de ciclo.

Viven aqui y no dentro de un modulo de tests para que un fichero de tests no
tenga que importar a otro: eso ata el orden de ejecucion y rompe en cuanto
`tests/` no es un paquete.

Se sustituyen solo las dos fronteras externas —el modelo y la descarga de barras—
y todo lo demas corre de verdad: analista, Risk Manager, broker simulado y base
de datos.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.analyst import Analyst
from src.config import RiskLimits, Settings
from src.cycle import TradingCycle
from src.db import Database
from src.indicators import Bar
from src.llm import LLMResponse
from src.market_data import build_snapshot
from src.risk import RiskManager
from src.sim_broker import SimBroker

WATCHLIST = ("AAPL", "MSFT")


class StubLLM:
    """Devuelve una respuesta fija segun si se le pregunta por una entrada o por
    una salida. Distingue por el prompt de sistema, igual que haria el modelo."""

    def __init__(self, *, entry: dict, exit_: dict) -> None:
        self.entry = entry
        self.exit = exit_
        self.calls: list[str] = []

    def complete_json(self, *, system: str, user: str, max_tokens: int = 1600):
        is_exit = "gestor de riesgo discrecional" in system
        self.calls.append("exit" if is_exit else "entry")
        parsed = self.exit if is_exit else self.entry
        return LLMResponse(
            content=str(parsed), parsed=parsed, model="stub-model",
            latency_ms=12, prompt_tokens=100, completion_tokens=20,
        )


class StubMarketData:
    """Barras sinteticas. `closes` fija el cierre de cada sesion; la apertura se
    deriva para que precio de decision y de ejecucion nunca coincidan.

    Respeta el contrato real: devuelve sus propios candidatos mas los simbolos
    obligatorios que le pasen.
    """

    def __init__(self, closes_by_symbol: dict[str, list[float]]) -> None:
        self.closes = closes_by_symbol

    def fetch_snapshots(self, must_include=()):
        snapshots = {}
        for symbol in sorted(set(self.closes) | set(must_include)):
            closes = self.closes.get(symbol)
            if not closes:
                continue
            snapshot = build_snapshot(symbol, bars_from(closes))
            if snapshot is not None:
                snapshots[symbol] = snapshot
        return snapshots


def bars_from(closes: list[float]) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(
            timestamp=start + timedelta(days=index),
            open=close * 0.995,
            high=close * 1.01,
            low=close * 0.98,
            close=close,
            volume=2_000_000.0,
        )
        for index, close in enumerate(closes)
    ]


def rising(count: int = 80, start: float = 100.0, step: float = 0.4) -> list[float]:
    return [start + index * step for index in range(count)]


def make_settings(**overrides) -> Settings:
    defaults = dict(
        sim_slippage_bps=0.0, sim_commission=0.0,
        model_api_key="stub", model_base_url="http://stub",
        llm_model="stub-model", llm_temperature=0.0,
        llm_timeout_seconds=30.0, llm_max_retries=1,
        db_path=":memory:", portfolio_name="integracion",
        initial_budget=10_000.0, watchlist=WATCHLIST,
        lookback_days=200, dry_run=False, log_level="CRITICAL",
        bar_interval="1d",
        # Los tests no dependen del reloj: si respetaran el calendario, la suite
        # solo pasaria en horario de mercado.
        skip_when_market_closed=False,
        risk=RiskLimits(min_conviction=65, max_open_positions=5),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_cycle(
    db: Database, settings: Settings, llm: StubLLM, market_data: StubMarketData
) -> TradingCycle:
    portfolio_id = db.ensure_portfolio(
        name=settings.portfolio_name, mode=settings.mode,
        initial_budget=settings.initial_budget,
    )
    broker = SimBroker(
        database=db, portfolio_id=portfolio_id,
        initial_cash=settings.initial_budget,
        slippage_bps=settings.sim_slippage_bps,
        commission_per_order=settings.sim_commission,
    )
    return TradingCycle(
        settings=settings, broker=broker, market_data=market_data,
        database=db, analyst=Analyst(llm, interval=settings.bar_interval),
        risk_manager=RiskManager(settings.risk), portfolio_id=portfolio_id,
    )


BUY = {
    "action": "buy", "conviction": 85, "thesis": "Tendencia alcista intacta.",
    "risks": "Giro del mercado general.", "horizon_days": 15,
    "suggested_stop": None, "suggested_target": None,
}
HOLD_EXIT = {"action": "hold", "conviction": 70, "thesis": "La tesis sigue viva."}
SELL_EXIT = {"action": "sell", "conviction": 80, "thesis": "Deterioro tecnico."}
