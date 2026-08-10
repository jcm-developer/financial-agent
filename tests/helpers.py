"""Doubles and utilities shared by the cycle's tests.

They live here and not inside a test module so no test file has to import
another: that ties the execution order and breaks the moment `tests/` is not a
package.

Only the two external boundaries are replaced —the model and the bar download—
and everything else runs for real: analyst, Risk Manager, simulated broker and
database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.analyst import Analyst
from src.config import RiskLimits, Settings
from src.cycle import TradingCycle
from src.db import Database
from src.indicators import Bar
from src.llm import LLMResponse
from src.market_calendar import get_market
from src.market_data import build_snapshot
from src.risk import RiskManager
from src.sim_broker import SimBroker

WATCHLIST = ("AAPL", "MSFT")


class StubLLM:
    """Returns a fixed response depending on whether it is asked about an entry
    or an exit. It tells them apart by the system prompt, as the model would."""

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
    """Synthetic bars. `closes` sets each session's close; the open is derived so
    the decision price and the execution price never coincide.

    It honours the real contract: it returns its own candidates plus whatever
    mandatory symbols it is given.
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
        # The tests do not depend on the clock: if they honoured the calendar,
        # the suite would only pass during market hours.
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
        extra_commission=settings.sim_commission,
    )
    return TradingCycle(
        settings=settings, broker=broker, market_data=market_data,
        database=db, analyst=Analyst(llm, interval=settings.bar_interval),
        # Wired like `TradingCycle.build`: the risk manager gets the profile's
        # currency, because its verdict text is stored and shown as it is.
        risk_manager=RiskManager(
            settings.risk,
            currency_symbol=get_market(settings.market).currency_symbol,
        ),
        portfolio_id=portfolio_id,
    )


BUY = {
    "action": "buy", "conviction": 85, "thesis": "Tendencia alcista intacta.",
    "risks": "Giro del mercado general.", "horizon_days": 15,
    "suggested_stop": None, "suggested_target": None,
}
HOLD_EXIT = {"action": "hold", "conviction": 70, "thesis": "La tesis sigue viva."}
SELL_EXIT = {"action": "sell", "conviction": 80, "thesis": "Deterioro tecnico."}
