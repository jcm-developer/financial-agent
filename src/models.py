"""Domain types shared across the agent's layers.

They live in their own module so `risk` does not have to import `broker` or
`analyst`: the Risk Manager must be testable without network or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Action = Literal["buy", "sell", "hold"]
Kind = Literal["entry", "exit"]


@dataclass(frozen=True)
class BrokerPosition:
    """A position as the broker reports it. This is the source of truth."""

    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float


@dataclass(frozen=True)
class AccountState:
    """Snapshot of the account at the start of the cycle."""

    equity: float
    cash: float
    buying_power: float
    last_equity: float
    positions: tuple[BrokerPosition, ...] = ()

    @property
    def positions_value(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def open_symbols(self) -> set[str]:
        return {p.symbol for p in self.positions}

    @property
    def day_pnl(self) -> float:
        """The session's P&L. `last_equity` is the equity at the previous close."""
        return self.equity - self.last_equity

    @property
    def day_pnl_pct(self) -> float:
        if self.last_equity <= 0:
            return 0.0
        return self.day_pnl / self.last_equity * 100.0

    def position_for(self, symbol: str) -> BrokerPosition | None:
        for position in self.positions:
            if position.symbol == symbol:
                return position
        return None


@dataclass(frozen=True)
class Proposal:
    """The LLM analyst's proposal. It is NOT an order yet.

    No field here reaches the broker without passing through the Risk Manager.
    """

    symbol: str
    kind: Kind
    action: Action
    conviction: int
    thesis: str
    risks: str = ""
    horizon_days: int | None = None
    suggested_stop: float | None = None
    suggested_target: float | None = None
    #: What share of the capital the analyst wants in this position, in percent
    #: (F9.13). **It is a request and not a size**: `risk.py` treats it as one more
    #: cap, so it can only ask for less than `max_position_pct`, never more.
    #:
    #: It exists because the limit was behaving as the default. Before this, size
    #: came out of the risk budget capped by the limits, and with a 3 % risk and
    #: 1,2× ATR stops the budget never bound: every approved position landed on
    #: the ceiling. A 40 % ceiling is an allowance —"never more than this"— and
    #: what was missing was somebody deciding how much of it each idea deserves.
    #:
    #: It is deliberately **separate from `conviction`**. Overloading conviction
    #: would ruin the one number F5.7 measures —is a 70 right 7 times out of 10?—
    #: and in practice the model answers 70 to everything, so the size would be a
    #: constant dressed up as a judgement. "How sure am I" and "how much do I
    #: want" are different questions and now have different fields.
    suggested_weight_pct: float | None = None
    reference_price: float = 0.0
    # Metadata of the model call, for auditing.
    model: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskVerdict:
    """The Risk Manager's result. `approved=False` means nothing is sent to the
    broker, and the reason is recorded in `risk_events`."""

    approved: bool
    reason: str
    rule: str | None = None
    qty: float = 0.0
    notional: float = 0.0
    stop_price: float | None = None
    target_price: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExitSignal:
    """A close order. `forced=True` when a deterministic rule triggers it (stop
    or target reached) and therefore the LLM cannot veto it."""

    symbol: str
    qty: float
    reason: str
    rule: str
    forced: bool
    price: float


@dataclass(frozen=True)
class MarketSnapshot:
    """A symbol's market data at the moment of the analysis.

    The three prices are deliberately different, and the distinction is what
    avoids look-ahead bias:

      * `price`  -> close of the last COMPLETE session. It is the only thing the
                    analyst and the Risk Manager see, and what sizing is based on.
      * `fill_price` -> open of the following session, where execution happens. A
                    real system that decides on yesterday's close executes on
                    today's open; using the same close to decide and to execute
                    would hand over the overnight gap and falsify everything.
      * `mark_price` -> last known price, only for valuing the book.
    """

    symbol: str
    as_of: datetime
    price: float
    indicators: dict[str, Any]
    recent_bars: list[dict[str, Any]] = field(default_factory=list)
    snapshot_id: int | None = None
    fill_price: float | None = None
    mark_price: float | None = None
    fill_basis: str = "close"
    session: str | None = None

    @property
    def execution_price(self) -> float:
        """The price an order would execute at right now."""
        return self.fill_price if self.fill_price else self.price

    @property
    def valuation_price(self) -> float:
        """The price used to value an open position."""
        return self.mark_price if self.mark_price else self.price
