"""Market calendars. A registry, not a hardcoded exchange.

It is needed from the moment the agent decides during the session: without it the
scheduler would run cycles at 3 in the morning over stale data, and at weekends
it would burn model quota analysing bars identical to Friday's.

Until the previous version this module *was* NYSE: the time zone, the hours and
the holiday table were module constants. Now there is a registry of markets
(`MARKETS`) and each profile picks its own in `agent_settings.market`, which is
what is coherent with F6: everything that defines an experiment lives in the
profile.

Three decisions worth understanding:

  * **The market is a keyword argument defaulting to `us`.** That is not
    laziness: production code always passes it explicitly (the cycle and the
    ingestor take it from the profile), and the default exists so the American
    calendar's tests —which are the ones that fix `should_run`'s semantics— still
    read without noise.
  * **The European table only carries the closures common to all its exchanges.**
    Xetra closes on Whit Monday and Milan on Epiphany, but the rest open. Marking
    those days as "no session" would cost the other 60 symbols a whole session;
    leaving them as a market day makes those stocks turn up with no data that
    day, which is exactly what the ingestor already knows how to handle
    (`result.empty`). The visible, bounded failure is preferred.
  * **The currency lives here.** There is no conversion anywhere in the project:
    a book is in its market's currency and that is that. That is why the European
    universe is euro-zone only —mixing in London, which quotes in pence, would
    make `min_order_notional` mean different things per symbol with nothing
    warning about it.

No dependencies: the holidays go in a table because `pandas_market_calendars`
would drag in half a library for what here is thirty lines. The price is that the
table has to be maintained; `last_covered_year` marks how far it reaches, and the
functions warn instead of lying once that date is passed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

#: An arbitrary date for doing arithmetic with bare times. Its value is unused.
_ANY_DAY = date(2000, 1, 1)


def _minutes_between(start: time, end: time) -> int:
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def _shift(moment: time, minutes: int) -> time:
    """Adds minutes to a time of day.

    It leans on `datetime` instead of adding by hand because `time` supports no
    arithmetic. A window crossing midnight would wrap around silently; none of
    the registry's markets comes close, and `_check_markets` verifies it when the
    module is imported.
    """
    if not minutes:
        return moment
    return (datetime.combine(_ANY_DAY, moment) + timedelta(minutes=minutes)).time()


@dataclass(frozen=True)
class Market:
    """A market: when it opens, when it closes and which currency it quotes in."""

    code: str
    label: str
    tz: ZoneInfo
    open_time: time
    close_time: time
    currency: str
    currency_symbol: str
    #: Default benchmark index for the profile (`agent_settings.benchmark`).
    benchmark: str
    #: Universe file proposed for this market.
    universe_file: str
    #: Screener liquidity floor (`agent_settings.screener_min_turnover`) for
    #: a profile of this market, **in the market's currency**: the screener
    #: multiplies price by volume and converts nothing. It lives here because the
    #: number is not a user preference but a property of the universe: 20 M works
    #: for the S&P 500 and leaves out 15 of the 89 European ones.
    min_turnover: float
    #: Exchange suffixes Yahoo uses for this market. Empty = symbols with no
    #: suffix (United States). It is what allows detecting a profile with symbols
    #: from the wrong market before it starts trading.
    symbol_suffixes: frozenset[str]
    holidays: frozenset[date]
    #: Days with an early close and their time. Empty if the market has none.
    early_closes: Mapping[date, time]
    last_covered_year: int
    #: Minutes AFTER the open at which the system starts working. The first
    #: minutes of a session are the opening auction and the gaps: the noisiest
    #: bars of the day and the worst to decide on.
    warmup_minutes: int = 0
    #: Minutes AFTER the close during which the system keeps working. The
    #: session's last bar does not arrive at the instant of the close, and if the
    #: feed comes delayed (R1) it can take considerably longer.
    drain_minutes: int = 0

    def close_time_for(self, day: date) -> time:
        return self.early_closes.get(day, self.close_time)

    # -- Operating window --------------------------------------------------
    #
    # It is NOT the session, and the difference matters: `is_session_open` answers
    # "is the exchange open" and has to keep telling the truth —the dashboard
    # consults it and it is stored in `cycles.market_open`—, while `is_operating`
    # answers "is the system working now". For the euro zone that is 09:15-17:45
    # against a session of 09:00-17:30.
    #
    # They are stored as offsets and not as absolute times so a half session
    # drags its window along: with fixed times, on 24 December in New York the
    # system would go on waiting for bars until 16:00 of a session that closed at
    # 13:00.

    @property
    def operating_open(self) -> time:
        return _shift(self.open_time, self.warmup_minutes)

    def operating_close_for(self, day: date) -> time:
        return _shift(self.close_time_for(day), self.drain_minutes)

    @property
    def operating_close(self) -> time:
        """Close of the window on a normal day, for display."""
        return _shift(self.close_time, self.drain_minutes)

    def owns_symbol(self, symbol: str) -> bool:
        """True if the symbol belongs to this market, by its suffix.

        Yahoo tells the exchange apart with a suffix (`SAN.MC`) and leaves the
        American ones without one (`AAPL`, and `BRK-B` with a hyphen where the
        index puts a dot). This is not a cosmetic validation: a European profile
        with `AAPL` inside would request that symbol during the European session,
        when New York has been closed for hours, and would merely return the stale
        bar from the previous day.
        """
        clean = symbol.strip().upper()
        if not self.symbol_suffixes:
            return "." not in clean
        return any(clean.endswith(suffix) for suffix in self.symbol_suffixes)

    def foreign_symbols(self, symbols) -> list[str]:
        """The ones that are NOT of this market, in order, so they can be named."""
        return [s for s in symbols if not self.owns_symbol(s)]

    @property
    def session_minutes(self) -> int:
        """Minutes of regular session. The ingestor uses it for sizing."""
        return _minutes_between(self.open_time, self.close_time)

    @property
    def operating_minutes(self) -> int:
        """Minutes of operating window on a normal day.

        It coincides with `session_minutes` when warm-up and drain are equal,
        which is the case for today's two markets. It is computed anyway because
        depending on that coincidence would make changing either of the two
        numbers break something far away and in silence.
        """
        return _minutes_between(self.operating_open, self.operating_close)


# ----------------------------------------------------------------------
# Estados Unidos: NYSE / Nasdaq
# ----------------------------------------------------------------------

# Holidays with the market closed. When they fall at a weekend, NYSE moves them
# to the preceding Friday or the following Monday; the dates below are already the
# observed ones, not the nominal ones.
_US_HOLIDAYS = frozenset({
    # 2025
    date(2025, 1, 1), date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4),
    date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
})

# Media sesion: cierre a las 13:00 ET. Vispera de Independencia, dia siguiente a
# Accion de Gracias y vispera de Navidad.
_US_EARLY_CLOSE = time(13, 0)
_US_EARLY_CLOSES = MappingProxyType({
    day: _US_EARLY_CLOSE
    for day in (
        date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
        date(2026, 11, 27), date(2026, 12, 24),
        date(2027, 11, 26),
    )
})

US = Market(
    code="us",
    label="NYSE / Nasdaq",
    tz=ZoneInfo("America/New_York"),
    open_time=time(9, 30),
    close_time=time(16, 0),
    currency="USD",
    currency_symbol="$",
    benchmark="SPY",
    universe_file="universe/sp500.txt",
    # 20 M USD/day: it is the schema's historical default and where the figure
    # came from. With the S&P 500 it discards practically nothing.
    min_turnover=20_000_000.0,
    symbol_suffixes=frozenset(),
    holidays=_US_HOLIDAYS,
    early_closes=_US_EARLY_CLOSES,
    last_covered_year=2027,
    # No warm-up and no drain: the operating window coincides with the session.
    # That is deliberate —nobody has asked to change the American behaviour, and
    # doing it as a side effect would alter an experiment in flight—. The reason
    # for the European drain, besides, is the lag of Yahoo's feed in Europe (R1),
    # which does not apply here.
    warmup_minutes=0,
    drain_minutes=0,
)


# ----------------------------------------------------------------------
# Zona euro: Xetra, Euronext, BME, Borsa Italiana, Nasdaq Helsinki
# ----------------------------------------------------------------------

# The five exchanges of the European universe share a continuous 09:00-17:30
# CET/CEST schedule, so a single calendar covers them. Only the closures ALL of
# them share are here; the ones specific to one exchange (Whit Monday on Xetra,
# Epiphany in Milan, Independence Day in Helsinki) are deliberately left out: see
# the second decision in the docstring.
#
# Unlike NYSE, holidays here are NOT moved when they fall at a weekend: there is
# simply no extra closure. That is why dates one would expect are missing (1 May
# 2027 is a Saturday, and so is 26 December 2026).
_EU_HOLIDAYS = frozenset({
    # 2025 — Pascua el 20 de abril
    date(2025, 1, 1),    # Ano Nuevo
    date(2025, 4, 18),   # Viernes Santo
    date(2025, 4, 21),   # Lunes de Pascua
    date(2025, 5, 1),    # Dia del Trabajo
    date(2025, 12, 24),  # Nochebuena
    date(2025, 12, 25),  # Navidad
    date(2025, 12, 26),  # San Esteban
    date(2025, 12, 31),  # Nochevieja
    # 2026 — Pascua el 5 de abril
    date(2026, 1, 1),
    date(2026, 4, 3),
    date(2026, 4, 6),
    date(2026, 5, 1),
    date(2026, 12, 24),
    date(2026, 12, 25),
    date(2026, 12, 31),
    # 2027 — Pascua el 28 de marzo
    date(2027, 1, 1),
    date(2027, 3, 26),
    date(2027, 3, 29),
    date(2027, 12, 24),
    date(2027, 12, 31),
})

# Christmas Eve and New Year's Eve are treated as full closures, not as half
# sessions. It is not exact —Xetra, BME and Borsa Italiana close, but Euronext
# runs an auction until 14:05— and it is deliberate: a half session with holiday
# liquidity produces bars that distort the indicators more than they contribute.
# That is why both dates are up in HOLIDAYS, and this map stays empty.
_EU_EARLY_CLOSES: Mapping[date, time] = MappingProxyType({})

EU = Market(
    code="eu",
    label="Zona euro (Xetra, Euronext, BME, Borsa Italiana, Helsinki)",
    tz=ZoneInfo("Europe/Madrid"),
    open_time=time(9, 0),
    close_time=time(17, 30),
    currency="EUR",
    currency_symbol="€",
    # The iShares ETF on the EURO STOXX 50, which is the natural equivalent of
    # SPY here: it trades on Xetra, in euros and on the universe's own schedule.
    benchmark="EXW1.DE",
    universe_file="universe/eurostoxx50_ibex35.txt",
    # 5 M EUR/day. Measured on 2026-08-08 over the last 20 sessions: with the
    # 20 M default, 15 of the 89 drop out —ANE.MC, LOG.MC, COL.MC, PUIG.MC,
    # FDR.MC, ROVI.MC, SCYR.MC, MAP.MC...—, which are precisely the Spanish
    # mid-caps the IBEX was added for. With 5 M all 89 pass: the least liquid
    # trades 5.4 M EUR/day, so the threshold still filters for real instead of
    # sitting below everything.
    min_turnover=5_000_000.0,
    # The universe's six exchanges. The ones not quoting in euros are absent on
    # purpose: .L (London, in pence), .SW (Zurich), .ST (Stockholm), .CO, .OL.
    symbol_suffixes=frozenset({".MC", ".PA", ".DE", ".AS", ".MI", ".BR", ".HE"}),
    holidays=_EU_HOLIDAYS,
    early_closes=_EU_EARLY_CLOSES,
    last_covered_year=2027,
    # Operating window 09:15-17:45, explicitly requested.
    #   * The first 15 minutes are let go: they are the hangover of the opening
    #     auction and the overnight gaps, the noisiest bars of the day.
    #   * The last 15 are gained: the closing auction crosses around 17:35 and the
    #     17:29 bar does not appear at the instant of the close. If the European
    #     feed's lag is confirmed (R1 / F2.1c), stopping at 17:30 would lose the
    #     last quarter of an hour of EVERY session.
    warmup_minutes=15,
    drain_minutes=15,
)


# ----------------------------------------------------------------------
# Registro
# ----------------------------------------------------------------------

MARKETS: Mapping[str, Market] = MappingProxyType({US.code: US, EU.code: EU})
DEFAULT_MARKET = US.code


def _check_markets(markets=None) -> None:
    """The registry's invariants, checked on import.

    They are mistakes made while editing the table by hand that afterwards give
    no symptom: an empty operating window, or one crossing midnight, does not blow
    up, it merely makes the system work —or stop working— at hours nobody chose.

    It accepts a list so it can be tested against a made-up market: `MARKETS` is
    read-only on purpose and does not let itself be patched.
    """
    for mkt in (MARKETS.values() if markets is None else markets):
        if mkt.open_time >= mkt.close_time:
            raise ValueError(f"{mkt.code}: la sesion cierra antes de abrir.")
        if mkt.min_turnover <= 0:
            # A 0 does not blow up: it switches the screener's liquidity filter
            # off without saying so, and the agent starts analysing stocks that
            # cannot be bought at the book's size.
            raise ValueError(f"{mkt.code}: min_turnover tiene que ser positivo.")
        if mkt.warmup_minutes < 0 or mkt.drain_minutes < 0:
            raise ValueError(f"{mkt.code}: los desplazamientos van hacia adelante.")
        if mkt.operating_open >= mkt.operating_close:
            raise ValueError(
                f"{mkt.code}: la ventana operativa "
                f"({mkt.operating_open:%H:%M}-{mkt.operating_close:%H:%M}) esta "
                "vacia o cruza la medianoche."
            )
        # The warm-up cannot eat the whole session, not even a half one.
        for day, early in mkt.early_closes.items():
            if _shift(early, mkt.drain_minutes) <= mkt.operating_open:
                raise ValueError(
                    f"{mkt.code}: el {day} cierra a las {early:%H:%M} y la ventana "
                    "operativa quedaria vacia."
                )


_check_markets()


class UnknownMarket(ValueError):
    """The market code is not in the registry."""


def get_market(market: str | Market | None = None) -> Market:
    """Resolves a code to its `Market`. Accepts a `Market` already, for convenience."""
    if isinstance(market, Market):
        return market
    code = (market or DEFAULT_MARKET).strip().lower()
    try:
        return MARKETS[code]
    except KeyError as exc:
        raise UnknownMarket(
            f"Mercado desconocido: {code!r}. Los disponibles son "
            + ", ".join(f"{c!r} ({m.label})" for c, m in MARKETS.items())
        ) from exc


# Aliases of the American market. They exist because the rest of the project and
# its tests used them as module constants before there was a registry; they are
# kept so as not to rewrite forty asserts that are still correct.
EASTERN = US.tz
OPEN_TIME = US.open_time
CLOSE_TIME = US.close_time
EARLY_CLOSE_TIME = _US_EARLY_CLOSE
LAST_COVERED_YEAR = US.last_covered_year
HOLIDAYS = US.holidays
EARLY_CLOSES = frozenset(US.early_closes)


# ----------------------------------------------------------------------
# Consultas
# ----------------------------------------------------------------------

def now_local(market: str | Market | None = None) -> datetime:
    """Hora actual en la zona del mercado."""
    return datetime.now(get_market(market).tz)


def now_eastern() -> datetime:
    """Compatibilidad: la hora en Nueva York."""
    return now_local(US)


def _localize(moment: datetime | None, mkt: Market) -> datetime:
    if moment is None:
        return datetime.now(mkt.tz)
    if moment.tzinfo is None:
        # A datetime with no zone is read as the market's local time, not as UTC:
        # assuming UTC would shift the sessions by several hours without warning.
        return moment.replace(tzinfo=mkt.tz)
    return moment.astimezone(mkt.tz)


def _warn_if_uncovered(day: date, mkt: Market) -> None:
    if day.year > mkt.last_covered_year:
        log.warning(
            "El calendario de festivos de %s solo cubre hasta %d; %s se evalua "
            "solo por el dia de la semana. Actualiza la tabla en "
            "src/market_calendar.py.",
            mkt.code, mkt.last_covered_year, day,
        )


def is_trading_day(day: date, *, market: str | Market | None = None) -> bool:
    """True if there is a session that day (neither weekend nor holiday)."""
    mkt = get_market(market)
    _warn_if_uncovered(day, mkt)
    if day.weekday() >= 5:
        return False
    return day not in mkt.holidays


def close_time_for(day: date, *, market: str | Market | None = None) -> time:
    return get_market(market).close_time_for(day)


def is_session_open(
    moment: datetime | None = None, *, market: str | Market | None = None
) -> bool:
    """True if the market is open at that instant."""
    mkt = get_market(market)
    local = _localize(moment, mkt)
    if not is_trading_day(local.date(), market=mkt):
        return False
    return mkt.open_time <= local.time() < mkt.close_time_for(local.date())


def is_operating(
    moment: datetime | None = None, *, market: str | Market | None = None
) -> bool:
    """True if the system should be working at that instant.

    Deliberately different from `is_session_open`: that one says whether the
    exchange is open —a market datum, stored in the history and shown in the
    dashboard— and this one says whether it is our turn to capture prices and
    analyse. For the euro zone, the session is 09:00-17:30 and the window
    09:15-17:45.
    """
    mkt = get_market(market)
    local = _localize(moment, mkt)
    if not is_trading_day(local.date(), market=mkt):
        return False
    return mkt.operating_open <= local.time() < mkt.operating_close_for(local.date())


def next_operating_open(
    moment: datetime | None = None, *, market: str | Market | None = None
) -> datetime:
    """Next start of the operating window.

    It is what the ingestor uses to sleep. With `next_session_open` it would wake
    up 15 minutes before having anything to do and spend that time asking for
    auction bars.
    """
    mkt = get_market(market)
    local = _localize(moment, mkt)
    day = local.date()
    if is_trading_day(day, market=mkt) and local.time() < mkt.operating_open:
        return datetime.combine(day, mkt.operating_open, tzinfo=mkt.tz)
    for _ in range(1, 12):
        day += timedelta(days=1)
        if is_trading_day(day, market=mkt):
            return datetime.combine(day, mkt.operating_open, tzinfo=mkt.tz)
    raise RuntimeError(
        f"No se encontro ninguna sesion de {mkt.code} en los proximos 12 dias."
    )


def operating_bounds(
    day: date, *, market: str | Market | None = None
) -> tuple[datetime, datetime] | None:
    """Start and end of that day's operating window, or None if there is no session."""
    mkt = get_market(market)
    if not is_trading_day(day, market=mkt):
        return None
    return (
        datetime.combine(day, mkt.operating_open, tzinfo=mkt.tz),
        datetime.combine(day, mkt.operating_close_for(day), tzinfo=mkt.tz),
    )


def session_bounds(
    day: date, *, market: str | Market | None = None
) -> tuple[datetime, datetime] | None:
    """That day's session open and close, or None if there is no session."""
    mkt = get_market(market)
    if not is_trading_day(day, market=mkt):
        return None
    return (
        datetime.combine(day, mkt.open_time, tzinfo=mkt.tz),
        datetime.combine(day, mkt.close_time_for(day), tzinfo=mkt.tz),
    )


def last_trading_day(
    moment: datetime | None = None, *, market: str | Market | None = None
) -> date:
    """Last day with a session, counting today if it has already opened."""
    mkt = get_market(market)
    local = _localize(moment, mkt)
    day = local.date()
    if is_trading_day(day, market=mkt) and local.time() >= mkt.open_time:
        return day
    day -= timedelta(days=1)
    for _ in range(10):
        if is_trading_day(day, market=mkt):
            return day
        day -= timedelta(days=1)
    # Ten days in a row with no session does not happen; if it does, the table is wrong.
    raise RuntimeError(
        f"No se encontro ningun dia de mercado de {mkt.code} en los ultimos 10 dias."
    )


def next_session_open(
    moment: datetime | None = None, *, market: str | Market | None = None
) -> datetime:
    """Proxima apertura, para poder decir cuanto falta."""
    mkt = get_market(market)
    local = _localize(moment, mkt)
    day = local.date()
    if is_trading_day(day, market=mkt) and local.time() < mkt.open_time:
        return datetime.combine(day, mkt.open_time, tzinfo=mkt.tz)
    for _ in range(1, 12):
        day += timedelta(days=1)
        if is_trading_day(day, market=mkt):
            return datetime.combine(day, mkt.open_time, tzinfo=mkt.tz)
    raise RuntimeError(
        f"No se encontro ninguna sesion de {mkt.code} en los proximos 12 dias."
    )


def should_run(
    interval: str,
    moment: datetime | None = None,
    *,
    market: str | Market | None = None,
) -> tuple[bool, str]:
    """Decides whether a cycle is worth spending. Returns (run, reason).

    The question is not "is the market open" but **"is there new data to
    analyse"**, and the answer depends on the interval:

      * With daily bars, the natural moment is right AFTER the close, when the
        session is already complete. Demanding an open market would leave out
        precisely the best moment of the day. It only skips when there is no
        session: weekends and holidays, where the bars are identical to the
        previous cycle's.
      * With hourly bars a live session is needed: a new bar every hour is
        exactly what is to be taken advantage of, and outside the session none
        arrive.

    "Live session" here means **operating window**, not market session: in the
    first 15 European minutes nothing is decided on purpose, and in the last 15 it
    is, because that is when the closing bars finish arriving.
    """
    mkt = get_market(market)
    local = _localize(moment, mkt)

    if not is_trading_day(local.date(), market=mkt):
        return False, f"sin sesion: {describe(local, market=mkt)}"

    if interval == "1d":
        # A market day: there is a new bar, whether it is open or already closed.
        return True, f"dia de mercado ({local:%a %d %b}), {describe(local, market=mkt)}"

    if is_operating(local, market=mkt):
        return True, describe(local, market=mkt)
    return (
        False,
        f"barras de {interval} necesitan sesion viva: {describe(local, market=mkt)}",
    )


def describe(
    moment: datetime | None = None, *, market: str | Market | None = None
) -> str:
    """A sentence for the cycle's startup log."""
    mkt = get_market(market)
    local = _localize(moment, mkt)
    zone = local.strftime("%Z") or mkt.code.upper()
    if is_session_open(local, market=mkt):
        _, close = session_bounds(local.date(), market=mkt)
        remaining = (close - local).total_seconds() / 60
        early = " (media sesion)" if local.date() in mkt.early_closes else ""
        espera = ""
        if not is_operating(local, market=mkt):
            # Session open but still in the warm-up. Without this sentence, the
            # log would say "market open" while the cycle skips itself.
            espera = f", ventana operativa desde las {mkt.operating_open:%H:%M}"
        return (
            f"mercado abierto{early}, cierra en {remaining:.0f} min "
            f"({local:%H:%M} {zone}){espera}"
        )

    if is_operating(local, market=mkt):
        # Closed but inside the drain: this is when the last bars arrive.
        _, fin = operating_bounds(local.date(), market=mkt)
        restante = (fin - local).total_seconds() / 60
        return (
            f"mercado cerrado ({local:%H:%M} {zone}), ventana operativa abierta "
            f"{restante:.0f} min mas (hasta las "
            f"{mkt.operating_close_for(local.date()):%H:%M})"
        )

    upcoming = next_session_open(local, market=mkt)
    hours = (upcoming - local).total_seconds() / 3600
    return (
        f"mercado cerrado ({local:%a %H:%M} {zone}), abre en {hours:.1f} h "
        f"el {upcoming:%a %d %b %H:%M} {zone}"
    )
