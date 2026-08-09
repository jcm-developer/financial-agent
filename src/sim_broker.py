"""Simulated broker: the experiment's bookkeeping in SQLite.

It exists so the experiment does not demand opening a broker account: the goal is
to watch the agent trade and measure the quality of its decisions, and a ledger
of our own serves that just as well.

It implements the `Broker` protocol from [broker.py](broker.py), which is all
`cycle.py` knows about: the cycle has no idea it is talking to a simulator.

The honesty of the simulation, which is the only thing that makes it useful:

  * **Execution happens at the next session's open**, not at the close the
    decision was made on. Executing at the same price used to decide hands over
    the overnight gap and turns any result into rubbish. `MarketSnapshot` keeps
    the two prices apart precisely for this.
  * **Slippage and commission are applied.** Slippage is 5 basis points by
    default; the commission is the bank's tariff for the symbol's exchange
    ([fees.py](fees.py)) plus whatever surcharge the profile adds on top. It is
    charged on both legs, so a round trip pays it twice.
  * **You cannot buy without cash** nor sell what you do not hold.
  * **There is no leverage.** Buying power is the available cash.

What it does NOT simulate, and is worth knowing: liquidity (a large order would
move the real price), intraday gaps, partial fills, market hours, or the pattern
day trader rules. At daily frequency and in very liquid names it matters little;
in illiquid ones, the results would be optimistic.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from . import fees
from .broker import BrokerError, SubmittedOrder
from .db import Database
from .models import AccountState, BrokerPosition

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Quote:
    """A symbol's prices for this cycle."""

    fill_price: float      # next session's open: where execution happens
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
        extra_commission: float = 0.0,
    ) -> None:
        self.db = database
        self.account_id = portfolio_id
        self.slippage_bps = slippage_bps
        #: Surcharge on top of the bank's tariff, from `agent_settings`. Zero --
        #: the default -- means the standard and nothing else.
        self.extra_commission = extra_commission
        self.paper = True
        self._quotes: dict[str, Quote] = {}
        self._ensure_account(initial_cash)

    def commission_for(self, symbol: str) -> float:
        """What one leg on `symbol` costs: the exchange's tariff plus the surcharge.

        It is resolved per symbol and not once per broker because a single
        European profile holds Spanish names at 4,11 and the rest at 3,00, so
        one number for the whole portfolio could not be right for both.
        """
        return fees.standard_commission(symbol) + self.extra_commission

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
        """Symbols with an open position. Whoever is going to call `set_quotes`
        needs to know which assets prices have to be brought for."""
        return {str(row["symbol"]) for row in self._positions()}

    # -- Precios del ciclo -------------------------------------------------

    def set_quotes(self, quotes: dict[str, Quote]) -> None:
        """The cycle injects this pass's prices here.

        Without this the simulator cannot value or execute: it has no data source
        of its own, on purpose, so that it uses exactly the same prices the
        analyst saw.
        """
        self._quotes = dict(quotes)

    def roll_session(self, session: str | None) -> None:
        """Marks the beginning of a new session.

        It pins `last_equity` to the equity the previous session closed at, which
        is the reference for the daily P&L and for the kill switch. If several
        cycles run on the same day the reference does not move: otherwise the
        kill switch would reset on every pass and stop protecting anything.
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
        """The simulator executes whenever it has prices.

        It models no hours: if there is a new bar, there is a session to trade.
        A real broker would consult the market clock here.
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
            # No leverage: buying power is the cash.
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

        # Slippage works against you: buying pays a little more.
        price = quote.fill_price * (1 + self.slippage_bps / 10_000.0)
        commission = self.commission_for(symbol)
        cost = price * whole_qty + commission

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
            # Should not happen: the Risk Manager refuses to enlarge positions. It
            # is supported anyway so the ledger never ends up wrong.
            old = existing[0]
            old_qty, old_price = float(old["qty"]), float(old["avg_entry_price"])
            new_qty = old_qty + whole_qty
            new_avg = (old_qty * old_price + whole_qty * price) / new_qty
            # The commissions accumulate: two buys paid two of them, and the
            # sale has to give back both.
            paid = float(old["entry_commission"]) + commission
            self.db.execute(
                "update sim_positions set qty = ?, avg_entry_price = ?, "
                "entry_commission = ? where id = ?",
                (new_qty, new_avg, paid, old["id"]),
            )
        else:
            self.db.execute(
                "insert into sim_positions "
                "(id, account_id, symbol, qty, avg_entry_price, entry_commission, "
                " opened_at) values (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), self.account_id, symbol, float(whole_qty),
                 price, commission, _now()),
            )

        self.db.execute(
            "update sim_accounts set cash = ?, updated_at = ? where id = ?",
            (cash - cost, _now(), self.account_id),
        )
        self._record_fill(symbol, "buy", whole_qty, price, quote.basis, commission)

        log.info(
            "[SIM] COMPRA %s: %d a %.4f (%s, deslizamiento %.0f pb, "
            "comision %.2f). Efectivo: %.2f",
            symbol, whole_qty, price, quote.basis, self.slippage_bps,
            commission, cash - cost,
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
        commission = self.commission_for(symbol)
        proceeds = price * whole_qty - commission
        entry = float(position["avg_entry_price"])

        # The share of the opening commission belonging to what is being sold.
        # Prorated by quantity so a partial sale carries its part and leaves the
        # rest with the remainder: charging it whole on the first partial sale
        # would make that trade look worse and the last one look free.
        paid_on_entry = float(position["entry_commission"])
        entry_share = paid_on_entry * (whole_qty / held)

        # Both legs are subtracted, which is the whole point of storing the
        # first one: cash was already right --the buy took it out-- but the
        # realized P&L only knew about the sale, so every closed trade reported
        # exactly one commission more than it made.
        realized = (price - entry) * whole_qty - commission - entry_share

        remaining = held - whole_qty
        if remaining <= 1e-9:
            self.db.execute("delete from sim_positions where id = ?", (position["id"],))
        else:
            self.db.execute(
                "update sim_positions set qty = ?, entry_commission = ? where id = ?",
                (remaining, paid_on_entry - entry_share, position["id"]),
            )

        row = self._account_row()
        new_cash = float(row["cash"]) + proceeds
        self.db.execute(
            "update sim_accounts set cash = ?, updated_at = ? where id = ?",
            (new_cash, _now(), self.account_id),
        )
        self._record_fill(
            symbol, "sell", whole_qty, price, quote.basis, commission, realized=realized
        )

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
        commission: float, realized: float | None = None,
    ) -> None:
        """Records the fill with the commission **of this leg**.

        Not the round trip's: `sim_fills` is a ledger of executions, and each
        one paid what it paid. The sale's `realized_pnl` is the one that already
        nets both legs.
        """
        self.db.execute(
            "insert into sim_fills "
            "(account_id, symbol, side, qty, price, basis, slippage_bps, commission, "
            " realized_pnl, filled_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.account_id, symbol, side, float(qty), price, basis,
             self.slippage_bps, commission, realized, _now()),
        )
