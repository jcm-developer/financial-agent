"""Risk Manager: la barrera determinista entre el LLM y el broker.

Este modulo es la pieza mas importante del sistema y la unica que decide
cuanto dinero se pone en riesgo. Reglas de diseno:

  1. Cero llamadas de red y cero IA. Solo aritmetica y comparaciones.
  2. Cierra por defecto: cualquier dato que falte o no cuadre es un rechazo,
     nunca una aproximacion.
  3. Todo rechazo nombra la regla que lo provoco, para poder agregarlo despues
     en SQL y ver contra que limite choca el modelo.

El LLM solo aporta direccion y conviccion. El tamano de la posicion, el stop y
el veredicto final salen de aqui.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import RiskLimits
from .models import AccountState, BrokerPosition, ExitSignal, Proposal, RiskVerdict


@dataclass(frozen=True)
class KillSwitch:
    triggered: bool
    reason: str
    day_pnl_pct: float


class RiskManager:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    # -- Nivel cartera -----------------------------------------------------

    def check_kill_switch(self, account: AccountState) -> KillSwitch:
        """Detiene el ciclo completo si la perdida del dia excede el limite.

        Se evalua antes de analizar nada: si la cartera ya sangra, el agente no
        abre posiciones nuevas por muy convincente que sea la tesis.
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
        """Cierres que se ejecutan sin consultar al LLM.

        `tracked` mapea simbolo -> {'stop_price': x, 'target_price': y} segun lo
        registrado en la base de datos al abrir la posicion. Un stop alcanzado no se
        negocia: es la unica proteccion real del presupuesto.
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
        """Convierte una propuesta de compra en una cantidad concreta, o la
        rechaza. Las comprobaciones van de la mas barata a la mas costosa."""
        limits = self.limits
        symbol = proposal.symbol
        price = proposal.reference_price

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

        # --- Stop: lo fija el ATR, no el modelo ---------------------------
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
            # El modelo pide mas holgura que el ATR: se la damos, porque implica
            # una posicion mas pequena. Nunca al contrario.
            stop = llm_stop
            stop_source = "llm_wider"

        risk_per_share = price - stop
        if risk_per_share <= 0:
            return _reject("non_positive_risk", "La distancia hasta el stop no es positiva.")

        # --- Objetivo y ratio beneficio/riesgo ---------------------------
        target = proposal.suggested_target
        target_source = "llm"
        if target is None or target <= price:
            target = price + risk_per_share * limits.min_reward_risk
            target_source = "derived"

        reward_risk = (target - price) / risk_per_share
        if reward_risk < limits.min_reward_risk:
            return _reject(
                "min_reward_risk",
                f"Ratio beneficio/riesgo {reward_risk:.2f} por debajo del minimo "
                f"{limits.min_reward_risk:.2f}.",
                details={"target": round(target, 4), "stop": round(stop, 4)},
            )

        # --- Dimensionado por riesgo -------------------------------------
        # Se arriesga un % fijo del equity hasta el stop. El resultado es que
        # los activos volatiles reciben posiciones mas pequenas, automaticamente.
        risk_budget = account.equity * limits.risk_per_trade_pct / 100.0
        qty = math.floor(risk_budget / risk_per_share)
        binding_rule = "risk_per_trade"

        # Tope por tamano de posicion.
        max_position_qty = math.floor(
            (account.equity * limits.max_position_pct / 100.0) / price
        )
        if max_position_qty < qty:
            qty, binding_rule = max_position_qty, "max_position_pct"

        # Tope por exposicion total de la cartera.
        exposure_cap = account.equity * limits.max_total_exposure_pct / 100.0
        remaining_exposure = exposure_cap - account.positions_value
        if remaining_exposure <= 0:
            return _reject(
                "max_total_exposure_pct",
                f"Exposicion actual ${account.positions_value:,.2f} ya cubre el limite de "
                f"${exposure_cap:,.2f}.",
            )
        exposure_qty = math.floor(remaining_exposure / price)
        if exposure_qty < qty:
            qty, binding_rule = exposure_qty, "max_total_exposure_pct"

        # Tope por cash disponible. Usamos cash, no buying_power: sin apalancamiento.
        cash_qty = math.floor(account.cash / price)
        if cash_qty < qty:
            qty, binding_rule = cash_qty, "insufficient_cash"

        if qty < 1:
            return _reject(
                binding_rule if binding_rule != "risk_per_trade" else "qty_below_one",
                f"El tamano calculado es {qty} acciones (limitado por {binding_rule}); "
                f"a ${price:,.2f} por accion no da para una unidad.",
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
                f"Valor de la orden ${notional:,.2f} por debajo del minimo "
                f"${limits.min_order_notional:,.2f}.",
                details={"qty": qty, "price": round(price, 4)},
            )

        return RiskVerdict(
            approved=True,
            reason=(
                f"Aprobadas {qty} acciones de {symbol} por ${notional:,.2f} "
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
                "risk_amount": round(qty * risk_per_share, 2),
                "reward_risk": round(reward_risk, 2),
                "stop_source": stop_source,
                "target_source": target_source,
                "binding_rule": binding_rule,
                "pct_of_equity": round(notional / account.equity * 100, 2),
                "conviction": proposal.conviction,
            },
        )


def _reject(rule: str, reason: str, details: dict | None = None) -> RiskVerdict:
    return RiskVerdict(
        approved=False, reason=reason, rule=rule, details=details or {}
    )
