"""Calendarios de mercado. Un registro, no un mercado cableado.

Hace falta desde el momento en que el agente decide durante la sesion: sin esto
el planificador ejecutaria ciclos a las 3 de la manana sobre datos rancios, y los
fines de semana gastaria cuota del modelo analizando barras identicas a las del
viernes.

Hasta la version anterior este modulo *era* NYSE: la zona horaria, el horario y
la tabla de festivos estaban en constantes de modulo. Ahora hay un registro de
mercados (`MARKETS`) y cada perfil elige el suyo en `agent_settings.market`, que
es lo coherente con F6: todo lo que define un experimento vive en el perfil.

Tres decisiones que conviene entender:

  * **El mercado es un argumento de palabra clave con `us` por defecto.** No es
    pereza: el codigo de produccion lo pasa siempre explicito (el ciclo y el
    ingestor lo sacan del perfil), y el valor por defecto existe para que los
    tests del calendario americano —que son los que fijan la semantica de
    `should_run`— sigan leyendose sin ruido.
  * **La tabla europea solo lleva los cierres comunes a todas sus bolsas.**
    Xetra cierra el Lunes de Pentecostes y Milan la Epifania, pero las demas
    abren. Marcar esos dias como "sin sesion" costaria una sesion entera a los
    otros 60 simbolos; dejarlos como dia de mercado hace que esos valores
    aparezcan sin datos ese dia, que es exactamente lo que el ingestor ya sabe
    tratar (`resultado.vacios`). Se prefiere el fallo visible y acotado.
  * **La divisa vive aqui.** No hay conversion en ninguna parte del proyecto:
    una cartera esta en la moneda de su mercado y punto. Por eso el universo
    europeo es solo de la zona euro —mezclar Londres, que cotiza en peniques,
    haria que `min_order_notional` significara cosas distintas segun el simbolo
    sin que nada avisara.

Sin dependencias: los festivos van en una tabla porque `pandas_market_calendars`
arrastraria media libreria para lo que aqui son treinta lineas. El precio es que
la tabla hay que mantenerla; `last_covered_year` marca hasta donde llega, y las
funciones avisan en lugar de mentir cuando se pasa esa fecha.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

#: Fecha cualquiera para hacer aritmetica con horas sueltas. No se usa su valor.
_ANY_DAY = date(2000, 1, 1)


def _minutes_between(start: time, end: time) -> int:
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def _shift(moment: time, minutes: int) -> time:
    """Suma minutos a una hora del dia.

    Se apoya en `datetime` en lugar de sumar a mano porque `time` no soporta
    aritmetica. Una ventana que cruzara la medianoche daria la vuelta en
    silencio; ninguno de los mercados del registro se acerca, y `_check_markets`
    lo comprueba al importar el modulo.
    """
    if not minutes:
        return moment
    return (datetime.combine(_ANY_DAY, moment) + timedelta(minutes=minutes)).time()


@dataclass(frozen=True)
class Market:
    """Un mercado: cuando abre, cuando cierra y en que moneda cotiza."""

    code: str
    label: str
    tz: ZoneInfo
    open_time: time
    close_time: time
    currency: str
    currency_symbol: str
    #: Indice de referencia por defecto para el perfil (`agent_settings.benchmark`).
    benchmark: str
    #: Fichero de universo que se propone para este mercado.
    universe_file: str
    #: Sufijos de bolsa que Yahoo usa para este mercado. Vacio = simbolos sin
    #: sufijo (Estados Unidos). Es lo que permite detectar un perfil con
    #: simbolos del mercado equivocado antes de que empiece a operar.
    symbol_suffixes: frozenset[str]
    holidays: frozenset[date]
    #: Dias con cierre anticipado y su hora. Vacio si el mercado no los tiene.
    early_closes: Mapping[date, time]
    last_covered_year: int
    #: Minutos DESPUES de la apertura en que el sistema empieza a trabajar. Los
    #: primeros minutos de sesion son la subasta de apertura y los huecos: las
    #: barras mas ruidosas del dia y las peores para decidir sobre ellas.
    warmup_minutes: int = 0
    #: Minutos DESPUES del cierre en que el sistema sigue trabajando. La ultima
    #: barra de la sesion no llega en el instante del cierre, y si el feed viene
    #: con retraso (R1) puede tardar bastante mas.
    drain_minutes: int = 0

    def close_time_for(self, day: date) -> time:
        return self.early_closes.get(day, self.close_time)

    # -- Ventana operativa -------------------------------------------------
    #
    # NO es la sesion, y la diferencia importa: `is_session_open` responde "esta
    # la bolsa abierta" y tiene que seguir diciendo la verdad —lo consulta el
    # dashboard y se guarda en `cycles.market_open`—, mientras que `is_operating`
    # responde "trabaja el sistema ahora". Con la zona euro son 09:15-17:45
    # frente a una sesion de 09:00-17:30.
    #
    # Se guardan como desplazamientos y no como horas absolutas para que una
    # media sesion arrastre su ventana: con horas fijas, el 24 de diciembre en
    # Nueva York el sistema seguiria esperando barras hasta las 16:00 de una
    # sesion que cerro a las 13:00.

    @property
    def operating_open(self) -> time:
        return _shift(self.open_time, self.warmup_minutes)

    def operating_close_for(self, day: date) -> time:
        return _shift(self.close_time_for(day), self.drain_minutes)

    @property
    def operating_close(self) -> time:
        """Cierre de la ventana en un dia normal, para enseñarlo."""
        return _shift(self.close_time, self.drain_minutes)

    def owns_symbol(self, symbol: str) -> bool:
        """True si el simbolo pertenece a este mercado, por su sufijo.

        Yahoo distingue la bolsa con un sufijo (`SAN.MC`) y deja los americanos
        sin el (`AAPL`, y `BRK-B` con guion donde el indice pone punto). No es
        una validacion cosmetica: un perfil europeo con `AAPL` dentro pediria ese
        simbolo durante la sesion europea, cuando Nueva York lleva horas cerrada,
        y se limitaria a devolver la barra rancia del dia anterior.
        """
        clean = symbol.strip().upper()
        if not self.symbol_suffixes:
            return "." not in clean
        return any(clean.endswith(suffix) for suffix in self.symbol_suffixes)

    def foreign_symbols(self, symbols) -> list[str]:
        """Los que NO son de este mercado, en orden, para poder nombrarlos."""
        return [s for s in symbols if not self.owns_symbol(s)]

    @property
    def session_minutes(self) -> int:
        """Minutos de sesion regular. Lo usa el ingestor para dimensionar."""
        return _minutes_between(self.open_time, self.close_time)

    @property
    def operating_minutes(self) -> int:
        """Minutos de ventana operativa en un dia normal.

        Coincide con `session_minutes` cuando calentamiento y cola son iguales,
        que es el caso de los dos mercados de hoy. Se calcula igualmente porque
        depender de esa coincidencia haria que cambiar uno de los dos numeros
        rompiera algo lejos y en silencio.
        """
        return _minutes_between(self.operating_open, self.operating_close)


# ----------------------------------------------------------------------
# Estados Unidos: NYSE / Nasdaq
# ----------------------------------------------------------------------

# Festivos con mercado cerrado. Cuando caen en fin de semana, NYSE los traslada
# al viernes anterior o al lunes siguiente; las fechas de abajo ya son las
# observadas, no las nominales.
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
    symbol_suffixes=frozenset(),
    holidays=_US_HOLIDAYS,
    early_closes=_US_EARLY_CLOSES,
    last_covered_year=2027,
    # Sin calentamiento ni cola: la ventana operativa coincide con la sesion.
    # Es a proposito —nadie ha pedido cambiar el comportamiento americano, y
    # hacerlo de rebote alteraria un experimento en marcha—. El motivo de la
    # cola europea, ademas, es el retraso del feed de Yahoo en Europa (R1), que
    # aqui no aplica.
    warmup_minutes=0,
    drain_minutes=0,
)


# ----------------------------------------------------------------------
# Zona euro: Xetra, Euronext, BME, Borsa Italiana, Nasdaq Helsinki
# ----------------------------------------------------------------------

# Las cinco bolsas del universo europeo comparten horario continuo 09:00-17:30
# CET/CEST, asi que un solo calendario las cubre. Solo estan los cierres que
# comparten TODAS; los propios de una bolsa (Pentecostes en Xetra, Epifania en
# Milan, Dia de la Independencia en Helsinki) se dejan fuera a proposito: ver la
# segunda decision del docstring.
#
# A diferencia de NYSE, aqui los festivos NO se trasladan cuando caen en fin de
# semana: simplemente no hay cierre extra. Por eso faltan fechas que uno
# esperaria (el 1 de mayo de 2027 es sabado, el 26 de diciembre de 2026 tambien).
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

# Nochebuena y Nochevieja se tratan como cierre completo, no como media sesion.
# No es exacto —Xetra, BME y Borsa Italiana cierran, pero Euronext hace subasta
# hasta las 14:05— y es deliberado: media sesion con liquidez de festivo produce
# barras que distorsionan los indicadores mas de lo que aportan. Por eso las dos
# fechas estan arriba, en HOLIDAYS, y este mapa queda vacio.
_EU_EARLY_CLOSES: Mapping[date, time] = MappingProxyType({})

EU = Market(
    code="eu",
    label="Zona euro (Xetra, Euronext, BME, Borsa Italiana, Helsinki)",
    tz=ZoneInfo("Europe/Madrid"),
    open_time=time(9, 0),
    close_time=time(17, 30),
    currency="EUR",
    currency_symbol="€",
    # El ETF de iShares sobre el EURO STOXX 50, que es el equivalente natural de
    # SPY aqui: cotiza en Xetra, en euros y con el mismo horario que el universo.
    benchmark="EXW1.DE",
    universe_file="universe/eurostoxx50_ibex35.txt",
    # Las seis bolsas del universo. Faltan a proposito las que no cotizan en
    # euros: .L (Londres, en peniques), .SW (Zurich), .ST (Estocolmo), .CO, .OL.
    symbol_suffixes=frozenset({".MC", ".PA", ".DE", ".AS", ".MI", ".BR", ".HE"}),
    holidays=_EU_HOLIDAYS,
    early_closes=_EU_EARLY_CLOSES,
    last_covered_year=2027,
    # Ventana operativa 09:15-17:45, pedida explicitamente.
    #   * Los 15 primeros minutos se dejan pasar: son la resaca de la subasta de
    #     apertura y los huecos de la noche, las barras mas ruidosas del dia.
    #   * Los 15 ultimos se ganan: la subasta de cierre se cruza sobre las 17:35
    #     y la barra de las 17:29 no aparece en el instante del cierre. Si se
    #     confirma el retraso del feed europeo (R1 / F2.1c), parar a las 17:30
    #     perderia el ultimo cuarto de hora de CADA sesion.
    warmup_minutes=15,
    drain_minutes=15,
)


# ----------------------------------------------------------------------
# Registro
# ----------------------------------------------------------------------

MARKETS: Mapping[str, Market] = MappingProxyType({US.code: US, EU.code: EU})
DEFAULT_MARKET = US.code


def _check_markets(markets=None) -> None:
    """Invariantes del registro, comprobadas al importar.

    Son errores que se cometen editando la tabla a mano y que despues no dan
    sintoma: una ventana operativa vacia o que cruza la medianoche no revienta,
    solo hace que el sistema trabaje —o deje de hacerlo— en horas que nadie
    eligio.

    Acepta una lista para poder probarla sobre un mercado inventado: `MARKETS`
    es de solo lectura a proposito y no se deja parchear.
    """
    for mkt in (MARKETS.values() if markets is None else markets):
        if mkt.open_time >= mkt.close_time:
            raise ValueError(f"{mkt.code}: la sesion cierra antes de abrir.")
        if mkt.warmup_minutes < 0 or mkt.drain_minutes < 0:
            raise ValueError(f"{mkt.code}: los desplazamientos van hacia adelante.")
        if mkt.operating_open >= mkt.operating_close:
            raise ValueError(
                f"{mkt.code}: la ventana operativa "
                f"({mkt.operating_open:%H:%M}-{mkt.operating_close:%H:%M}) esta "
                "vacia o cruza la medianoche."
            )
        # El calentamiento no puede comerse la sesion entera, ni en media sesion.
        for day, early in mkt.early_closes.items():
            if _shift(early, mkt.drain_minutes) <= mkt.operating_open:
                raise ValueError(
                    f"{mkt.code}: el {day} cierra a las {early:%H:%M} y la ventana "
                    "operativa quedaria vacia."
                )


_check_markets()


class UnknownMarket(ValueError):
    """El codigo de mercado no esta en el registro."""


def get_market(market: str | Market | None = None) -> Market:
    """Resuelve un codigo a su `Market`. Acepta ya un `Market` para comodidad."""
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


# Alias del mercado americano. Existen porque el resto del proyecto y sus tests
# los usaban como constantes de modulo antes de que hubiera registro; se
# conservan para no reescribir cuarenta asserts que siguen siendo correctos.
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
        # Un datetime sin zona se interpreta como hora local del mercado, no como
        # UTC: asumir UTC desplazaria las sesiones varias horas sin avisar.
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
    """True si hay sesion ese dia (ni fin de semana ni festivo)."""
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
    """True si el mercado esta abierto en ese instante."""
    mkt = get_market(market)
    local = _localize(moment, mkt)
    if not is_trading_day(local.date(), market=mkt):
        return False
    return mkt.open_time <= local.time() < mkt.close_time_for(local.date())


def is_operating(
    moment: datetime | None = None, *, market: str | Market | None = None
) -> bool:
    """True si el sistema debe estar trabajando en ese instante.

    Distinta de `is_session_open` a proposito: aquella dice si la bolsa esta
    abierta —dato de mercado, que se guarda en el historico y se enseña en el
    dashboard— y esta dice si nos toca capturar precios y analizar. Con la zona
    euro, la sesion es 09:00-17:30 y la ventana 09:15-17:45.
    """
    mkt = get_market(market)
    local = _localize(moment, mkt)
    if not is_trading_day(local.date(), market=mkt):
        return False
    return mkt.operating_open <= local.time() < mkt.operating_close_for(local.date())


def next_operating_open(
    moment: datetime | None = None, *, market: str | Market | None = None
) -> datetime:
    """Proximo arranque de la ventana operativa.

    Es lo que el ingestor usa para dormir. Con `next_session_open` se despertaria
    15 minutos antes de tener nada que hacer y se pasaria ese rato pidiendo
    barras de la subasta.
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
    """Inicio y fin de la ventana operativa de ese dia, o None si no hay sesion."""
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
    """Apertura y cierre de la sesion de ese dia, o None si no hay sesion."""
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
    """Ultimo dia con sesion, contando hoy si ya ha abierto."""
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
    # Diez dias seguidos sin sesion no ocurre; si ocurre, la tabla esta mal.
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
    """Decide si merece la pena gastar un ciclo. Devuelve (ejecutar, motivo).

    La pregunta no es "esta el mercado abierto" sino **"hay datos nuevos que
    analizar"**, y la respuesta depende del intervalo:

      * Con barras diarias, el momento natural es justo DESPUES del cierre, cuando
        la sesion ya esta completa. Exigir mercado abierto dejaria fuera
        precisamente el mejor momento del dia. Solo se salta si no hay sesion:
        fines de semana y festivos, donde las barras son identicas a las del
        ciclo anterior.
      * Con barras horarias hace falta la sesion viva: una barra nueva cada hora
        es justamente lo que se quiere aprovechar, y fuera de sesion no llegan.

    "Sesion viva" significa aqui **ventana operativa**, no sesion de mercado: en
    los 15 primeros minutos europeos no se decide a proposito, y en los 15
    ultimos si, porque es cuando terminan de llegar las barras del cierre.
    """
    mkt = get_market(market)
    local = _localize(moment, mkt)

    if not is_trading_day(local.date(), market=mkt):
        return False, f"sin sesion: {describe(local, market=mkt)}"

    if interval == "1d":
        # Dia de mercado: hay barra nueva, este abierto o ya cerrado.
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
    """Frase para el log de arranque del ciclo."""
    mkt = get_market(market)
    local = _localize(moment, mkt)
    zone = local.strftime("%Z") or mkt.code.upper()
    if is_session_open(local, market=mkt):
        _, close = session_bounds(local.date(), market=mkt)
        remaining = (close - local).total_seconds() / 60
        early = " (media sesion)" if local.date() in mkt.early_closes else ""
        espera = ""
        if not is_operating(local, market=mkt):
            # Sesion abierta pero todavia en el calentamiento. Sin esta frase, el
            # log diria "mercado abierto" mientras el ciclo se salta a si mismo.
            espera = f", ventana operativa desde las {mkt.operating_open:%H:%M}"
        return (
            f"mercado abierto{early}, cierra en {remaining:.0f} min "
            f"({local:%H:%M} {zone}){espera}"
        )

    if is_operating(local, market=mkt):
        # Cerrado pero dentro de la cola: es cuando llegan las ultimas barras.
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
