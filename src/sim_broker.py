"""Broker simulado: la contabilidad del experimento en SQLite.

Existe para que el experimento no exija dar de alta una cuenta de broker: el
objetivo es ver operar al agente y medir la calidad de sus decisiones, y para eso
un libro contable propio sirve igual.

Implementa el protocolo `Broker` de [broker.py](broker.py), que es lo unico que
`cycle.py` conoce: el ciclo no sabe que esta hablando con un simulador.

Honestidad de la simulacion, que es lo unico que la hace util:

  * **Se ejecuta a la apertura de la sesion siguiente**, no al cierre con el que
    se decidio. Ejecutar al mismo precio que se usa para decidir regala el hueco
    de la noche y convierte cualquier resultado en basura. `MarketSnapshot`
    mantiene los dos precios separados justamente para esto.
  * **Se aplica deslizamiento y comision.** Ambos configurables; por defecto 5
    puntos basicos y cero comision (los brokers americanos no cobran por acciones).
  * **No se puede comprar sin efectivo** ni vender lo que no se tiene.
  * **No hay apalancamiento.** El poder de compra es el efectivo disponible.

Lo que NO simula, y conviene saber: liquidez (una orden grande movería el precio
real), huecos intradia, ordenes parciales, horarios de mercado, ni las reglas de
patron day trader. A frecuencia diaria y en valores muy liquidos importa poco;
en ilíquidos, los resultados serian optimistas.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .broker import BrokerError, SubmittedOrder
from .db import Database
from .models import AccountState, BrokerPosition

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Quote:
    """Precios de un simbolo para este ciclo."""

    fill_price: float      # apertura de la sesion siguiente: donde se ejecuta
    mark_price: float      # ultimo precio conocido: valoracion
    basis: str = "next_open"


class SimBroker:
    """Broker simulado respaldado por SQLite."""

    def __init__(
        self,
        *,
        database: Database,
        portfolio_id: str,
        initial_cash: float,
        slippage_bps: float = 5.0,
        commission_per_order: float = 0.0,
    ) -> None:
        self.db = database
        self.account_id = portfolio_id
        self.slippage_bps = slippage_bps
        self.commission = commission_per_order
        self.paper = True
        self._quotes: dict[str, Quote] = {}
        self._ensure_account(initial_cash)

    # -- Estado interno ----------------------------------------------------

    def _ensure_account(self, initial_cash: float) -> None:
        rows = self.db.query("select * from sim_accounts where id = ?", (self.account_id,))
        if rows:
            return
        timestamp = _now()
        self.db.execute(
            "insert into sim_accounts "
            "(id, cash, initial_cash, last_equity, last_session, created_at, updated_at) "
            "values (?, ?, ?, ?, null, ?, ?)",
            (self.account_id, initial_cash, initial_cash, initial_cash,
             timestamp, timestamp),
        )
        log.info(
            "Cuenta simulada creada con %.2f USD de efectivo inicial.", initial_cash
        )

    def _account_row(self) -> dict:
        rows = self.db.query("select * from sim_accounts where id = ?", (self.account_id,))
        if not rows:
            raise BrokerError("La cuenta simulada ha desaparecido de la base de datos.")
        return rows[0]

    def _positions(self) -> list[dict]:
        return self.db.query(
            "select * from sim_positions where account_id = ? order by symbol",
            (self.account_id,),
        )

    def held_symbols(self) -> set[str]:
        """Simbolos con posicion abierta. Quien vaya a llamar a `set_quotes`
        necesita saber para que activos hay que traer precios."""
        return {str(row["symbol"]) for row in self._positions()}

    # -- Precios del ciclo -------------------------------------------------

    def set_quotes(self, quotes: dict[str, Quote]) -> None:
        """El ciclo inyecta aqui los precios de esta pasada.

        Sin esto el simulador no puede valorar ni ejecutar: no tiene fuente de
        datos propia a proposito, para que use exactamente los mismos precios que
        vio el analista.
        """
        self._quotes = dict(quotes)

    def roll_session(self, session: str | None) -> None:
        """Marca el comienzo de una sesion nueva.

        Fija `last_equity` al equity con el que se cierra la sesion anterior, que
        es la referencia del P&L diario y del kill switch. Si se ejecutan varios
        ciclos el mismo dia, la referencia no se mueve: si no, el kill switch se
        reiniciaria en cada pasada y dejaria de proteger.
        """
        if not session:
            return
        row = self._account_row()
        if row.get("last_session") == session:
            return

        equity = self._equity(row)
        self.db.execute(
            "update sim_accounts set last_equity = ?, last_session = ?, updated_at = ? "
            "where id = ?",
            (equity, session, _now(), self.account_id),
        )
        log.debug("Sesion %s: referencia de equity fijada en %.2f.", session, equity)

    # -- Lectura -----------------------------------------------------------

    def is_market_open(self) -> bool:
        """El simulador ejecuta siempre que tenga precios.

        No modela horarios: si hay una barra nueva, hay sesion que operar. Un
        broker real consultaria aqui el reloj del mercado.
        """
        return bool(self._quotes)

    def is_tradable(self, symbol: str) -> bool:
        return symbol in self._quotes

    def _mark(self, symbol: str, fallback: float) -> float:
        quote = self._quotes.get(symbol)
        return quote.mark_price if quote else fallback

    def _equity(self, row: dict | None = None) -> float:
        row = row or self._account_row()
        cash = float(row["cash"])
        holdings = sum(
            float(p["qty"]) * self._mark(p["symbol"], float(p["avg_entry_price"]))
            for p in self._positions()
        )
        return cash + holdings

    def get_account_state(self) -> AccountState:
        row = self._account_row()
        cash = float(row["cash"])

        positions: list[BrokerPosition] = []
        for raw in self._positions():
            symbol = str(raw["symbol"])
            qty = float(raw["qty"])
            entry = float(raw["avg_entry_price"])
            price = self._mark(symbol, entry)
            positions.append(
                BrokerPosition(
                    symbol=symbol,
                    qty=qty,
                    avg_entry_price=entry,
                    current_price=price,
                    market_value=qty * price,
                    unrealized_pl=(price - entry) * qty,
                    unrealized_pl_pct=(price / entry - 1) * 100 if entry else 0.0,
                )
            )

        equity = cash + sum(p.market_value for p in positions)
        return AccountState(
            equity=equity,
            cash=cash,
            # Sin apalancamiento: el poder de compra es el efectivo.
            buying_power=cash,
            last_equity=float(row["last_equity"]) or equity,
            positions=tuple(positions),
        )

    # -- Escritura ---------------------------------------------------------

    def buy_market(self, symbol: str, qty: float) -> SubmittedOrder:
        whole_qty = int(qty)
        if whole_qty < 1:
            raise BrokerError(f"Cantidad invalida para comprar {symbol}: {qty}.")

        quote = self._quotes.get(symbol)
        if quote is None:
            raise BrokerError(f"No hay precio de ejecucion para {symbol}.")

        # El deslizamiento va en contra: al comprar se paga un poco mas.
        price = quote.fill_price * (1 + self.slippage_bps / 10_000.0)
        cost = price * whole_qty + self.commission

        row = self._account_row()
        cash = float(row["cash"])
        if cost > cash + 1e-9:
            raise BrokerError(
                f"Efectivo insuficiente para comprar {whole_qty} de {symbol}: "
                f"hacen falta {cost:,.2f} y hay {cash:,.2f}."
            )

        existing = self.db.query(
            "select * from sim_positions where account_id = ? and symbol = ?",
            (self.account_id, symbol),
        )
        if existing:
            # No deberia ocurrir: el Risk Manager rechaza ampliar posiciones. Se
            # soporta de todos modos para que el libro contable nunca quede mal.
            old = existing[0]
            old_qty, old_price = float(old["qty"]), float(old["avg_entry_price"])
            new_qty = old_qty + whole_qty
            new_avg = (old_qty * old_price + whole_qty * price) / new_qty
            self.db.execute(
                "update sim_positions set qty = ?, avg_entry_price = ? where id = ?",
                (new_qty, new_avg, old["id"]),
            )
        else:
            self.db.execute(
                "insert into sim_positions "
                "(id, account_id, symbol, qty, avg_entry_price, opened_at) "
                "values (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), self.account_id, symbol, float(whole_qty),
                 price, _now()),
            )

        self.db.execute(
            "update sim_accounts set cash = ?, updated_at = ? where id = ?",
            (cash - cost, _now(), self.account_id),
        )
        self._record_fill(symbol, "buy", whole_qty, price, quote.basis)

        log.info(
            "[SIM] COMPRA %s: %d a %.4f (%s, deslizamiento %.0f pb). Efectivo: %.2f",
            symbol, whole_qty, price, quote.basis, self.slippage_bps, cash - cost,
        )
        return SubmittedOrder(
            broker_order_id=f"sim-{uuid.uuid4().hex[:12]}",
            symbol=symbol, side="buy", qty=float(whole_qty), status="filled",
            filled_qty=float(whole_qty), filled_avg_price=round(price, 4),
        )

    def sell_market(self, symbol: str, qty: float) -> SubmittedOrder:
        whole_qty = int(qty)
        if whole_qty < 1:
            raise BrokerError(f"Cantidad invalida para vender {symbol}: {qty}.")
        return self._sell(symbol, whole_qty)

    def close_position(self, symbol: str) -> SubmittedOrder:
        rows = self.db.query(
            "select * from sim_positions where account_id = ? and symbol = ?",
            (self.account_id, symbol),
        )
        if not rows:
            raise BrokerError(f"No hay posicion abierta en {symbol} que cerrar.")
        return self._sell(symbol, int(float(rows[0]["qty"])))

    def _sell(self, symbol: str, whole_qty: int) -> SubmittedOrder:
        quote = self._quotes.get(symbol)
        if quote is None:
            raise BrokerError(f"No hay precio de ejecucion para {symbol}.")

        rows = self.db.query(
            "select * from sim_positions where account_id = ? and symbol = ?",
            (self.account_id, symbol),
        )
        if not rows:
            raise BrokerError(f"No hay posicion abierta en {symbol}.")

        position = rows[0]
        held = float(position["qty"])
        if whole_qty > held + 1e-9:
            raise BrokerError(
                f"No se puede vender {whole_qty} de {symbol}: solo hay {held:g}. "
                "El simulador no permite ventas en corto."
            )

        # Al vender, el deslizamiento resta.
        price = quote.fill_price * (1 - self.slippage_bps / 10_000.0)
        proceeds = price * whole_qty - self.commission
        entry = float(position["avg_entry_price"])
        realized = (price - entry) * whole_qty - self.commission

        remaining = held - whole_qty
        if remaining <= 1e-9:
            self.db.execute("delete from sim_positions where id = ?", (position["id"],))
        else:
            self.db.execute(
                "update sim_positions set qty = ? where id = ?", (remaining, position["id"])
            )

        row = self._account_row()
        new_cash = float(row["cash"]) + proceeds
        self.db.execute(
            "update sim_accounts set cash = ?, updated_at = ? where id = ?",
            (new_cash, _now(), self.account_id),
        )
        self._record_fill(symbol, "sell", whole_qty, price, quote.basis, realized=realized)

        log.info(
            "[SIM] VENTA %s: %d a %.4f (%s). P&L %+.2f. Efectivo: %.2f",
            symbol, whole_qty, price, quote.basis, realized, new_cash,
        )
        return SubmittedOrder(
            broker_order_id=f"sim-{uuid.uuid4().hex[:12]}",
            symbol=symbol, side="sell", qty=float(whole_qty), status="filled",
            filled_qty=float(whole_qty), filled_avg_price=round(price, 4),
        )

    def _record_fill(
        self, symbol: str, side: str, qty: int, price: float, basis: str,
        realized: float | None = None,
    ) -> None:
        self.db.execute(
            "insert into sim_fills "
            "(account_id, symbol, side, qty, price, basis, slippage_bps, commission, "
            " realized_pnl, filled_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.account_id, symbol, side, float(qty), price, basis,
             self.slippage_bps, self.commission, realized, _now()),
        )
