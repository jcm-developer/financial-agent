"""The contract `cycle.py` expects from a broker.

There is no implementation here: only the error, the shape of a submitted order
and the protocol describing the surface. The only implementation is
[sim_broker.py](sim_broker.py), which keeps its books in SQLite.

The protocol is kept even with a single broker for two concrete reasons:
`cycle.py` can declare its dependency without importing the simulator (and
without the import cycle that would create), and the day a real broker is added
there is one place where it is written down which methods are needed, instead of
discovering them through `AttributeError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .models import AccountState


class BrokerError(RuntimeError):
    """The broker rejected the operation, or could not be reached."""


@dataclass(frozen=True)
class SubmittedOrder:
    broker_order_id: str
    symbol: str
    side: str
    qty: float
    status: str
    filled_qty: float | None = None
    filled_avg_price: float | None = None


@runtime_checkable
class Broker(Protocol):
    """What the cycle needs to be able to ask a broker for."""

    def is_market_open(self) -> bool:
        ...

    def get_account_state(self) -> AccountState:
        ...

    def is_tradable(self, symbol: str) -> bool:
        ...

    def commission_for(self, symbol: str) -> float:
        """What one leg on `symbol` costs, before executing it.

        It is on the protocol and not read from `fees.py` by whoever needs it
        because **the Risk Manager sizes with this number** (F9.9): the commission
        comes out of the same cash that pays for the shares, and the round trip is
        what decides whether a reward/risk ratio is real. A broker that charges
        differently —or not at all— has to be able to say so without the risk
        rules knowing which broker they are talking to.
        """
        ...

    def buy_market(self, symbol: str, qty: float) -> SubmittedOrder:
        ...

    def sell_market(self, symbol: str, qty: float) -> SubmittedOrder:
        ...

    def close_position(self, symbol: str) -> SubmittedOrder:
        ...
