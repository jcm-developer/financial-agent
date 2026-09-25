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

from . import market_calendar, stop_signal
from .analyst import Analyst, prompt_versions
from .broker import Broker, BrokerError
from .config import Settings
from .db import Database, DatabaseError
from .formatting import money, number as fmt_number, signed_money
from .llm import LLMClient
from .market_data import INDICATOR_INTERVAL, MarketDataError, build_market_data
from .models import AccountState, ExitSignal, MarketSnapshot, Proposal
from .news import NewsContext, NewsProvider, NumberedHeadline, build_news_provider, number
from .risk import RiskManager
from .sim_broker import Quote, SimBroker

log = logging.getLogger(__name__)

# Minutes after which a cycle in 'running' is presumed dead and stops blocking.
# A normal cycle with the funnel takes ~20; 90 leaves plenty of room without
# leaving the agent stopped all night because of a container that died.
STALE_CYCLE_MINUTES = 90

#: What is recorded and shown when the stop came from the interface. It ends up in
#: `cycles.error` and in the summary, both of which are read on screen, so it is
#: screen text (and it is what tells this 'halted' from the kill switch's).
STOP_REASON = "Parada solicitada desde la interfaz."

#: How the summary names each `cycles.status`; the column keeps the machine value.
_STATUS_LABELS = {
    "running": "en marcha",
    "completed": "completado",
    "halted": "detenido",
    "failed": "fallido",
    "skipped": "omitido",
}

#: How the log names the rule behind an exit. `positions.exit_reason` keeps the
#: machine key in brackets, because the Posiciones screen splits it from there.
_EXIT_RULE_LABELS = {
    "stop_loss_hit": "stop",
    "take_profit_hit": "objetivo",
    "llm_exit": "decisión del analista",
    "experiment_closed": "cierre del experimento",
}

#: Why an approved order was not sent, as the order's `error` and the log say it.
_DRY_RUN_REASON = "modo de prueba (DRY_RUN)"
_MARKET_CLOSED_REASON = "mercado cerrado"

#: Fraction of the profile's initial stop distance that the trailing stop may
#: never cross (F9.25). Half, so the ratchet still has somewhere to go —a floor
#: at 1x would freeze it— while the position keeps a cushion the daily noise
#: does not clear on its own. It is a fraction and not an absolute number of
#: ATRs because `stop_atr_multiple` is a profile setting: a conservative profile
#: with a 3x stop gets a 1,5x floor and an aggressive one with 1,5x gets 0,75x,
#: which is the same decision in both.
TRAILING_STOP_FLOOR_FRACTION = 0.5


class CycleStopped(Exception):
    """A stop was requested from the interface and honoured at a checkpoint.

    An exception and not a `return`, because the request has to be honoured from
    inside two nested loops **and** the closing block —equity snapshot and
    `finish_cycle`— has to run all the same: a cycle that stops without closing
    its row blocks the next one for the 90 minutes of `STALE_CYCLE_MINUTES`. It is
    caught in `run()` and never leaves this module.
    """


@dataclass
class CycleReport:
    """Summary of what happened, for the final log and for the tests."""

    cycle_id: str | None = None
    status: str = "completed"
    market_open: bool = False
    equity_start: float = 0.0
    equity_end: float = 0.0
    #: What the profile's market prices in. The summary is shown verbatim on the
    #: Ciclos screen, so it is screen text and the symbol travels with the figure
    #: (FE.8). Empty by default rather than `$`: a report built without one says
    #: nothing instead of saying something false.
    currency_symbol: str = ""
    analyzed: int = 0
    proposals_buy: int = 0
    approved: int = 0
    rejected: int = 0
    orders_submitted: int = 0
    exits_forced: int = 0
    exits_discretionary: int = 0
    halted_reason: str | None = None
    #: True when the cycle was cut short because it was asked to stop. Kept apart
    #: from `halted_reason` so the summary does not print "KILL SWITCH" over a stop
    #: somebody asked for: they are opposite readings of the same short cycle.
    stopped: bool = False
    screened: str | None = None
    analyst_calls: int = 0
    analyst_failures: int = 0
    #: News lookups this cycle made (one per analysed symbol, plus the market),
    #: how many could not be made, and how many headlines reached a prompt. All
    #: zero with news off, and then the summary does not mention them.
    news_queries: int = 0
    news_failures: int = 0
    news_headlines: int = 0
    #: Open positions this cycle got no price for. Empty is the normal case.
    positions_without_price: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.status == "skipped":
            return f"Ciclo omitido. {self.halted_reason or ''}".strip()
        lines = [
            f"Estado del ciclo: {_STATUS_LABELS.get(self.status, self.status)}",
            f"Mercado abierto: {'sí' if self.market_open else 'no'}",
            f"Capital: {money(self.equity_start, self.currency_symbol)} → "
            f"{money(self.equity_end, self.currency_symbol)}",
        ]
        if self.screened:
            lines.append(f"Cribado: {self.screened}")
        lines += [
            f"Analizados: {self.analyzed} · propuestas de compra: {self.proposals_buy}",
            f"Riesgo: {self.approved} aprobadas, {self.rejected} rechazadas",
            f"Órdenes enviadas: {self.orders_submitted}",
            f"Salidas: {self.exits_forced} forzadas, "
            f"{self.exits_discretionary} a criterio del analista",
        ]
        # Same criterion as the analyst's failures below: it is only named when
        # it happens, so that when it appears it is read. It goes ABOVE the
        # errors because it is not an error —the cycle completed— and it is the
        # line that explains why a position did not move.
        if self.positions_without_price:
            lines.append(
                f"SIN PRECIO: {', '.join(self.positions_without_price)} "
                "(sin comprobar el stop ni revisar la tesis)"
            )

        if self.news_queries:
            line = (
                f"Noticias: {self.news_headlines} titulares en "
                f"{self.news_queries} consultas"
            )
            if self.news_failures:
                line += f" ({self.news_failures} no se pudieron consultar)"
            lines.append(line)

        # Only mentioned when there are failures: "0 of 33" in every summary is
        # noise that ends up unread, and this line has to stand out when it appears.
        if self.analyst_failures:
            lines.append(
                f"Analista: {self.analyst_failures} de {self.analyst_calls} "
                "llamadas sin respuesta"
            )
        if self.stopped:
            lines.append(f"PARADA: {self.halted_reason}")
        elif self.halted_reason:
            lines.append(f"SIN OPERAR EL RESTO DEL DÍA: {self.halted_reason}")
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
        news: NewsProvider | None = None,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.market_data = market_data
        self.db = database
        self.analyst = analyst
        self.risk = risk_manager
        self.portfolio_id = portfolio_id
        #: Where headlines come from, or None when the profile runs without news
        #: (F9.4). None and not an empty provider: a profile without news must
        #: send the analyst exactly the prompt it always sent.
        self.news = news
        #: This cycle's market context, fetched once and shown to every prompt.
        self._market_news: tuple[NumberedHeadline, ...] = ()
        self._market_news_error: str | None = None
        #: Resolved once and kept here instead of asking the calendar at each
        #: use: it was being derived in three places and forgotten in a fourth,
        #: which is how the sell line ended up printing "USD" in a European book.
        self.currency_symbol = market_calendar.get_market(settings.market).currency_symbol

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
            extra_commission=settings.sim_commission,
            currency_symbol=market_calendar.get_market(settings.market).currency_symbol,
        )

        return cls(
            settings=settings,
            broker=broker,
            market_data=build_market_data(settings, database),
            database=database,
            analyst=Analyst(
                llm,
                # Dos relojes, y no es un detalle de formato (F9.14): el precio va
                # en el intervalo del perfil y los indicadores siempre en diario,
                # asi que el prompt tiene que nombrar los dos o miente en uno.
                price_interval=settings.bar_interval,
                indicator_interval=INDICATOR_INTERVAL,
                # La divisa se pasa, nunca se asume (FE.8): el prompt decia
                # "USD" para todo, asi que un experimento europeo le contaba al
                # modelo que SAN.MC cotiza en dolares.
                currency=market_calendar.get_market(settings.market).currency,
                # The prompt states what operating costs, so the model stops
                # proposing targets whose whole gain is the commission (F9.9).
                commission_for=broker.commission_for,
                # And the ceiling, so `suggested_weight_pct` has a scale to be
                # small against (F9.13).
                max_position_pct=settings.risk.max_position_pct,
                # El horizonte del experimento y el suelo que se aplicara al
                # objetivo a ese plazo (F9.16, F9.17). Hasta aqui el modelo no
                # sabia a que plazo se le juzgaba, asi que contestaba 14 dias a
                # todo y ponia el objetivo a una sigma de dos semanas.
                horizon_days=settings.horizon_days,
                min_target_sigma=settings.risk.min_target_sigma,
            ),
            # Same reason as the analyst's currency right above: the verdict's
            # text is stored and printed as it is, so an approval in a European
            # profile was reading "por $3,949.20".
            risk_manager=RiskManager(
                settings.risk,
                currency_symbol=market_calendar.get_market(settings.market).currency_symbol,
                # The broker's own tariff and not `fees.standard_commission`
                # directly: this one adds the profile's `sim_commission`
                # surcharge, so a what-if with heavier friction is sized and
                # filtered with the friction it declares (F9.9).
                commission_for=broker.commission_for,
                # El horizonte sobre el que se mide el suelo del objetivo (F9.16).
                # El del perfil y no el que declare la propuesta: ver el comentario
                # de `RiskManager.horizon_days`.
                horizon_days=settings.horizon_days,
            ),
            portfolio_id=portfolio_id,
            news=build_news_provider(settings),
        )

    # ------------------------------------------------------------------

    def run(self) -> CycleReport:
        settings = self.settings
        report = CycleReport(currency_symbol=self.currency_symbol)

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
        # ⚠️ **En el orden en que los devolvio el proveedor, que es el del screener,
        # y no alfabetico** (F9.18). Este `sorted()` fue durante meses el sitio
        # donde se tiraba el ranking: el screener puntua el universo y ordena por
        # puntuacion, y aqui se reordenaba por nombre. Como las entradas se
        # ejecutan una a una y la caja se gasta al pasar, el primer ciclo del
        # experimento nuevo compro los cinco primeros del abecedario —ABI, ADS,
        # AENA, CS, GRF— y dejo 110 EUR para los diecinueve analisis siguientes.
        # Quien decide en que se gasta el dinero no puede ser la inicial.
        symbols = tuple(snapshots)
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
            # Plus which prompts it ran on (2026-09-25): experiments run one after
            # another now, and a prompt edited between two of them is a difference
            # in what was measured that no setting records.
            settings={
                **settings.snapshot(),
                "prompt_versions": prompt_versions(self.news is not None),
            },
        )
        report.cycle_id = cycle_id
        # Any request still lying around is for an earlier cycle: this one did not
        # exist a line ago. Cleared here so a Parar that arrived a second too late
        # does not stop the cycle that comes after the one it was meant for.
        stop_signal.clear(settings.db_path)
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
        except CycleStopped:
            # 'halted' and not a status of its own: `cycles.status` has a CHECK with
            # four values and SQLite cannot alter a constraint, so 'stopped' would
            # mean rebuilding the table six others hang off — and on an already
            # created database the old CHECK would reject the value on the very day
            # somebody presses the button. Same call `_grade_analyst` documents.
            # What separates the two 'halted' is `error`: the kill switch leaves it
            # empty and records a `risk_event`; this writes the reason.
            report.status = "halted"
            report.stopped = True
            report.halted_reason = STOP_REASON
            report.errors.append(STOP_REASON)
            log.warning("Ciclo detenido: %s", STOP_REASON)
        except (BrokerError, MarketDataError, DatabaseError) as exc:
            report.status = "failed"
            report.errors.append(str(exc))
            log.exception("El ciclo ha fallado: %s", exc)
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

    def close_all_positions(self, *, reason: str = "Cierre del experimento.") -> CycleReport:
        """Sells every open position, to end an experiment (F5.8).

        **It goes through the broker and the same exit path as any other sale**,
        not through an `UPDATE` to `positions`. That is the whole point: a
        position closed by hand in the database leaves no order, no fill, no exit
        price and no reason, so the history stops explaining the result it is
        being read for.

        The model is not consulted, and that is deliberate. This is not a
        decision about the market: the experiment is over and the book is
        liquidated. Asking the analyst would record a "sell" as if it had been
        judged, and it was not.

        No entry is opened and nothing is screened, so it costs no quota.

        It reuses the cycle's own lock, the copy of the settings and the equity
        snapshot, so the closure appears in the history as one more cycle —which
        is what it is— and can be told apart by its `experiment_closed` rule.
        """
        settings = self.settings
        report = CycleReport(currency_symbol=self.currency_symbol)

        portfolio_id = self.portfolio_id or self.db.ensure_portfolio(
            name=settings.portfolio_name,
            mode=settings.mode,
            initial_budget=settings.initial_budget,
        )

        blocked = self._check_no_other_cycle_running(portfolio_id)
        if blocked is not None:
            report.status = "skipped"
            report.halted_reason = blocked
            log.warning("Cierre no iniciado: %s", blocked)
            return report

        open_symbols = tuple(sorted(self.db.get_open_positions(portfolio_id)))
        if not open_symbols:
            # Not a failure: an experiment with nothing open is already closed,
            # and saying so beats writing an empty cycle into the history.
            report.status = "skipped"
            report.halted_reason = "No hay ninguna posición abierta que cerrar."
            log.info("Cierre innecesario: no hay posiciones abiertas.")
            return report

        snapshots = self.market_data.fetch_snapshots(open_symbols)
        self._prime_broker(snapshots)

        account = self.broker.get_account_state()
        market_open = self.broker.is_market_open()
        report.market_open = market_open
        report.equity_start = account.equity
        report.equity_end = account.equity

        # Refused up front instead of recording a pile of cancelled orders. With
        # the market shut there is no price to sell at, and inventing one would
        # falsify precisely the figure this whole operation exists to produce.
        if not self._can_execute(market_open):
            why = (
                f"está activo el {_DRY_RUN_REASON}" if settings.dry_run
                else "el mercado está cerrado"
            )
            report.status = "skipped"
            report.halted_reason = (
                f"No se puede cerrar ahora: {why}. Las {len(open_symbols)} posiciones "
                f"siguen abiertas; vuelve a intentarlo en la próxima sesión."
            )
            log.warning("Cierre no ejecutado: %s", why)
            return report

        cycle_id = self.db.start_cycle(
            portfolio_id=portfolio_id,
            equity_start=account.equity,
            cash_start=account.cash,
            market_open=market_open,
            symbols=list(open_symbols),
            llm_model=settings.llm_model,
            settings=settings.snapshot(),
        )
        report.cycle_id = cycle_id
        log.info(
            "CIERRE DEL EXPERIMENTO %s: %d posiciones a liquidar.",
            cycle_id, len(open_symbols),
        )

        tracked = self.db.get_open_positions(portfolio_id)
        broker_positions = {p.symbol: p for p in account.positions}

        try:
            for symbol in open_symbols:
                position = broker_positions.get(symbol)
                snapshot = snapshots.get(symbol)
                qty = position.qty if position else float(
                    tracked.get(symbol, {}).get("qty") or 0.0
                )
                if qty <= 0:
                    log.warning("%s: sin cantidad en el broker; se omite.", symbol)
                    continue
                signal = ExitSignal(
                    symbol=symbol,
                    qty=qty,
                    reason=reason,
                    rule="experiment_closed",
                    forced=True,
                    price=snapshot.price if snapshot else (
                        position.current_price if position else 0.0
                    ),
                )
                if self._execute_exit(
                    report, portfolio_id, cycle_id, signal, tracked,
                    broker_positions, market_open=market_open,
                ):
                    # Counted as forced: nobody judged the market, the experiment
                    # ended. Grouping it with the discretionary exits would make
                    # the analytics read a liquidation as a decision of the model.
                    report.exits_forced += 1
        except (BrokerError, MarketDataError, DatabaseError) as exc:
            report.status = "failed"
            report.errors.append(str(exc))
            log.exception("El cierre ha fallado: %s", exc)
        except Exception as exc:  # noqa: BLE001 - queremos cerrar el ciclo siempre
            report.status = "failed"
            report.errors.append(f"Error inesperado: {exc}")
            log.exception("Error inesperado al cerrar el experimento.")

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

        try:
            self.db.finish_cycle(
                cycle_id,
                status=report.status,
                equity_end=report.equity_end,
                error="; ".join(report.errors) if report.errors else None,
            )
        except DatabaseError as exc:
            log.error("No se pudo marcar el cierre como finalizado: %s", exc)

        log.info(
            "Experimento cerrado: %d posiciones liquidadas, capital final de %s.",
            report.exits_forced, money(report.equity_end, self.currency_symbol),
        )
        return report

    # ------------------------------------------------------------------

    def check_stops(self) -> CycleReport:
        """Checks the stop and target of every open position, without the model (F9.36).

        The analysing cycle runs once a day (decision nº 11) and the mandatory
        exits were only checked inside it, so a position that fell through its
        stop at 11:00 was not sold until 10:20 the next day. A real stop order is
        watched by the broker all session; this brings the simulation closer to
        that, at the resolution of the hourly bar.

        **Step 6 of the cycle and nothing else.** No screener, no analyst, no
        entries, no stop raised: those need the model's judgement, and asking it
        several times a day was what decision nº 11 ruled out. What runs here is
        the rule that does not need judging —a stop hit is not negotiable— so it
        costs no quota.

        **It writes nothing unless something fires.** Seven checks a day per
        profile would otherwise fill `cycles` with empty rows and the equity
        curve with points of nothing; a check that finds every position inside
        its levels returns `skipped` and leaves only a log line. When an exit
        does fire it is recorded as one more cycle —the orders and positions
        need a `cycle_id`—, with `llm_model` NULL and `cycle_kind` in the
        settings copy, so it can be told apart from an analysing cycle.
        """
        settings = self.settings
        report = CycleReport(currency_symbol=self.currency_symbol)

        allowed, reason = market_calendar.should_run(
            settings.bar_interval, market=settings.market
        )
        if settings.skip_when_market_closed and not allowed:
            report.status = "skipped"
            report.halted_reason = reason
            log.info("Comprobación de stops omitida: %s", reason)
            return report

        portfolio_id = self.portfolio_id or self.db.ensure_portfolio(
            name=settings.portfolio_name,
            mode=settings.mode,
            initial_budget=settings.initial_budget,
        )

        blocked = self._check_no_other_cycle_running(portfolio_id)
        if blocked is not None:
            # The running cycle checks the same levels in its own step 6.
            report.status = "skipped"
            report.halted_reason = blocked
            log.info("Comprobación de stops omitida: %s", blocked)
            return report

        open_symbols = tuple(sorted(self.db.get_open_positions(portfolio_id)))
        if not open_symbols:
            report.status = "skipped"
            report.halted_reason = "No hay posiciones abiertas."
            log.info("Comprobación de stops: no hay posiciones abiertas.")
            return report

        snapshots = self.market_data.fetch_positions(open_symbols)
        self._prime_broker(snapshots)

        account = self.broker.get_account_state()
        market_open = self.broker.is_market_open()
        report.market_open = market_open
        report.equity_start = account.equity
        report.equity_end = account.equity

        if not self._can_execute(market_open):
            why = (
                f"está activo el {_DRY_RUN_REASON}" if settings.dry_run
                else "el mercado está cerrado"
            )
            report.status = "skipped"
            report.halted_reason = f"No se comprueban stops: {why}."
            log.info("Comprobación de stops omitida: %s", why)
            return report

        tracked = self.db.get_open_positions(portfolio_id)
        broker_positions = {p.symbol: p for p in account.positions}
        levels = {
            symbol: {
                "stop_price": _opt_float(row.get("stop_price")),
                "target_price": _opt_float(row.get("target_price")),
            }
            for symbol, row in tracked.items()
        }
        signals = self.risk.mandatory_exits(broker_positions, levels)
        if not signals:
            report.status = "skipped"
            report.halted_reason = (
                f"Ningún stop ni objetivo alcanzado en {len(open_symbols)} posiciones."
            )
            log.info("Comprobación de stops: %s", report.halted_reason)
            return report

        cycle_id = self.db.start_cycle(
            portfolio_id=portfolio_id,
            equity_start=account.equity,
            cash_start=account.cash,
            market_open=market_open,
            symbols=[signal.symbol for signal in signals],
            llm_model=None,
            settings={**settings.snapshot(), "cycle_kind": "stop_check"},
        )
        report.cycle_id = cycle_id
        log.info(
            "COMPROBACIÓN DE STOPS %s: %d salidas obligatorias.", cycle_id, len(signals)
        )

        try:
            for signal in signals:
                if self._execute_exit(
                    report, portfolio_id, cycle_id, signal, tracked,
                    broker_positions, market_open=market_open,
                ):
                    report.exits_forced += 1
                    broker_positions.pop(signal.symbol, None)
        except (BrokerError, MarketDataError, DatabaseError) as exc:
            report.status = "failed"
            report.errors.append(str(exc))
            log.exception("La comprobación de stops ha fallado: %s", exc)
        except Exception as exc:  # noqa: BLE001 - queremos cerrar el ciclo siempre
            report.status = "failed"
            report.errors.append(f"Error inesperado: {exc}")
            log.exception("Error inesperado al comprobar los stops.")

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

        try:
            self.db.finish_cycle(
                cycle_id,
                status=report.status,
                equity_end=report.equity_end,
                error="; ".join(report.errors) if report.errors else None,
            )
        except DatabaseError as exc:
            log.error("No se pudo marcar la comprobación de stops como finalizada: %s", exc)

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

        detalle = f"El analista no respondió en {failures} de {calls} llamadas"

        if failures == calls and report.status == "completed":
            report.status = "failed"
            report.errors.append(
                f"{detalle}: este ciclo no ha analizado nada. Comprueba la cuota "
                "del proveedor y el log anterior."
            )
            log.error(
                "Ciclo sin análisis: fallaron %d de %d llamadas al modelo. "
                "El ciclo se marca como fallido para que no se lea como una "
                "sesión tranquila.", failures, calls,
            )
            return

        report.errors.append(f"{detalle}.")
        log.warning(
            "%s. El ciclo sigue siendo válido, pero esos valores se han "
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
                log.warning("No se pudieron guardar los datos de mercado de %s: %s", symbol, exc)

        # --- 2 bis. Market context, once for the whole cycle (F9.4) ---------
        self._fetch_market_news(report, cycle_id)

        tracked = self.db.get_open_positions(portfolio_id)

        # Adopted orphans have no stop: one is assigned by ATR so they are
        # protected from this very cycle on.
        self._assign_stops_to_orphans(
            reconcile_report.adopted_orphans, snapshots, broker_positions, tracked
        )
        if reconcile_report.adopted_orphans:
            tracked = self.db.get_open_positions(portfolio_id)

        self._report_positions_without_price(
            report, portfolio_id, cycle_id, tracked, snapshots
        )

        # --- 3. Kill switch ----------------------------------------------
        kill_switch = self.risk.check_kill_switch(account)
        if kill_switch.triggered:
            report.halted_reason = kill_switch.reason
            log.warning("Sin operar el resto del día: %s", kill_switch.reason)
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

            self._check_stop(cycle_id)
            proposal = self.analyst.evaluate_exit(
                position=position,
                snapshot=snapshot,
                entry_thesis=row.get("thesis"),
                stop_price=_opt_float(row.get("stop_price")),
                target_price=_opt_float(row.get("target_price")),
                news=self._news_for(report, cycle_id, symbol),
            )
            if proposal is None:
                continue

            decision_id = self._save_decision(
                cycle_id, portfolio_id, proposal, snapshot_ids.get(symbol)
            )
            self._maybe_raise_stop(row, proposal, position, snapshot)

            if proposal.action != "sell":
                continue
            if proposal.conviction < self.settings.risk.min_conviction:
                log.info(
                    "%s: venta propuesta con convicción %d, por debajo del mínimo de %d; "
                    "se mantiene la posición.",
                    symbol, proposal.conviction, self.settings.risk.min_conviction,
                )
                continue

            signal = ExitSignal(
                symbol=symbol,
                qty=position.qty,
                reason=proposal.thesis or "El analista da la tesis por agotada.",
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
        # Checked before the phase and not only inside its loop: the entries are
        # what open positions, so a stop asked for during the exits must not buy
        # anything on its way out.
        self._check_stop(cycle_id)

        if kill_switch.triggered:
            log.info("No se evalúan entradas: la pérdida del día ha alcanzado el límite.")
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

        # Cuantas puede abrir este ciclo (F9.18). 0 = sin tope.
        per_cycle_cap = self.settings.max_new_positions_per_cycle
        opened_this_cycle = 0

        # ⚠️ **Dos pasadas: primero se analiza todo, y solo entonces se reparte**
        # (F9.19). Hasta el 2026-09-26 el ciclo analizaba y ejecutaba en la misma
        # pasada, asi que con un tope de dos entradas se llevaban el dinero las dos
        # primeras `buy` del ranking del screener, y las siguientes ni se
        # preguntaban. La conviccion del modelo no decidia que se compraba, y eso
        # es justo lo que tres perfiles con tres modelos intentan comparar.
        #
        # Lo que cuesta: con plazas libres se analizan siempre los veinte
        # candidatos, en vez de cortar al llenar el tope. El corte que ahorra
        # cuota se queda donde si sirve: con la cartera llena no se pregunta nada.
        #
        # Lo que no cuesta, aunque lo pareciera: precio. Todas las ordenes del
        # ciclo se ejecutan a la apertura de la misma barra, fijada al bajar los
        # datos, asi que da igual mandar la primera a las 10:21 o a las 10:40. Eso
        # cambiara con F9.3, al ejecutar a precio vivo.
        if len(account.positions) >= self.settings.risk.max_open_positions:
            log.info("Cartera llena: no se evalúan entradas en este ciclo.")
            return

        # --- 6a. Analizar todos los candidatos ------------------------------
        buys: list[tuple[Proposal, MarketSnapshot, str | None]] = []
        for symbol in candidates:
            self._check_stop(cycle_id)
            snapshot = snapshots[symbol]
            proposal = self.analyst.evaluate_entry(
                snapshot, account, news=self._news_for(report, cycle_id, symbol)
            )
            if proposal is None:
                continue

            decision_id = self._save_decision(
                cycle_id, portfolio_id, proposal, snapshot_ids.get(symbol)
            )

            if proposal.action != "buy":
                continue
            report.proposals_buy += 1
            buys.append((proposal, snapshot, decision_id))

        # --- 6b. Repartir, de mas a menos conviccion -------------------------
        # Por conviccion y no por sigmas de recorrido prometido: es la cifra con
        # la que el propio modelo dice cuanto se fia, y la que F9.27 calibrara.
        # `sorted` es estable, asi que a igual conviccion manda el orden del
        # screener, que es el desempate que ya se tenia.
        self._check_stop(cycle_id)
        ranked = sorted(buys, key=lambda item: -item[0].conviction)
        if len(ranked) > 1:
            log.info(
                "Propuestas de compra por convicción: %s",
                ", ".join(f"{p.symbol} ({p.conviction})" for p, _, _ in ranked),
            )

        for proposal, snapshot, decision_id in ranked:
            symbol = proposal.symbol
            full = len(account.positions) >= self.settings.risk.max_open_positions
            capped = per_cycle_cap > 0 and opened_this_cycle >= per_cycle_cap
            if full or capped:
                # Recorded, not dropped: a `buy` that was analysed and did not get
                # money is part of what the model said, and the Riesgo screen and
                # F9.27 need to see why it was not executed.
                reason = (
                    "Sin plaza: la cartera ha llegado a su máximo de posiciones."
                    if full else
                    f"Sin plaza: el ciclo ya ha abierto {opened_this_cycle} entradas, "
                    f"su tope. Había propuestas con más convicción."
                )
                self._save_risk_event(
                    cycle_id, portfolio_id, symbol, _rejection("entry_cap", reason),
                    decision_id,
                )
                report.rejected += 1
                log.info("SIN PLAZA %s (convicción %d)", symbol, proposal.conviction)
                continue

            atr = _opt_float(snapshot.indicators.get("atr_14"))
            verdict = self.risk.evaluate_entry(proposal, account, atr)
            risk_event_id = self._save_risk_event(
                cycle_id, portfolio_id, symbol, verdict, decision_id
            )

            if not verdict.approved:
                report.rejected += 1
                log.info("RECHAZADA %s: %s", symbol, verdict.reason)
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
                opened_this_cycle += 1
                # Refreshed so the limits for the following proposals account for
                # the position just opened.
                account = self.broker.get_account_state()
                report.equity_end = account.equity

    # ------------------------------------------------------------------
    # Ejecucion
    # ------------------------------------------------------------------

    def _fetch_market_news(self, report: CycleReport, cycle_id: str) -> None:
        """The market context for this cycle: fetched once, stored once, shown to
        every prompt. Never raises: a feed that fails leaves an error the prompt
        states, not a dead cycle."""
        if self.news is None:
            return
        report.news_queries += 1
        result = self.news.market_context()
        if result.error:
            report.news_failures += 1
            self._market_news_error = result.error
            log.warning("Contexto de mercado no disponible: %s", result.error)
            return
        self._market_news = number(result.headlines, "M")
        report.news_headlines += len(self._market_news)
        self._save_news(cycle_id, None, self._market_news)
        log.info("Contexto de mercado: %d titulares.", len(self._market_news))

    def _news_for(
        self, report: CycleReport, cycle_id: str, symbol: str
    ) -> NewsContext | None:
        """What one prompt is told about the news, already stored.

        Fetched right before the model is asked, not up front for every
        candidate: the entries stop at `max_new_positions_per_cycle`, and
        headlines for symbols nobody analyses would be requests to Google and
        rows in `news_items` that no prompt ever showed.
        """
        if self.news is None:
            return None
        report.news_queries += 1
        result = self.news.company(symbol)
        company = number(result.headlines, "N")
        if result.error:
            report.news_failures += 1
        report.news_headlines += len(company)
        self._save_news(cycle_id, symbol, company)
        return NewsContext(
            company=company,
            company_error=result.error,
            market=self._market_news,
            market_error=self._market_news_error,
            max_age_days=self.settings.news_max_age_days,
        )

    def _save_news(
        self, cycle_id: str, symbol: str | None, items: tuple[NumberedHeadline, ...]
    ) -> None:
        try:
            self.db.save_news_items(
                cycle_id=cycle_id, symbol=symbol, items=[item.as_row() for item in items]
            )
        except DatabaseError as exc:
            # Logged and not raised: the analysis can still run, but the record of
            # what it read is incomplete, and that is worth a warning.
            log.warning("No se pudieron guardar los titulares de %s: %s", symbol or "mercado", exc)

    def _check_stop(self, cycle_id: str) -> None:
        """Honours a stop asked for from the interface, if it is for this cycle.

        Called **right before each call to the model**, which is where the time
        goes: a cycle spends its twenty minutes waiting for the analyst, so
        checking here makes the stop land within seconds and never in the middle
        of sending an order — the two things a SIGTERM cannot promise
        (`src/stop_signal.py`).

        The request is deleted as it is honoured so the same file cannot stop the
        next cycle too.
        """
        if not stop_signal.requested_for(self.settings.db_path, cycle_id):
            return
        stop_signal.clear(self.settings.db_path)
        raise CycleStopped

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
            log.error("Ha fallado la orden de compra de %s: %s", symbol, exc)
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
                "Orden de %s enviada, pero no se pudo registrar la posición: %s. "
                "El próximo ciclo la recuperará al cuadrar la cartera con el broker.",
                symbol, exc,
            )
            report.errors.append(f"Posición de {symbol} sin registrar: {exc}")

        log.info(
            "COMPRA %s: %g acciones a unos %s, stop en %s, objetivo en %s",
            symbol, verdict.qty, fmt_number(entry_price),
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
            reason = _DRY_RUN_REASON if self.settings.dry_run else _MARKET_CLOSED_REASON
            log.warning(
                "SALIDA PENDIENTE %s (%s): %s No se ejecuta: %s.",
                symbol, _EXIT_RULE_LABELS.get(signal.rule, signal.rule),
                signal.reason, reason,
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
            log.error("Ha fallado el cierre de %s: %s", symbol, exc)
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
                log.error("Posición de %s cerrada en el broker pero no en la base de datos: %s",
                          symbol, exc)
                report.errors.append(f"Cierre de {symbol} sin registrar: {exc}")
            log.info(
                "VENTA %s: %g acciones a unos %s, resultado de %s (%s)",
                symbol, signal.qty, fmt_number(exit_price),
                signed_money(realized, self.currency_symbol),
                _EXIT_RULE_LABELS.get(signal.rule, signal.rule),
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
                    "%s recuperada sin datos de mercado: queda sin stop. Revísala a mano.",
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
                log.info("%s recuperada: stop en %s por ATR.", symbol, fmt_number(stop))
            except DatabaseError as exc:
                log.warning("No se pudo asignar stop a %s: %s", symbol, exc)

    def _maybe_raise_stop(
        self, row: dict, proposal: Proposal, position, snapshot: MarketSnapshot
    ) -> None:
        """Lets the LLM raise the stop, never lower it, and never past the floor.

        A model that can move the stop further away can void the protection; being
        able only to bring it closer turns the suggestion into a discretionary
        trailing stop.

        ⚠️ **«Only closer» is not the same as «no added risk», and that was the
        bug (F9.25).** `risk.py` grants the analyst's stop on an entry *only if it
        is wider* than the ATR one —see `llm_wider`, "never the other way
        round"— because the distance to the stop is this system's risk unit. Here
        the asymmetry ran the other way and unbounded: every suggestion closer to
        the price was written as it came. Measured on the first cycle with
        `nemotron-3-super`, six of nine positions moved in one go and the book
        went from stops at 3x ATR to stops between 0,48x and 1,20x, with one of
        them **above** the live price. At that distance the position does not exit
        on a broken thesis, it exits on Tuesday's noise, and with a 45-day horizon
        and 12 % targets that is the experiment losing its positions before its
        thesis can be right or wrong.

        So the suggestion is still honoured, but clipped at
        `TRAILING_STOP_FLOOR_FRACTION` of the profile's own stop distance. The
        clip is logged with both numbers: a stop that silently ends up somewhere
        other than where the analyst asked is exactly what took a whole cycle to
        notice.
        """
        suggested = proposal.suggested_stop
        if suggested is None:
            return
        current = _opt_float(row.get("stop_price"))
        price = position.current_price
        if suggested >= price:
            return
        if current is not None and suggested <= current:
            return

        stop = suggested
        atr = _opt_float(snapshot.indicators.get("atr_14"))
        if atr:
            floor_multiple = (
                self.settings.risk.stop_atr_multiple * TRAILING_STOP_FLOOR_FRACTION
            )
            nearest = price - atr * floor_multiple
            if suggested > nearest:
                if current is not None and nearest <= current:
                    log.info(
                        "%s: el analista pedía el stop en %s, a %s veces el ATR del "
                        "precio; se queda en %s porque el mínimo de %s veces el ATR "
                        "(%s) no lo mejora.",
                        position.symbol, fmt_number(suggested),
                        fmt_number((price - suggested) / atr), _fmt(current),
                        fmt_number(floor_multiple), fmt_number(nearest),
                    )
                    return
                log.info(
                    "%s: el analista pedía el stop en %s, a %s veces el ATR del "
                    "precio; se sube solo a %s, el mínimo de %s veces el ATR.",
                    position.symbol, fmt_number(suggested),
                    fmt_number((price - suggested) / atr), fmt_number(nearest),
                    fmt_number(floor_multiple),
                )
                stop = nearest

        try:
            self.db.update_position_levels(str(row["id"]), stop_price=round(stop, 4))
            log.info(
                "%s: stop subido de %s a %s a propuesta del analista.",
                position.symbol, _fmt(current), fmt_number(stop),
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
                    f"Abandonado: seguía en marcha tras {age_minutes:.0f} minutos. "
                    "Probablemente el proceso murió a medias.",
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
                "Sin precio para %s: se valoran a su precio de entrada y no se "
                "pueden cerrar en este ciclo.", ", ".join(sorted(missing)),
            )

    def _warn_if_budget_exceeds_account(self, account: AccountState) -> None:
        if self.settings.initial_budget > account.equity:
            log.warning(
                "El presupuesto inicial (%s) supera el valor de la cuenta (%s). Los "
                "límites de riesgo se calculan sobre el valor real, que es menor.",
                money(self.settings.initial_budget, self.currency_symbol),
                money(account.equity, self.currency_symbol),
            )

    def _record_unexecuted_order(
        self, cycle_id, portfolio_id, symbol, side, verdict,
        decision_id, risk_event_id, market_open,
    ) -> None:
        reason = _DRY_RUN_REASON if self.settings.dry_run else _MARKET_CLOSED_REASON
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
            log.warning("No se pudo guardar la decisión de %s: %s", proposal.symbol, exc)
            return None

    def _report_positions_without_price(
        self,
        report: CycleReport,
        portfolio_id: str,
        cycle_id: str,
        tracked: dict[str, dict],
        snapshots: dict[str, MarketSnapshot],
    ) -> None:
        """Reports the open positions this cycle got no price for.

        ⚠️ **It is the quietest failure in the whole cycle, and until now it left
        no trace at all.** An open position with no snapshot —Yahoo stops serving
        the symbol, a local holiday closes one exchange of the six, a suffix goes
        bad— is left:

          * **valued at its entry price**, because `SimBroker._mark` falls back to
            it, so its P&L shows 0 as if it had not moved;
          * **with its stop unwatched**, because `mandatory_exits` compares that
            same frozen price against the stop, and it can never breach it;
          * **unreviewed by the model**, because the discretionary pass skips a
            symbol with no snapshot.

        Three things go wrong at once and the cycle used to finish `completed`
        with nothing said. The screen was honest —it labels the position `SIN
        PRECIO`— but the history was not, and the history is what gets read
        afterwards.

        **It is not closed automatically**, and that is deliberate: selling blind
        —at a price we precisely do not have— would be worse than holding. What
        changes is that it now shouts.
        """
        missing = sorted(symbol for symbol in tracked if symbol not in snapshots)
        if not missing:
            return

        report.positions_without_price = missing
        log.warning(
            "SIN PRECIO en %d posiciones abiertas: %s. No se ha podido "
            "comprobar su stop ni revisar su tesis en este ciclo; se valoran a su "
            "precio de entrada.",
            len(missing), ", ".join(missing),
        )
        for symbol in missing:
            # `rejected` because nothing could be approved for it, and because it
            # is the only other value `risk_events.verdict` admits: the CHECK has
            # two, and SQLite cannot alter a constraint (the lesson of F6.9). It
            # lands in the Riesgo screen and in the rejections-by-rule chart,
            # which is exactly where an absence like this has to show up.
            self._save_risk_event(
                cycle_id, portfolio_id, symbol,
                _rejection(
                    "no_price",
                    "Sin cotización en este ciclo: no se comprueba el stop ni se "
                    "revisa la tesis, y la posición se valora a su precio de entrada.",
                ),
                None,
            )

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
    return "n/d" if value is None else fmt_number(value)
