"""Risk Manager: the deterministic barrier between the LLM and the broker.

This module is the most important piece of the system and the only one that
decides how much money is put at risk. Design rules:

  1. Zero network calls and zero AI. Only arithmetic and comparisons.
  2. Closed by default: any datum that is missing or does not add up is a
     rejection, never an approximation.
  3. Every rejection names the rule that caused it, so it can be aggregated
     later in SQL to see which limit the model keeps hitting.

The LLM only contributes direction and conviction. The position size, the stop
and the final verdict come from here.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from . import fees
from .config import RiskLimits
from .models import AccountState, BrokerPosition, ExitSignal, Proposal, RiskVerdict


#: What fraction of the allowance a proposal at exactly `min_conviction` gets
#: (F9.10). Conviction interpolates between this and 1,0 at 100.
#:
#: **It is not zero, and that is the whole reason it is a constant with a
#: comment.** At zero, a proposal that just cleared the conviction gate would be
#: sized at zero shares and rejected, so the gate would stop meaning "this is
#: worth trading" and start meaning "this is worth trading, but not really". Half
#: keeps the gate meaning what it says: everything that passes gets a position,
#: and conviction decides how much of the allowance it takes.
CONVICTION_FLOOR = 0.5


@dataclass(frozen=True)
class KillSwitch:
    triggered: bool
    reason: str
    day_pnl_pct: float


class RiskManager:
    def __init__(
        self,
        limits: RiskLimits,
        currency_symbol: str = "",
        commission_for: Callable[[str], float] | None = None,
    ) -> None:
        self.limits = limits
        #: What one leg costs, injected by whoever knows: `cycle.py` passes the
        #: broker's own method, which adds the profile's surcharge on top of the
        #: bank's tariff.
        #:
        #: **The default is the bank's standard tariff and not zero** (F9.9).
        #: Zero would be the one silent lie this module cannot afford: it would
        #: approve orders the cash cannot pay for and clear reward/risk ratios
        #: that friction turns negative, which is the bug this parameter exists
        #: to fix. `fees.standard_commission` raises on an exchange nobody has
        #: priced, so the failure is loud rather than free.
        self._commission_for = commission_for or fees.standard_commission
        #: What the profile's market prices in, for the text of the verdict.
        #:
        #: The verdict's `reason` is not a log line: it is stored in
        #: `risk_events.reason` and the Riesgo screen prints it verbatim, so it
        #: is screen text and the currency has to travel with the figure (FE.8).
        #: It used to write `$` as a literal, which meant a European experiment
        #: read "Aprobadas 9 acciones de ALV.DE por $3,949.20" — an amount you
        #: could compare with another book's as if they were the same unit.
        #:
        #: **The default is the empty string and not `$`**: a manager built
        #: without one writes a bare figure, which says nothing, instead of
        #: saying something false.
        self.currency_symbol = currency_symbol

    # -- Nivel cartera -----------------------------------------------------

    def check_kill_switch(self, account: AccountState) -> KillSwitch:
        """Halts the whole cycle if the day's loss exceeds the limit.

        It is evaluated before anything is analysed: if the book is already
        bleeding, the agent opens no new positions however convincing the
        thesis may be.
        """
        pnl_pct = account.day_pnl_pct
        if pnl_pct <= -self.limits.max_daily_loss_pct:
            return KillSwitch(
                triggered=True,
                reason=(
                    f"Perdida diaria {pnl_pct:.2f}% alcanza el limite de "
                    f"-{self.limits.max_daily_loss_pct:.2f}%. No se abren posiciones nuevas."
                ),
                day_pnl_pct=pnl_pct,
            )
        return KillSwitch(False, f"Perdida diaria {pnl_pct:.2f}% dentro del limite.", pnl_pct)

    # -- Salidas obligatorias ---------------------------------------------

    def mandatory_exits(
        self,
        positions: dict[str, BrokerPosition],
        tracked: dict[str, dict[str, float | None]],
    ) -> list[ExitSignal]:
        """Closes that execute without consulting the LLM.

        `tracked` maps symbol -> {'stop_price': x, 'target_price': y} as recorded
        in the database when the position was opened. A stop that has been hit is
        not negotiable: it is the budget's only real protection.
        """
        signals: list[ExitSignal] = []
        for symbol, position in positions.items():
            levels = tracked.get(symbol) or {}
            stop = levels.get("stop_price")
            target = levels.get("target_price")
            price = position.current_price

            if stop is not None and price <= stop:
                signals.append(
                    ExitSignal(
                        symbol=symbol,
                        qty=position.qty,
                        reason=f"Precio {price:.2f} ha perforado el stop {stop:.2f}.",
                        rule="stop_loss_hit",
                        forced=True,
                        price=price,
                    )
                )
                continue

            if target is not None and price >= target:
                signals.append(
                    ExitSignal(
                        symbol=symbol,
                        qty=position.qty,
                        reason=f"Precio {price:.2f} ha alcanzado el objetivo {target:.2f}.",
                        rule="take_profit_hit",
                        forced=True,
                        price=price,
                    )
                )
        return signals

    # -- Entradas ----------------------------------------------------------

    def evaluate_entry(
        self,
        proposal: Proposal,
        account: AccountState,
        atr: float | None,
    ) -> RiskVerdict:
        """Turns a buy proposal into a concrete quantity, or rejects it. The
        checks run from the cheapest to the most expensive."""
        limits = self.limits
        symbol = proposal.symbol
        price = proposal.reference_price
        money = self.currency_symbol

        if proposal.action != "buy":
            return _reject("action_not_buy", f"La accion propuesta es {proposal.action!r}, no una compra.")

        if proposal.conviction < limits.min_conviction:
            return _reject(
                "min_conviction",
                f"Conviccion {proposal.conviction} por debajo del minimo {limits.min_conviction}.",
            )

        if price <= 0:
            return _reject("invalid_price", f"Precio de referencia invalido: {price}.")

        if symbol in account.open_symbols:
            return _reject(
                "already_open",
                f"Ya hay una posicion abierta en {symbol}; no se promedia a la baja ni se amplia.",
            )

        if len(account.positions) >= limits.max_open_positions:
            return _reject(
                "max_open_positions",
                f"Ya hay {len(account.positions)} posiciones abiertas, el maximo es "
                f"{limits.max_open_positions}.",
            )

        if account.equity <= 0:
            return _reject("no_equity", "El equity de la cuenta es cero o negativo.")

        # --- Stop: set by the ATR, not by the model ------------------------
        if atr is None or atr <= 0:
            return _reject(
                "atr_unavailable",
                "Sin ATR no se puede dimensionar la posicion ni situar el stop.",
            )

        atr_stop = price - atr * limits.stop_atr_multiple
        if atr_stop <= 0:
            return _reject(
                "stop_below_zero",
                f"El stop por ATR ({atr_stop:.2f}) cae por debajo de cero; activo demasiado volatil.",
            )

        stop = atr_stop
        stop_source = "atr"
        llm_stop = proposal.suggested_stop
        if llm_stop is not None and 0 < llm_stop < price and llm_stop < atr_stop:
            # The model asks for more room than the ATR: it is granted, because
            # it implies a smaller position. Never the other way round.
            stop = llm_stop
            stop_source = "llm_wider"

        risk_per_share = price - stop
        if risk_per_share <= 0:
            return _reject("non_positive_risk", "La distancia hasta el stop no es positiva.")

        # --- Coste de operar ----------------------------------------------
        # Both legs, because a position that is opened gets closed: the entry is
        # charged now and the exit is charged whether it leaves by the stop, by
        # the target or by the analyst. Sizing it as one leg would flatter every
        # ratio below by half of the friction.
        commission = self._commission_for(symbol)
        round_trip = commission * 2

        # --- Risk-based sizing --------------------------------------------
        # A fixed % of equity is risked down to the stop. The result is that
        # volatile assets get smaller positions, automatically.
        risk_budget = account.equity * limits.risk_per_trade_pct / 100.0
        qty = math.floor(risk_budget / risk_per_share)
        binding_rule = "risk_per_trade"

        # Cap by position size.
        max_position_qty = math.floor(
            (account.equity * limits.max_position_pct / 100.0) / price
        )
        if max_position_qty < qty:
            qty, binding_rule = max_position_qty, "max_position_pct"

        # Cap by the book's total exposure.
        exposure_cap = account.equity * limits.max_total_exposure_pct / 100.0
        remaining_exposure = exposure_cap - account.positions_value
        if remaining_exposure <= 0:
            return _reject(
                "max_total_exposure_pct",
                f"Exposicion actual {money}{account.positions_value:,.2f} ya cubre el "
                f"limite de {money}{exposure_cap:,.2f}.",
            )
        exposure_qty = math.floor(remaining_exposure / price)
        if exposure_qty < qty:
            qty, binding_rule = exposure_qty, "max_total_exposure_pct"

        # Cap by available cash. Cash and not buying_power: no leverage.
        #
        # **The commission is reserved before dividing** (F9.9): it comes out of
        # this same cash, `sim_broker.buy_market` charges `qty × precio +
        # comision` and raises if it does not fit, so `floor(cash / price)` could
        # approve an order the broker then refused — a rejection appearing at
        # execution, after the Risk Manager had already said yes.
        #
        # It is a reservation and not a guarantee, and the difference is worth
        # knowing: the fill happens at the **next bar's open**, which is unknown
        # here, so a gap up can still break it. That is why the broker keeps its
        # own check instead of trusting this one.
        affordable = account.cash - commission
        cash_qty = math.floor(affordable / price) if affordable > 0 else 0
        if cash_qty < qty:
            qty, binding_rule = cash_qty, "insufficient_cash"

        # --- El peso que pidio el analista, como un tope mas ------------------
        #
        # **El tope del perfil era el valor por defecto, y eso es lo que arregla
        # esto** (F9.13). Con 3 % de riesgo y stops a 1,2× ATR el presupuesto de
        # riesgo no ataba nunca, así que toda posición aprobada aterrizaba en el
        # techo: un 40 % que significa «nunca más de esto» se estaba comportando
        # como «esto». Lo que faltaba era alguien decidiendo cuánto de esa holgura
        # merece cada idea, y quien tiene la tesis delante es el analista.
        #
        # Se aplica como `min` contra lo ya calculado, así que **solo puede pedir
        # menos**: la premisa del proyecto no se toca, porque el modelo no puede
        # ampliar un límite ni ejecutar nada, y un peso absurdo lo recorta el techo
        # de arriba. Es la misma forma que tiene el stop, que el modelo solo puede
        # ensanchar.
        weight = proposal.suggested_weight_pct
        if weight is not None:
            # Contra el techo del perfil y no contra el 100 %: pedir un 80 % en un
            # perfil que permite 40 no es una petición, es no haber leído el límite.
            allowed = min(weight, limits.max_position_pct)
            weight_qty = math.floor((account.equity * allowed / 100.0) / price)
            if weight_qty < qty:
                qty, binding_rule = weight_qty, "suggested_weight"

        # --- La conviccion modula, dentro de lo que los topes permiten --------
        #
        # **It scales the result of every cap and not the risk budget** (F9.10),
        # and the order matters. Measured on this experiment, the risk budget was
        # never what bound: 300 EUR of budget against 43-94 EUR of risk actually
        # taken, because `max_position_pct` cut in far earlier. So a conviction
        # factor applied to the budget would have changed nothing at all — the
        # same inertia the risk slider already had.
        #
        # Applied here it can only ever shrink an already-approved size, so no
        # limit can be crossed by raising conviction: the caps say "never more
        # than this" and conviction says "how much of that this idea deserves".
        #
        # ⚠️ **The model is not told about this**, and it is deliberate. Telling it
        # that conviction moves money turns conviction into a lever instead of an
        # estimate, and the calibration of F5.7 —is a 70 really right 7 times out
        # of 10?— is measured on exactly that number. The prompt's promise still
        # holds literally: it does not decide the size.
        span = 100.0 - limits.min_conviction
        reach = (proposal.conviction - limits.min_conviction) / span if span > 0 else 1.0
        conviction_factor = CONVICTION_FLOOR + (1.0 - CONVICTION_FLOOR) * min(1.0, reach)
        # **Solo cuando el analista no pidió peso** (F9.13). Con las dos cosas a la
        # vez se contaría dos veces la misma opinión: un peso del 10 % recortado
        # además a la mitad por una convicción de 60 da un 5 % que nadie decidió.
        # El peso es la respuesta explícita a «cuánto», y esto es lo que se hace
        # cuando no la hay.
        if weight is None:
            scaled = math.floor(qty * conviction_factor)
            if scaled < qty:
                qty, binding_rule = scaled, "conviction"

        if qty < 1:
            return _reject(
                binding_rule if binding_rule != "risk_per_trade" else "qty_below_one",
                f"El tamano calculado es {qty} acciones (limitado por {binding_rule}); "
                f"a {money}{price:,.2f} por accion no da para una unidad.",
                details={
                    "risk_budget": round(risk_budget, 2),
                    "risk_per_share": round(risk_per_share, 4),
                    "cash": round(account.cash, 2),
                },
            )

        notional = qty * price
        if notional < limits.min_order_notional:
            return _reject(
                "min_order_notional",
                f"Valor de la orden {money}{notional:,.2f} por debajo del minimo "
                f"{money}{limits.min_order_notional:,.2f}.",
                details={"qty": qty, "price": round(price, 4)},
            )

        # --- Objetivo y ratio beneficio/riesgo, con la friccion dentro ------
        #
        # **This runs after the sizing and not before, and the order is the whole
        # point** (F9.9). The commission is a fixed amount per order, not a cost
        # per share, so the ratio it produces depends on how many shares are
        # bought: the same trade is ruinous at 100 EUR and fine at 2.000 EUR. A
        # ratio computed before the quantity exists cannot know that, which is
        # why the check used to pass trades that were losers by construction —
        # measured on this experiment, a paper 1,02 was really 0,72.
        target = proposal.suggested_target
        target_source = "llm"
        if target is None or target <= price:
            target = _target_for_ratio(price, stop, qty, round_trip, limits.min_reward_risk)
            target_source = "derived"

        gain = (target - price) * qty - round_trip
        loss = risk_per_share * qty + round_trip
        reward_risk = gain / loss
        if reward_risk < limits.min_reward_risk:
            return _reject(
                "min_reward_risk",
                f"Ratio beneficio/riesgo {reward_risk:.2f} por debajo del minimo "
                f"{limits.min_reward_risk:.2f} contando {money}{round_trip:,.2f} de "
                f"comisiones de ida y vuelta.",
                details={
                    "target": round(target, 4),
                    "stop": round(stop, 4),
                    "qty": qty,
                    "round_trip_commission": round(round_trip, 2),
                    # What it would have been without friction, so a rejection can
                    # be told from one caused by the thesis itself.
                    "reward_risk_gross": round((target - price) / risk_per_share, 2),
                },
            )

        return RiskVerdict(
            approved=True,
            reason=(
                f"Aprobadas {qty} acciones de {symbol} por {money}{notional:,.2f} "
                f"(limita: {binding_rule}). Stop {stop:.2f} ({stop_source}), "
                f"objetivo {target:.2f} ({target_source}), R/R {reward_risk:.2f}."
            ),
            rule=binding_rule,
            qty=float(qty),
            notional=round(notional, 2),
            stop_price=round(stop, 4),
            target_price=round(target, 4),
            details={
                "risk_budget": round(risk_budget, 2),
                "risk_per_share": round(risk_per_share, 4),
                # What the stop would cost including the commissions, which is
                # what would actually be booked.
                "risk_amount": round(qty * risk_per_share + round_trip, 2),
                "reward_risk": round(reward_risk, 2),
                "round_trip_commission": round(round_trip, 2),
                # So the Riesgo screen can tell a position that was cut by a limit
                # from one the analyst simply did not believe in much.
                "conviction_factor": round(conviction_factor, 3),
                # What the analyst asked for, unclamped, so a model that keeps
                # asking for more than it may have is visible in the record
                # instead of only in the resulting quantity.
                "suggested_weight_pct": proposal.suggested_weight_pct,
                "weight_pct_applied": round(notional / account.equity * 100, 2),
                "stop_source": stop_source,
                "target_source": target_source,
                "binding_rule": binding_rule,
                "pct_of_equity": round(notional / account.equity * 100, 2),
                "conviction": proposal.conviction,
            },
        )


def _target_for_ratio(
    price: float, stop: float, qty: int, round_trip: float, minimum: float
) -> float:
    """The target a proposal with none of its own gets, friction included.

    The old derivation was `price + risk_per_share * min_reward_risk`, which
    produced exactly the minimum ratio **before** commissions. Left as it was, it
    would now land just under the net check and **every proposal without a target
    would be rejected** — a change of behaviour disguised as a rounding error, and
    the analyst leaves the target out often.

    So the ratio is solved for the target instead of the target being guessed:
    from `((t − p)·q − rt) / ((p − s)·q + rt) = m` follows
    `t = p + (m·((p − s)·q + rt) + rt) / q`. It gives back the old formula exactly
    when `rt` is zero, which is the American case.

    @param price: Reference price.
    @param stop: Stop level, below the price.
    @param qty: Shares being bought, which is what turns a fixed cost into a
        per-share one.
    @param round_trip: Both legs' commission.
    @param minimum: The net ratio the target has to reach.
    @return: The target price.
    """
    loss = (price - stop) * qty + round_trip
    return price + (minimum * loss + round_trip) / qty


def _reject(rule: str, reason: str, details: dict | None = None) -> RiskVerdict:
    return RiskVerdict(
        approved=False, reason=reason, rule=rule, details=details or {}
    )
