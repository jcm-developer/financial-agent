"""El contrato que `cycle.py` espera de un broker.

Aqui no hay implementacion: solo el error, la forma de una orden enviada y el
protocolo que describe la superficie. La unica implementacion es
[sim_broker.py](sim_broker.py), que lleva la contabilidad en SQLite.

El protocolo se conserva aunque haya un solo broker por dos razones concretas:
`cycle.py` puede anotar su dependencia sin importar el simulador (y sin el ciclo
de imports que eso crearia), y el dia que se anada un broker real hay un sitio
donde esta escrito que metodos hacen falta, en lugar de descubrirlos a base de
`AttributeError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .models import AccountState


class BrokerError(RuntimeError):
    """El broker rechazo la operacion o no se pudo contactar."""


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
    """Lo que el ciclo necesita poder pedirle a un broker."""

    def is_market_open(self) -> bool:
        ...

    def get_account_state(self) -> AccountState:
        ...

    def is_tradable(self, symbol: str) -> bool:
        ...

    def buy_market(self, symbol: str, qty: float) -> SubmittedOrder:
        ...

    def sell_market(self, symbol: str, qty: float) -> SubmittedOrder:
        ...

    def close_position(self, symbol: str) -> SubmittedOrder:
        ...
