"""Orchestration of one agent cycle.

The order of the phases is deliberate:

  1. Reconcile with the broker      -> start from reality, not from the record.
  2. Market data                    -> a single request for the whole universe.
  3. Daily-loss kill switch         -> if it trips, nothing new is opened.
  4. Forced exits                   -> stop/target hit, without asking the LLM.
  5. LLM review of exits            -> thesis degraded.
  6. Entries                        -> analysis, risk filter, execution.
  7. Equity curve and close         -> always, even if something failed.

Exits go before entries for a practical reason: they free cash and position slots
that this very cycle's entries can use.
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

# Minutes after which a cycle in 'running' is presumed dead and stops blocking.
# A normal cycle with the funnel takes ~20; 90 leaves plenty of room without
# leaving the agent stopped all night because of a container that died.
STALE_CYCLE_MINUTES = 90


@dataclass
class CycleReport:
    """Summary of what happened, for the final log and for the tests."""

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
    analyst_calls: int = 0
    analyst_failures: int = 0
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
        # Only mentioned when there are failures: "0 of 33" in every summary is
        # noise that ends up unread, and this line has to stand out when it appears.
        if self.analyst_failures:
            lines.append(
                f"Analista: {self.analyst_failures} de {self.analyst_calls} "
                "llamadas sin respuesta"
            )
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
        """Assembles the cycle with the configured data provider.

        The simulated broker needs the database and the book id, so those are
        created first.
        """
        database = Database(path=settings.db_path)
        portfolio_id = database.ensure_portfolio(
            name=settings.portfolio_name,
            mode=settings.mode,
            initial_budget=settings.initial_budget,
        )

        broker = SimBroker(
            database=database,
            portfolio_id=portfolio_id,
            initial_cash=settings.initial_budget,
            slippage_bps=settings.sim_slippage_bps,
            commission_per_order=settings.sim_commission,
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

        # The calendar is consulted before spending anything: with no new bars,
        # analysing would mean repeating the previous cycle's decisions while
        # burning quota. With daily bars the natural moment is right after the
        # close, so the check is not "is it open" but "is there a session today".
        allowed, reason = market_calendar.should_run(
            settings.bar_interval, market=settings.market
        )
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

        # One cycle per book at a time. Two in parallel step on each other's cash
        # and positions, and leave a history with duplicated decisions that can no
        # longer be interpreted. It happens easily: launching one by hand while
        # the scheduler starts its own is enough.
        blocked = self._check_no_other_cycle_running(portfolio_id)
        if blocked is not None:
            report.status = "skipped"
            report.halted_reason = blocked
            log.warning("Ciclo no iniciado: %s", blocked)
            return report

        # Open positions are mandatory: they need reviewing even when the screener
        # does not select them. The provider adds its own candidates.
        required = tuple(sorted(self.db.get_open_positions(portfolio_id)))

        # The data comes BEFORE reading the account: the simulated broker has no
        # price source of its own, so without it the book cannot be valued nor
        # anything executed.
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

        # `settings.snapshot()` leaves this cycle's exact parameters in the row
        # (F6.3). Without that copy, editing the settings halfway through an
        # experiment would make the history unreadable: yesterday's decisions
        # would be read with today's configuration.
        cycle_id = self.db.start_cycle(
            portfolio_id=portfolio_id,
            equity_start=account.equity,
            cash_start=account.cash,
            market_open=market_open,
            symbols=list(symbols),
            llm_model=settings.llm_model,
            settings=settings.snapshot(),
        )
        report.cycle_id = cycle_id
        log.info("Ciclo %s iniciado. %s", cycle_id, settings.describe())
        if settings.risk_summary:
            log.info("Riesgo: %s", settings.risk_summary)
        log.info("Calendario: %s", market_calendar.describe(market=settings.market))

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

        # Closing the cycle: it runs whatever happens, so no rows are left
        # hanging in the 'running' state.
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

        # They are read here, and not at the end of `_run_phases`, because they
        # also count if an exception cut the phases off mid-way.
        report.analyst_calls = self.analyst.calls
        report.analyst_failures = self.analyst.failures
        self._grade_analyst(report)

        try:
            self.db.finish_cycle(
                cycle_id,
                status=report.status,
                equity_end=report.equity_end,
                error="; ".join(report.errors) if report.errors else None,
                analyst_calls=report.analyst_calls,
                analyst_failures=report.analyst_failures,
            )
        except DatabaseError as exc:
            log.error("No se pudo marcar el ciclo como finalizado: %s", exc)

        return report

    # ------------------------------------------------------------------

    def _grade_analyst(self, report: CycleReport) -> None:
        """Tells "the model said no" apart from "there was no model".

        `Analyst` swallows the `LLMError`s on purpose: a 429 on one symbol must
        not take the whole cycle down. But when the cause is exhausted quota or a
        provider outage, it fails on **every** call in a row, and the cycle used to
        end in 'completed' with zero proposals: indistinguishable from a session
        in which the model saw nothing. A two-week experiment can lose ten
        sessions that way without the history saying so.

        Three decisions:

          * **Only total failure degrades the status.** A cycle with 3 failures
            out of 33 did analyse and could trade; marking it 'failed' would lie
            in the other direction. The tally stays in the row and a note in
            `error`.
          * **A cycle that already came in as 'failed' or 'halted' is left
            alone.** The kill switch is the headline of its own cycle, and it does
            not evaluate entries by definition, so its calls are few and not
            representative.
          * **'failed' is reused instead of adding a new status.**
            `cycles.status` has a CHECK with four values and SQLite cannot alter a
            constraint: adding 'degraded' would force rebuilding the table six
            others hang off with `on delete cascade`. Worse: on an already created
            database the old CHECK would reject the new value, and the failure
            would show up on precisely the day the quota runs out, that is, the
            day this has to work. The tally in columns gives the nuance without
            touching the CHECK.
        """
        failures, calls = report.analyst_failures, report.analyst_calls
        if not failures:
            return

        detalle = f"El analista no respondio en {failures} de {calls} llamadas"

        if failures == calls and report.status == "completed":
            report.status = "failed"
            report.errors.append(
                f"{detalle}: este ciclo no ha analizado nada. Comprueba la cuota "
                "del proveedor y el log anterior."
            )
            log.error(
                "Ciclo sin analisis: %d de %d llamadas al modelo fallaron. "
                "El ciclo se marca como fallido para que no se lea como una "
                "sesion tranquila.", failures, calls,
            )
            return

        report.errors.append(f"{detalle}.")
        log.warning(
            "%s. El ciclo sigue siendo valido, pero esos simbolos se han "
            "quedado sin analizar.", detalle,
        )

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

        # --- 2. Recording the data the analyst saw ------------------------
        snapshot_ids: dict[str, int] = {}
        for symbol, snapshot in snapshots.items():
            try:
                snapshot_ids[symbol] = self.db.save_snapshot(
                    cycle_id=cycle_id, snapshot=snapshot
                )
            except DatabaseError as exc:
                log.warning("No se pudo guardar el snapshot de %s: %s", symbol, exc)

        tracked = self.db.get_open_positions(portfolio_id)

        # Adopted orphans have no stop: one is assigned by ATR so they are
        # protected from this very cycle on.
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
        # A symbol closed in this cycle is not reopened in the same cycle: that
        # would be buying and selling on the same day over the same thesis, which
        # is pure churn and in a real account counts as a day trade.
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

        # Refreshed state: this cycle's sales freed cash and slots.
        account = self.broker.get_account_state()
        report.equity_end = account.equity

        # Everything the provider returned that we do not already hold is a
        # candidate: with the funnel those are the screener's picks, without it
        # the watchlist.
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
                # Refreshed so the limits for the following candidates account
                # for the position just opened.
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
            # The order is already sent: this cannot be undone. It is logged loudly
            # so the next cycle's reconciliation adopts it.
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
        """Places an ATR stop on the adopted positions that have no levels."""
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
        """Lets the LLM raise the stop, never lower it.

        A model that can move the stop further away can void the protection; being
        able only to bring it closer turns the suggestion into a discretionary
        trailing stop with no added risk.
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
        """Returns the reason it cannot start, or None when the way is clear.

        A cycle left hanging in 'running' —container restarted, Docker down
        mid-run— is presumed abandoned after `STALE_CYCLE_MINUTES` and stops
        blocking. Without that escape hatch, a single dead process would stop the
        agent forever.
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
        """Hands the simulated broker this cycle's prices.

        Valuation uses the decision session's close and execution uses the
        following open. Keeping the two aligned is what makes the simulation
        honest: the stop is checked against the same close the analyst saw, and
        the resulting order fills at the later open, which is the real order of
        events.

        With a broker that had prices of its own it would do nothing, and that is
        why the type check is still here.
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

        # The session is the execution bar's; it serves as the reference for the
        # daily P&L and the kill switch.
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
