"""Envoltorio sobre Alpaca. Es el unico modulo que puede mover dinero real.

Normaliza los objetos del SDK a los dataclasses de `models`, de modo que el
resto del sistema no dependa de la forma concreta de la API de Alpaca y pueda
testearse con dobles.

Solo se usa con `BROKER=alpaca`; por defecto el sistema opera con `SimBroker`.
Por eso `alpaca-py` se importa de forma diferida: quien no vaya a usar Alpaca no
necesita instalarlo, y este modulo se puede importar igualmente.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .models import AccountState, BrokerPosition

log = logging.getLogger(__name__)


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


def _require_alpaca():
    """Importa el SDK, con un mensaje util si no esta instalado."""
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
    except ImportError as exc:  # pragma: no cover
        raise BrokerError(
            "BROKER=alpaca necesita el paquete alpaca-py, que no esta instalado.\n"
            "  Instalalo con:  pip install -r requirements-alpaca.txt\n"
            "  O vuelve al broker simulado poniendo BROKER=sim en el .env."
        ) from exc
    return TradingClient, OrderSide, TimeInForce, MarketOrderRequest


class Broker:
    def __init__(self, *, api_key: str, secret_key: str, paper: bool = True) -> None:
        TradingClient, *_ = _require_alpaca()
        self.paper = paper
        self._client = TradingClient(
            api_key=api_key, secret_key=secret_key, paper=paper
        )
        if not paper:
            log.warning(
                "Broker inicializado en modo DINERO REAL. Las ordenes son irreversibles."
            )

    # -- Lectura -----------------------------------------------------------

    def is_market_open(self) -> bool:
        try:
            return bool(self._client.get_clock().is_open)
        except Exception as exc:  # noqa: BLE001 - el SDK lanza tipos variados
            raise BrokerError(f"No se pudo consultar el reloj del mercado: {exc}") from exc

    def get_account_state(self) -> AccountState:
        try:
            account = self._client.get_account()
            raw_positions = self._client.get_all_positions()
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"No se pudo leer el estado de la cuenta: {exc}") from exc

        positions = tuple(
            BrokerPosition(
                symbol=str(p.symbol),
                qty=_to_float(p.qty),
                avg_entry_price=_to_float(p.avg_entry_price),
                current_price=_to_float(p.current_price),
                market_value=_to_float(p.market_value),
                unrealized_pl=_to_float(p.unrealized_pl),
                unrealized_pl_pct=_to_float(p.unrealized_plpc) * 100.0,
            )
            for p in raw_positions
        )

        equity = _to_float(account.equity)
        return AccountState(
            equity=equity,
            cash=_to_float(account.cash),
            buying_power=_to_float(account.buying_power),
            # `last_equity` es el equity al cierre de la sesion anterior: es la
            # referencia correcta para el P&L del dia.
            last_equity=_to_float(account.last_equity) or equity,
            positions=positions,
        )

    def is_tradable(self, symbol: str) -> bool:
        """Comprueba que el activo existe y admite operaciones."""
        try:
            asset = self._client.get_asset(symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo consultar el activo %s: %s", symbol, exc)
            return False
        return bool(getattr(asset, "tradable", False))

    # -- Escritura ---------------------------------------------------------

    def buy_market(self, symbol: str, qty: float) -> SubmittedOrder:
        """Orden de compra a mercado por un numero entero de acciones.

        `time_in_force=DAY` es deliberado: si no se ejecuta en la sesion, la
        orden caduca en lugar de dispararse al dia siguiente con una tesis vieja.
        """
        whole_qty = int(qty)
        if whole_qty < 1:
            raise BrokerError(f"Cantidad invalida para comprar {symbol}: {qty}.")

        _, OrderSide, TimeInForce, MarketOrderRequest = _require_alpaca()
        request = MarketOrderRequest(
            symbol=symbol,
            qty=whole_qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        return self._submit(request, symbol=symbol, side="buy", qty=float(whole_qty))

    def sell_market(self, symbol: str, qty: float) -> SubmittedOrder:
        whole_qty = int(qty)
        if whole_qty < 1:
            raise BrokerError(f"Cantidad invalida para vender {symbol}: {qty}.")

        _, OrderSide, TimeInForce, MarketOrderRequest = _require_alpaca()
        request = MarketOrderRequest(
            symbol=symbol,
            qty=whole_qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        return self._submit(request, symbol=symbol, side="sell", qty=float(whole_qty))

    def close_position(self, symbol: str) -> SubmittedOrder:
        """Cierra la posicion completa. Lo prefiere `executor` frente a
        `sell_market` porque evita desajustes si la cantidad real cambio."""
        try:
            order = self._client.close_position(symbol)
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"No se pudo cerrar la posicion en {symbol}: {exc}") from exc
        return _normalize_order(order, symbol=symbol, side="sell")

    def _submit(
        self, request: MarketOrderRequest, *, symbol: str, side: str, qty: float
    ) -> SubmittedOrder:
        try:
            order = self._client.submit_order(order_data=request)
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(
                f"El broker rechazo la orden {side} {qty:g} {symbol}: {exc}"
            ) from exc
        return _normalize_order(order, symbol=symbol, side=side, fallback_qty=qty)


def _normalize_order(
    order: Any, *, symbol: str, side: str, fallback_qty: float = 0.0
) -> SubmittedOrder:
    status = getattr(order, "status", None)
    return SubmittedOrder(
        broker_order_id=str(getattr(order, "id", "") or ""),
        symbol=str(getattr(order, "symbol", symbol) or symbol),
        side=str(getattr(getattr(order, "side", None), "value", side) or side),
        qty=_to_float(getattr(order, "qty", fallback_qty)) or fallback_qty,
        status=str(getattr(status, "value", status) or "submitted"),
        filled_qty=_to_float(getattr(order, "filled_qty", None)) or None,
        filled_avg_price=_to_float(getattr(order, "filled_avg_price", None)) or None,
    )


def _to_float(value: Any) -> float:
    """Alpaca devuelve numeros como cadenas o Decimal segun el campo."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
