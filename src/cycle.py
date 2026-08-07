"""Orquestacion de un ciclo del agente.

Orden deliberado de las fases:

  1. Reconciliar con el broker      -> partir de la realidad, no del registro.
  2. Datos de mercado               -> una sola peticion para todo el universo.
  3. Kill switch de perdida diaria  -> si salta, no se abre nada nuevo.
  4. Salidas obligatorias           -> stop/objetivo alcanzado, sin consultar al LLM.
  5. Revision de salidas por el LLM -> tesis degradada.
  6. Entradas                       -> analisis, filtro de riesgo, ejecucion.
  7. Curva de capital y cierre      -> siempre, incluso si algo fallo.

Las salidas van antes que las entradas por una razon practica: liberan cash y
huecos de posicion que las entradas de este mismo ciclo pueden aprovechar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import market_calendar
from .analyst import Analyst
from .broker import Broker, BrokerError
from .config import Settings
from .db import Database, DatabaseError
from .llm import LLMClient
from .market_data import MarketDataError, build_market_data
from .models import AccountState, ExitSignal, MarketSnapshot, Proposal
from .risk import RiskManager
from .sim_broker import Quote, SimBroker

log = logging.getLogger(__name__)

# Minutos tras los que un ciclo en 'running' se da por muerto y deja de bloquear.
# Un ciclo normal con el embudo tarda ~20; 90 da margen de sobra sin dejar el
# agente parado toda la noche por un contenedor que murio.
STALE_CYCLE_MINUTES = 90


@dataclass
class CycleReport:
    """Resumen de lo ocurrido, para el log final y para los tests."""

    cycle_id: str | None = None
    status: str = "completed"
    market_open: bool = False
    equity_start: float = 0.0
    equity_end: float = 0.0
    analyzed: int = 0
    proposals_buy: int = 0
    approved: int = 0
    rejected: int = 0
    orders_submitted: int = 0
    exits_forced: int = 0
    exits_discretionary: int = 0
    halted_reason: str | None = None
    screened: str | None = None
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.status == "skipped":
            return f"Ciclo omitido. {self.halted_reason or ''}".strip()
        lines = [
            f"Estado del ciclo: {self.status}",
            f"Mercado abierto: {'si' if self.market_open else 'no'}",
            f"Equity: ${self.equity_start:,.2f} -> ${self.equity_end:,.2f}",
        ]
        if self.screened:
            lines.append(f"Cribado: {self.screened}")
        lines += [
            f"Analizados: {self.analyzed}  propuestas de compra: {self.proposals_buy}",
            f"Riesgo: {self.approved} aprobadas / {self.rejected} rechazadas",
            f"Ordenes enviadas: {self.orders_submitted}",
            f"Salidas: {self.exits_forced} forzadas / "
            f"{self.exits_discretionary} discrecionales",
        ]
        if self.halted_reason:
            lines.append(f"KILL SWITCH: {self.halted_reason}")
        for error in self.errors:
            lines.append(f"Error: {error}")
        return "\n".join(lines)


class TradingCycle:
    def __init__(
        self,
        *,
        settings: Settings,
        broker: Broker,
        market_data: MarketData,
        database: Database,
        analyst: Analyst,
        risk_manager: RiskManager,
        portfolio_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.market_data = market_data
        self.db = database
        self.analyst = analyst
        self.risk = risk_manager
        self.portfolio_id = portfolio_id

    # ------------------------------------------------------------------

    @classmethod
    def build(cls, settings: Settings, llm: LLMClient) -> TradingCycle:
        """Monta el ciclo con el broker y el proveedor de datos configurados.

        El broker simulado necesita la base de datos y el id de cartera, asi que
        se crean primero.
        """
        database = Database(path=settings.db_path)
        portfolio_id = database.ensure_portfolio(
            name=settings.portfolio_name,
            mode=settings.mode,
            initial_budget=settings.initial_budget,
        )

        if settings.broker == "sim":
            broker = SimBroker(
                database=database,
                portfolio_id=portfolio_id,
                initial_cash=settings.initial_budget,
                slippage_bps=settings.sim_slippage_bps,
                commission_per_order=settings.sim_commission,
            )
        else:
            broker = Broker(
                api_key=settings.alpaca_api_key,
                secret_key=settings.alpaca_secret_key,
                paper=settings.alpaca_paper,
            )

        return cls(
            settings=settings,
            broker=broker,
            market_data=build_market_data(settings, database),
            database=database,
            analyst=Analyst(llm, interval=settings.bar_interval),
            risk_manager=RiskManager(settings.risk),
            portfolio_id=portfolio_id,
        )

    # ------------------------------------------------------------------

    def run(self) -> CycleReport:
        report = CycleReport()
        settings = self.settings

        # El calendario se consulta antes de gastar nada: sin barras nuevas,
        # analizar seria repetir las decisiones del ciclo anterior gastando cuota.
        # Con barras diarias el momento natural es justo despues del cierre, asi
        # que la comprobacion no es "esta abierto" sino "hay sesion hoy".
        allowed, reason = market_calendar.should_run(settings.bar_interval)
        if settings.skip_when_market_closed and not allowed:
            log.info("Ciclo omitido: %s", reason)
            report.status = "skipped"
            report.halted_reason = reason
            return report

        portfolio_id = self.portfolio_id or self.db.ensure_portfolio(
            name=settings.portfolio_name,
            mode=settings.mode,
            initial_budget=settings.initial_budget,
        )

        # Un solo ciclo por cartera a la vez. Dos en paralelo se pisan el efectivo
        # y las posiciones, y dejan un historico con decisiones duplicadas que ya
        # no se puede interpretar. Pasa con facilidad: basta lanzar uno a mano
        # mientras el planificador arranca el suyo.
        blocked = self._check_no_other_cycle_running(portfolio_id)
        if blocked is not None:
            report.status = "skipped"
            report.halted_reason = blocked
            log.warning("Ciclo no iniciado: %s", blocked)
            return report

        # Las posiciones abiertas son obligatorias: necesitan revision aunque el
        # screener no las seleccione. El proveedor anade sus propios candidatos.
        required = tuple(sorted(self.db.get_open_positions(portfolio_id)))

        # Los datos van ANTES de leer la cuenta: el broker simulado no tiene
        # fuente de precios propia, asi que sin ellos no puede valorar la cartera
        # ni ejecutar. Con Alpaca el orden es indiferente.
        snapshots = self.market_data.fetch_snapshots(required)
        symbols = tuple(sorted(snapshots))
        self._prime_broker(snapshots)

        account = self.broker.get_account_state()
        market_open = self.broker.is_market_open()
        report.market_open = market_open
        report.equity_start = account.equity
        report.equity_end = account.equity
        report.analyzed = len(snapshots)

        self._warn_if_budget_exceeds_account(account)

        cycle_id = self.db.start_cycle(
            portfolio_id=portfolio_id,
            equity_start=account.equity,
            cash_start=account.cash,
            market_open=market_open,
            symbols=list(symbols),
            llm_model=settings.llm_model,
        )
        report.cycle_id = cycle_id
        log.info("Ciclo %s iniciado. %s", cycle_id, settings.describe())
        log.info("Calendario: %s", market_calendar.describe())

        describe_selection = getattr(self.market_data, "describe_selection", None)
        if callable(describe_selection):
            report.screened = describe_selection()
            log.info("Screener: %s", report.screened)

        try:
            self._run_phases(
                report, portfolio_id, cycle_id, account, symbols, market_open, snapshots
            )
        except (BrokerError, MarketDataError, DatabaseError) as exc:
            report.status = "failed"
            report.errors.append(str(exc))
            log.exception("El ciclo fallo: %s", exc)
        except Exception as exc:  # noqa: BLE001 - queremos cerrar el ciclo siempre
            report.status = "failed"
            report.errors.append(f"Error inesperado: {exc}")
            log.exception("Error inesperado en el ciclo.")

        # Cierre del ciclo: se ejecuta pase lo que pase, para no dejar filas
        # colgadas en estado 'running'.
        try:
            final_account = self.broker.get_account_state()
            report.equity_end = final_account.equity
            self.db.save_equity_snapshot(
                portfolio_id=portfolio_id,
                cycle_id=cycle_id,
                equity=final_account.equity,
                cash=final_account.cash,
                positions_value=final_account.positions_value,
                open_positions=len(final_account.positions),
                day_pnl=final_account.day_pnl,
                day_pnl_pct=final_account.day_pnl_pct,
            )
        except (BrokerError, DatabaseError) as exc:
            report.errors.append(f"No se pudo guardar la curva de capital: {exc}")
            log.warning("No se pudo guardar la curva de capital: %s", exc)

        try:
            self.db.finish_cycle(
                cycle_id,
                status=report.status,
                equity_end=report.equity_end,
                error="; ".join(report.errors) if report.errors else None,
            )
        except DatabaseError as exc:
            log.error("No se pudo marcar el ciclo como finalizado: %s", exc)

        return report

    # ------------------------------------------------------------------

    def _run_phases(
        self,
        report: CycleReport,
        portfolio_id: str,
        cycle_id: str,
        account: AccountState,
        symbols: tuple[str, ...],
        market_open: bool,
        snapshots: dict[str, MarketSnapshot],
    ) -> None:
        broker_positions = {p.symbol: p for p in account.positions}

        # --- 1. Reconciliacion -------------------------------------------
        reconcile_report = self.db.reconcile(
            portfolio_id=portfolio_id, broker_positions=broker_positions
        )

        # --- 2. Registro de los datos que vio el analista -----------------
        snapshot_ids: dict[str, int] = {}
        for symbol, snapshot in snapshots.items():
            try:
                snapshot_ids[symbol] = self.db.save_snapshot(
                    cycle_id=cycle_id, snapshot=snapshot
                )
            except DatabaseError as exc:
                log.warning("No se pudo guardar el snapshot de %s: %s", symbol, exc)

        tracked = self.db.get_open_positions(portfolio_id)

        # Las huerfanas adoptadas no tienen stop: se les asigna uno por ATR para
        # que queden protegidas desde este mismo ciclo.
        self._assign_stops_to_orphans(
            reconcile_report.adopted_orphans, snapshots, broker_positions, tracked
        )
        if reconcile_report.adopted_orphans:
            tracked = self.db.get_open_positions(portfolio_id)

        # --- 3. Kill switch ----------------------------------------------
        kill_switch = self.risk.check_kill_switch(account)
        if kill_switch.triggered:
            report.halted_reason = kill_switch.reason
            log.warning("KILL SWITCH activado: %s", kill_switch.reason)
            self.db.save_risk_event(
                cycle_id=cycle_id,
                portfolio_id=portfolio_id,
                symbol=None,
                verdict=_rejection("max_daily_loss_pct", kill_switch.reason),
            )

        # --- 4. Salidas obligatorias -------------------------------------
        levels = {
            symbol: {
                "stop_price": _opt_float(row.get("stop_price")),
                "target_price": _opt_float(row.get("target_price")),
            }
            for symbol, row in tracked.items()
        }
        # Un simbolo que se cierra en este ciclo no se vuelve a abrir en el mismo
        # ciclo: seria comprar y vender el mismo dia sobre la misma tesis, que es
        # churn puro y en una cuenta real cuenta como day trade.
        closed_this_cycle: set[str] = set()

        forced_exits = self.risk.mandatory_exits(broker_positions, levels)
        for signal in forced_exits:
            if self._execute_exit(
                report, portfolio_id, cycle_id, signal, tracked, broker_positions,
                market_open=market_open,
            ):
                report.exits_forced += 1
                broker_positions.pop(signal.symbol, None)
                closed_this_cycle.add(signal.symbol)

        # --- 5. Revision discrecional de salidas -------------------------
        forced_symbols = {s.symbol for s in forced_exits}
        for symbol, position in list(broker_positions.items()):
            if symbol in forced_symbols:
                continue
            snapshot = snapshots.get(symbol)
            row = tracked.get(symbol)
            if snapshot is None or row is None:
                continue

            proposal = self.analyst.evaluate_exit(
                position=position,
                snapshot=snapshot,
                entry_thesis=row.get("thesis"),
                stop_price=_opt_float(row.get("stop_price")),
                target_price=_opt_float(row.get("target_price")),
            )
            if proposal is None:
                continue

            decision_id = self._save_decision(
                cycle_id, portfolio_id, proposal, snapshot_ids.get(symbol)
            )
            self._maybe_raise_stop(row, proposal, position)

            if proposal.action != "sell":
                continue
            if proposal.conviction < self.settings.risk.min_conviction:
                log.info(
                    "%s: venta propuesta con conviccion %d, por debajo del minimo %d; "
                    "se mantiene la posicion.",
                    symbol, proposal.conviction, self.settings.risk.min_conviction,
                )
                continue

            signal = ExitSignal(
                symbol=symbol,
                qty=position.qty,
                reason=proposal.thesis or "Tesis degradada segun el analista.",
                rule="llm_exit",
                forced=False,
                price=position.current_price,
            )
            if self._execute_exit(
                report, portfolio_id, cycle_id, signal, tracked, broker_positions,
                market_open=market_open, decision_id=decision_id,
            ):
                report.exits_discretionary += 1
                broker_positions.pop(symbol, None)
                closed_this_cycle.add(symbol)

        # --- 6. Entradas --------------------------------------------------
        if kill_switch.triggered:
            log.info("No se evaluan entradas: el kill switch esta activo.")
            report.status = "halted"
            return

        # Estado refrescado: las ventas de este ciclo liberaron cash y huecos.
        account = self.broker.get_account_state()
        report.equity_end = account.equity

        # Todo lo que el proveedor haya devuelto y no tengamos ya es candidato: con
        # embudo son los seleccionados por el screener, sin embudo la watchlist.
        candidates = [
            symbol for symbol in symbols
            if symbol not in account.open_symbols
            and symbol not in closed_this_cycle
        ]
        if closed_this_cycle:
            log.info(
                "Excluidos de entrada por haberse cerrado en este ciclo: %s",
                ", ".join(sorted(closed_this_cycle)),
            )
        log.info("Evaluando %d candidatos a entrada.", len(candidates))

        for symbol in candidates:
            if len(account.positions) >= self.settings.risk.max_open_positions:
                log.info("Limite de posiciones abiertas alcanzado; se detiene la busqueda.")
                break

            snapshot = snapshots[symbol]
            proposal = self.analyst.evaluate_entry(snapshot, account)
            if proposal is None:
                continue

            decision_id = self._save_decision(
                cycle_id, portfolio_id, proposal, snapshot_ids.get(symbol)
            )

            if proposal.action != "buy":
                continue
            report.proposals_buy += 1

            atr = _opt_float(snapshot.indicators.get("atr_14"))
            verdict = self.risk.evaluate_entry(proposal, account, atr)
            risk_event_id = self._save_risk_event(
                cycle_id, portfolio_id, symbol, verdict, decision_id
            )

            if not verdict.approved:
                report.rejected += 1
                log.info("RECHAZADA %s [%s]: %s", symbol, verdict.rule, verdict.reason)
                continue

            report.approved += 1
            log.info("APROBADA %s: %s", symbol, verdict.reason)

            if not self._can_execute(market_open):
                self._record_unexecuted_order(
                    cycle_id, portfolio_id, symbol, "buy", verdict,
                    decision_id, risk_event_id, market_open,
                )
                continue

            if self._execute_entry(
                report, portfolio_id, cycle_id, snapshot, proposal, verdict,
                decision_id, risk_event_id,
            ):
                # Refrescar para que los limites de los siguientes candidatos
                # cuenten con la posicion recien abierta.
                account = self.broker.get_account_state()
                report.equity_end = account.equity

    # ------------------------------------------------------------------
    # Ejecucion
    # ------------------------------------------------------------------

    def _can_execute(self, market_open: bool) -> bool:
        if self.settings.dry_run:
            return False
        return market_open

    def _execute_entry(
        self,
        report: CycleReport,
        portfolio_id: str,
        cycle_id: str,
        snapshot: MarketSnapshot,
        proposal: Proposal,
        verdict,
        decision_id: str | None,
        risk_event_id: str | None,
    ) -> bool:
        symbol = snapshot.symbol
        if not self.broker.is_tradable(symbol):
            self._save_risk_event(
                cycle_id, portfolio_id, symbol,
                _rejection("not_tradable", f"El broker no admite operaciones en {symbol}."),
                decision_id,
            )
            report.rejected += 1
            return False

        try:
            order = self.broker.buy_market(symbol, verdict.qty)
        except BrokerError as exc:
            log.error("Fallo la orden de compra de %s: %s", symbol, exc)
            self._safe_save_order(
                cycle_id=cycle_id, portfolio_id=portfolio_id, symbol=symbol,
                side="buy", qty=verdict.qty, status="failed",
                decision_id=decision_id, risk_event_id=risk_event_id,
                stop_price=verdict.stop_price, target_price=verdict.target_price,
                error=str(exc),
            )
            report.errors.append(f"Compra de {symbol} fallida: {exc}")
            return False

        report.orders_submitted += 1
        entry_price = order.filled_avg_price or snapshot.price

        order_id = self._safe_save_order(
            cycle_id=cycle_id, portfolio_id=portfolio_id, symbol=symbol,
            side="buy", qty=verdict.qty, status=order.status,
            decision_id=decision_id, risk_event_id=risk_event_id,
            broker_order_id=order.broker_order_id,
            filled_qty=order.filled_qty, filled_avg_price=order.filled_avg_price,
            stop_price=verdict.stop_price, target_price=verdict.target_price,
        )

        try:
            self.db.open_position(
                portfolio_id=portfolio_id,
                symbol=symbol,
                qty=verdict.qty,
                entry_price=entry_price,
                stop_price=verdict.stop_price,
                target_price=verdict.target_price,
                thesis=proposal.thesis,
                horizon_days=proposal.horizon_days,
                entry_order_id=order_id,
            )
        except DatabaseError as exc:
            # La orden ya esta enviada: esto no se puede deshacer. Se registra en
            # alto para que la reconciliacion del proximo ciclo la adopte.
            log.error(
                "Orden de %s enviada pero no se pudo registrar la posicion: %s. "
                "La reconciliacion del proximo ciclo la adoptara.", symbol, exc,
            )
            report.errors.append(f"Posicion de {symbol} sin registrar: {exc}")

        log.info(
            "COMPRA %s: %g acciones a ~%.2f, stop %s, objetivo %s",
            symbol, verdict.qty, entry_price,
            _fmt(verdict.stop_price), _fmt(verdict.target_price),
        )
        return True

    def _execute_exit(
        self,
        report: CycleReport,
        portfolio_id: str,
        cycle_id: str,
        signal: ExitSignal,
        tracked: dict[str, dict],
        broker_positions: dict,
        *,
        market_open: bool,
        decision_id: str | None = None,
    ) -> bool:
        symbol = signal.symbol
        risk_event_id = self._save_risk_event(
            cycle_id, portfolio_id, symbol,
            _approval(signal.rule, signal.reason, qty=signal.qty),
            decision_id,
        )

        if not self._can_execute(market_open):
            reason = "DRY_RUN" if self.settings.dry_run else "mercado cerrado"
            log.warning(
                "SALIDA PENDIENTE %s [%s]: %s. No se ejecuta (%s).",
                symbol, signal.rule, signal.reason, reason,
            )
            self._safe_save_order(
                cycle_id=cycle_id, portfolio_id=portfolio_id, symbol=symbol,
                side="sell", qty=signal.qty,
                status="dry_run" if self.settings.dry_run else "canceled",
                decision_id=decision_id, risk_event_id=risk_event_id,
                error=f"No ejecutada: {reason}.",
            )
            return False

        try:
            order = self.broker.close_position(symbol)
        except BrokerError as exc:
            log.error("Fallo el cierre de %s: %s", symbol, exc)
            self._safe_save_order(
                cycle_id=cycle_id, portfolio_id=portfolio_id, symbol=symbol,
                side="sell", qty=signal.qty, status="failed",
                decision_id=decision_id, risk_event_id=risk_event_id,
                error=str(exc),
            )
            report.errors.append(f"Cierre de {symbol} fallido: {exc}")
            return False

        report.orders_submitted += 1
        exit_price = order.filled_avg_price or signal.price
        order_id = self._safe_save_order(
            cycle_id=cycle_id, portfolio_id=portfolio_id, symbol=symbol,
            side="sell", qty=signal.qty, status=order.status,
            decision_id=decision_id, risk_event_id=risk_event_id,
            broker_order_id=order.broker_order_id,
            filled_qty=order.filled_qty, filled_avg_price=order.filled_avg_price,
        )

        row = tracked.get(symbol)
        position = broker_positions.get(symbol)
        if row is not None:
            entry_price = float(row.get("entry_price") or 0.0)
            if position is not None:
                entry_price = position.avg_entry_price or entry_price
            realized = (exit_price - entry_price) * signal.qty
            try:
                self.db.close_position(
                    str(row["id"]),
                    exit_price=exit_price,
                    realized_pnl=realized,
                    exit_reason=f"[{signal.rule}] {signal.reason}",
                    exit_order_id=order_id,
                )
            except DatabaseError as exc:
                log.error("Posicion de %s cerrada en el broker pero no en la base de datos: %s",
                          symbol, exc)
                report.errors.append(f"Cierre de {symbol} sin registrar: {exc}")
            log.info(
                "VENTA %s: %g acciones a ~%.2f, P&L %+.2f USD [%s]",
                symbol, signal.qty, exit_price, realized, signal.rule,
            )
        return True

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def _assign_stops_to_orphans(
        self,
        orphans: list[tuple[str, str]],
        snapshots: dict[str, MarketSnapshot],
        broker_positions: dict,
        tracked: dict[str, dict],
    ) -> None:
        """Coloca un stop por ATR en las posiciones adoptadas sin niveles."""
        for symbol, position_id in orphans:
            snapshot = snapshots.get(symbol)
            position = broker_positions.get(symbol)
            if snapshot is None or position is None:
                log.warning(
                    "%s adoptada sin datos de mercado: queda sin stop. Revisala a mano.",
                    symbol,
                )
                continue
            atr = _opt_float(snapshot.indicators.get("atr_14"))
            if not atr:
                continue
            stop = snapshot.price - atr * self.settings.risk.stop_atr_multiple
            target = snapshot.price + atr * self.settings.risk.stop_atr_multiple * \
                self.settings.risk.min_reward_risk
            if stop <= 0:
                continue
            try:
                self.db.update_position_levels(
                    position_id, stop_price=round(stop, 4), target_price=round(target, 4)
                )
                log.info("%s adoptada: stop asignado en %.2f por ATR.", symbol, stop)
            except DatabaseError as exc:
                log.warning("No se pudo asignar stop a %s: %s", symbol, exc)

    def _maybe_raise_stop(
        self, row: dict, proposal: Proposal, position
    ) -> None:
        """Permite al LLM subir el stop, nunca bajarlo.

        Un modelo que puede alejar el stop puede anular la proteccion; que solo
        pueda acercarlo convierte la sugerencia en un trailing stop discrecional
        sin riesgo anadido.
        """
        suggested = proposal.suggested_stop
        if suggested is None:
            return
        current = _opt_float(row.get("stop_price"))
        if suggested >= position.current_price:
            return
        if current is not None and suggested <= current:
            return
        try:
            self.db.update_position_levels(str(row["id"]), stop_price=suggested)
            log.info(
                "%s: stop elevado de %s a %.2f por sugerencia del analista.",
                position.symbol, _fmt(current), suggested,
            )
        except DatabaseError as exc:
            log.warning("No se pudo actualizar el stop de %s: %s", position.symbol, exc)

    def _check_no_other_cycle_running(self, portfolio_id: str) -> str | None:
        """Devuelve el motivo por el que no se puede arrancar, o None si via libre.

        Un ciclo que quedo colgado en 'running' —contenedor reiniciado, Docker
        caido a media ejecucion— se da por abandonado pasado `STALE_CYCLE_MINUTES`
        y deja de bloquear. Sin esa salida, un unico proceso muerto detendria el
        agente para siempre.
        """
        try:
            other = self.db.find_running_cycle(portfolio_id)
        except DatabaseError as exc:
            log.warning("No se pudo comprobar si hay otro ciclo en marcha: %s", exc)
            return None

        if other is None:
            return None

        started_raw = str(other.get("started_at") or "")
        try:
            started = datetime.fromisoformat(started_raw)
        except ValueError:
            started = None

        if started is not None:
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            age_minutes = (datetime.now(timezone.utc) - started).total_seconds() / 60
            if age_minutes > STALE_CYCLE_MINUTES:
                self.db.abandon_cycle(
                    str(other["id"]),
                    f"Abandonado: seguia en 'running' tras {age_minutes:.0f} minutos. "
                    "Probablemente el proceso murio a media ejecucion.",
                )
                return None
            return (
                f"Ya hay un ciclo en marcha desde hace {age_minutes:.0f} min "
                f"({started_raw[11:19]} UTC). Espera a que termine: dos ciclos a la "
                "vez sobre la misma cartera se pisan las posiciones."
            )

        return (
            f"Ya hay un ciclo en marcha ({other['id']}). Espera a que termine."
        )

    def _prime_broker(self, snapshots: dict[str, MarketSnapshot]) -> None:
        """Entrega al broker simulado los precios de este ciclo.

        Se valora con el cierre de la sesion de decision y se ejecuta con la
        apertura siguiente. Mantener las dos cosas alineadas es lo que hace la
        simulacion honesta: el stop se comprueba contra el mismo cierre que vio
        el analista, y la orden resultante se llena a la apertura posterior, que
        es el orden real de los acontecimientos.

        Con Alpaca no hace nada: los precios los pone el broker.
        """
        if not isinstance(self.broker, SimBroker):
            return

        quotes = {
            symbol: Quote(
                fill_price=snapshot.execution_price,
                mark_price=snapshot.price,
                basis=snapshot.fill_basis,
            )
            for symbol, snapshot in snapshots.items()
        }
        self.broker.set_quotes(quotes)

        # La sesion es la de la barra de ejecucion; sirve de referencia para el
        # P&L diario y el kill switch.
        sessions = [s.session for s in snapshots.values() if s.session]
        if sessions:
            self.broker.roll_session(max(sessions))

        missing = set(self.db.get_open_positions(self.portfolio_id or "")) - set(quotes)
        if missing:
            log.warning(
                "Sin precio para %s: se valoran al precio de entrada y no se "
                "pueden cerrar en este ciclo.", ", ".join(sorted(missing)),
            )

    def _warn_if_budget_exceeds_account(self, account: AccountState) -> None:
        if self.settings.initial_budget > account.equity:
            log.warning(
                "INITIAL_BUDGET (%.2f) supera el equity de la cuenta (%.2f). Los "
                "limites de riesgo se calculan sobre el equity real, que es menor.",
                self.settings.initial_budget, account.equity,
            )

    def _record_unexecuted_order(
        self, cycle_id, portfolio_id, symbol, side, verdict,
        decision_id, risk_event_id, market_open,
    ) -> None:
        reason = "DRY_RUN activo" if self.settings.dry_run else "mercado cerrado"
        log.info("%s aprobada pero no ejecutada: %s.", symbol, reason)
        self._safe_save_order(
            cycle_id=cycle_id, portfolio_id=portfolio_id, symbol=symbol,
            side=side, qty=verdict.qty,
            status="dry_run" if self.settings.dry_run else "canceled",
            decision_id=decision_id, risk_event_id=risk_event_id,
            stop_price=verdict.stop_price, target_price=verdict.target_price,
            error=f"No ejecutada: {reason}.",
        )

    def _save_decision(
        self, cycle_id: str, portfolio_id: str, proposal: Proposal,
        snapshot_id: int | None,
    ) -> str | None:
        try:
            return self.db.save_decision(
                cycle_id=cycle_id, portfolio_id=portfolio_id,
                proposal=proposal, snapshot_id=snapshot_id,
            )
        except DatabaseError as exc:
            log.warning("No se pudo guardar la decision de %s: %s", proposal.symbol, exc)
            return None

    def _save_risk_event(
        self, cycle_id: str, portfolio_id: str, symbol: str | None,
        verdict, decision_id: str | None,
    ) -> str | None:
        try:
            return self.db.save_risk_event(
                cycle_id=cycle_id, portfolio_id=portfolio_id, symbol=symbol,
                verdict=verdict, decision_id=decision_id,
            )
        except DatabaseError as exc:
            log.warning("No se pudo guardar el evento de riesgo de %s: %s", symbol, exc)
            return None

    def _safe_save_order(self, **kwargs) -> str | None:
        try:
            return self.db.save_order(**kwargs)
        except DatabaseError as exc:
            log.error("No se pudo registrar la orden de %s: %s", kwargs.get("symbol"), exc)
            return None


# ----------------------------------------------------------------------

def _rejection(rule: str, reason: str):
    from .models import RiskVerdict
    return RiskVerdict(approved=False, reason=reason, rule=rule)


def _approval(rule: str, reason: str, *, qty: float = 0.0):
    from .models import RiskVerdict
    return RiskVerdict(approved=True, reason=reason, rule=rule, qty=qty)


def _opt_float(value) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _fmt(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.2f}"
