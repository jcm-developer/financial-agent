"""The market as a parameter of the profile: European calendar, registry and universe.

What gets tested here are the silent failures, which in this part of the project
are nearly all of them. A misconfigured European calendar does not blow up: it
makes the ingestor ask for bars with the exchange closed and store the previous
close over and over, and three days later there is a history full of data that
looks fine. The same goes for an American symbol inside a European profile.

Every date is fixed, never the clock.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src import market_calendar as mc
from src.config import ConfigError, Infra
from src.profile_settings import import_env_profile, resolve_settings

MADRID = ZoneInfo("Europe/Madrid")

INFRA = Infra(
    db_path="data/no-se-usa.db",
    log_level="CRITICAL",
    model_api_key="clave-de-prueba",
    model_base_url="http://stub",
)


def eu(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=MADRID)


# -- Registro de mercados ----------------------------------------------------


def test_the_registry_brings_both_markets():
    assert set(mc.MARKETS) == {"us", "eu"}
    assert mc.get_market("eu") is mc.EU
    assert mc.get_market("US") is mc.US, "el codigo no distingue mayusculas"
    assert mc.get_market() is mc.US, "sin argumento manda DEFAULT_MARKET"


def test_an_unknown_market_says_which_ones_exist():
    """The message matters: 'eur' instead of 'eu' is the likely typo."""
    with pytest.raises(mc.UnknownMarket) as exc:
        mc.get_market("eur")

    assert "eur" in str(exc.value)
    assert "'eu'" in str(exc.value)


def test_the_currencies_do_not_mix():
    """Each market carries its own currency because the project converts none."""
    assert mc.US.currency == "USD"
    assert mc.EU.currency == "EUR"


def test_the_european_session_is_longer_than_the_american_one():
    """510 minutes against 390: it is what sizes the volume of bars_1m."""
    assert mc.EU.session_minutes == 510
    assert mc.US.session_minutes == 390


# -- Horario europeo ---------------------------------------------------------


def test_the_european_session_opens_at_nine():
    assert not mc.is_session_open(eu(2026, 8, 5, 8, 59), market="eu")
    assert mc.is_session_open(eu(2026, 8, 5, 9, 0), market="eu")


def test_the_european_session_closes_at_half_past_five():
    assert mc.is_session_open(eu(2026, 8, 5, 17, 29), market="eu")
    assert not mc.is_session_open(eu(2026, 8, 5, 17, 30), market="eu")


def test_the_two_markets_overlap_in_the_afternoon():
    """15:30-17:30 CET is the only stretch with both exchanges open. It is the
    case the ingestor has to know how to serve at once."""
    solape = eu(2026, 8, 5, 16, 0)

    assert mc.is_session_open(solape, market="eu")
    assert mc.is_session_open(solape, market="us")


def test_at_ten_in_the_morning_only_europe_is_open():
    """The reason the user asked for this: the computer is switched on."""
    manana = eu(2026, 8, 5, 10, 0)

    assert mc.is_session_open(manana, market="eu")
    assert not mc.is_session_open(manana, market="us")


def test_at_nine_at_night_only_new_york_is_open():
    noche = eu(2026, 8, 5, 21, 0)

    assert not mc.is_session_open(noche, market="eu")
    assert mc.is_session_open(noche, market="us")


def test_a_datetime_with_no_zone_is_read_in_market_time():
    """It is not read as UTC but as local time of the exchange being asked about.

    17:00 proves it: in Madrid 30 minutes of session are left and in New York the
    session closed an hour ago.
    """
    assert mc.is_session_open(datetime(2026, 8, 5, 17, 0), market="eu")
    assert not mc.is_session_open(datetime(2026, 8, 5, 17, 0), market="us")


# -- Operating window --------------------------------------------------------
#
# The point of this whole section: the window is NOT the session, and confusing
# them is the failure these tests exist to prevent. `is_session_open` is a market
# datum stored in the history; `is_operating` is "the system is working".


def test_the_european_window_runs_from_0915_to_1745():
    assert mc.EU.operating_open == time(9, 15)
    assert mc.EU.operating_close == time(17, 45)


def test_the_first_fifteen_minutes_are_session_but_not_window():
    """The exchange is open and the system is not working: it is the warm-up, so
    as not to decide on the hangover of the opening auction."""
    apertura = eu(2026, 8, 5, 9, 5)

    assert mc.is_session_open(apertura, market="eu")
    assert not mc.is_operating(apertura, market="eu")


def test_the_window_starts_at_0915():
    assert not mc.is_operating(eu(2026, 8, 5, 9, 14), market="eu")
    assert mc.is_operating(eu(2026, 8, 5, 9, 15), market="eu")


def test_the_fifteen_minutes_after_the_close_are_window_but_not_session():
    """The opposite of the warm-up: the exchange has closed and the system goes
    on, because that is when the last bar finishes arriving."""
    cola = eu(2026, 8, 5, 17, 40)

    assert not mc.is_session_open(cola, market="eu")
    assert mc.is_operating(cola, market="eu")


def test_the_window_ends_at_1745():
    assert mc.is_operating(eu(2026, 8, 5, 17, 44), market="eu")
    assert not mc.is_operating(eu(2026, 8, 5, 17, 45), market="eu")


def test_the_window_does_not_open_on_a_day_with_no_session():
    """A holiday has no drain and no warm-up: there is nothing to capture."""
    assert not mc.is_operating(eu(2026, 5, 1, 12, 0), market="eu")
    assert not mc.is_operating(eu(2026, 8, 8, 12, 0), market="eu")


def test_the_american_window_is_still_the_session():
    """Nobody has asked to change the American behaviour, and doing it as a side
    effect would alter an experiment in flight."""
    assert mc.US.warmup_minutes == 0
    assert mc.US.drain_minutes == 0
    assert mc.US.operating_open == mc.US.open_time
    assert mc.US.operating_close == mc.US.close_time


def test_a_half_session_drags_its_window_along():
    """The reason for storing offsets and not absolute times. On 24 December New
    York closes at 13:00; with 17:45 burned in, the system would wait three hours
    for bars of a session that had ended."""
    nochebuena = date(2026, 12, 24)

    assert mc.US.operating_close_for(nochebuena) == time(13, 0)
    assert mc.US.operating_close_for(date(2026, 12, 23)) == time(16, 0)


def test_next_operating_open_points_at_0915_not_0900():
    """It is what the ingestor uses to sleep: waking at 09:00 would mean spending
    fifteen minutes asking for auction bars."""
    upcoming = mc.next_operating_open(eu(2026, 8, 7, 20, 0), market="eu")

    assert upcoming.date() == date(2026, 8, 10)   # el finde por medio
    assert upcoming.timetz().hour == 9
    assert upcoming.timetz().minute == 15


def test_the_window_lasts_as_long_as_the_session():
    """Warm-up and drain are equal, so the number of bars per day does not
    change: 510 in Europe. It matters for the estimated volume of bars_1m."""
    assert mc.EU.operating_minutes == mc.EU.session_minutes == 510
    assert mc.US.operating_minutes == mc.US.session_minutes == 390


def test_operating_bounds_returns_the_days_window():
    inicio, fin = mc.operating_bounds(date(2026, 8, 10), market="eu")

    assert (inicio.hour, inicio.minute) == (9, 15)
    assert (fin.hour, fin.minute) == (17, 45)
    assert mc.operating_bounds(date(2026, 8, 8), market="eu") is None


def test_the_registry_is_validated_on_import():
    """`_check_markets` runs when the module is imported. An empty window, or one
    the wrong way round, does not blow up on its own: it makes the system work at
    hours nobody chose, and that gives no symptom until the data is looked at."""
    roto = dataclasses.replace(mc.EU, warmup_minutes=600)

    assert roto.operating_open >= roto.operating_close
    with pytest.raises(ValueError, match="ventana operativa"):
        mc._check_markets([roto])


# -- Festivos europeos -------------------------------------------------------


@pytest.mark.parametrize("day", [
    date(2026, 1, 1),    # Ano Nuevo
    date(2026, 4, 3),    # Viernes Santo
    date(2026, 4, 6),    # Lunes de Pascua
    date(2026, 5, 1),    # Dia del Trabajo
    date(2026, 12, 24),  # Nochebuena
    date(2026, 12, 25),  # Navidad
    date(2026, 12, 31),  # Nochevieja
])
def test_european_holidays_are_not_market_days(day):
    assert not mc.is_trading_day(day, market="eu")


def test_american_holidays_do_not_close_europe():
    """Thanksgiving is the obvious example: Wall Street closes and Madrid trades
    as normal. Sharing a table would have cost a whole session."""
    accion_de_gracias = date(2026, 11, 26)

    assert not mc.is_trading_day(accion_de_gracias, market="us")
    assert mc.is_trading_day(accion_de_gracias, market="eu")


def test_european_holidays_do_not_close_new_york():
    """1 May 2026 (a Friday) is a holiday across the euro zone and an ordinary
    day in the United States."""
    assert not mc.is_trading_day(date(2026, 5, 1), market="eu")
    assert mc.is_trading_day(date(2026, 5, 1), market="us")


@pytest.mark.parametrize("day, motivo", [
    (date(2026, 5, 25), "Lunes de Pentecostes: cierra Xetra, no las demas"),
    (date(2026, 1, 6), "Epifania: cierra Milan, no las demas"),
])
def test_single_exchange_closures_are_still_market_days(day, motivo):
    """The European table only carries the COMMON closures. Marking these days as
    holidays would cost the other sixty-odd symbols their session; this way the
    affected ones turn up as empty symbols, which the ingestor already handles."""
    assert mc.is_trading_day(day, market="eu"), motivo


def test_europe_does_not_move_weekend_holidays():
    """NYSE moves 4 July to the Friday; Europe moves nothing. 26 December 2026
    falls on a Saturday and generates no extra closure."""
    assert date(2026, 12, 26).weekday() == 5
    assert mc.is_trading_day(date(2026, 12, 28), market="eu")


def test_the_european_table_has_no_weekend_holidays():
    """A weekend date in the table would be harmless but would give away that the
    nominal holiday was copied without checking which day it falls on."""
    assert [d for d in mc.EU.holidays if d.weekday() >= 5] == []


def test_europa_no_tiene_medias_sesiones():
    """Christmas Eve and New Year's Eve are treated as full closures, not as half
    sessions: see the comment on _EU_EARLY_CLOSES."""
    assert dict(mc.EU.early_closes) == {}
    assert not mc.is_trading_day(date(2026, 12, 24), market="eu")


# -- should_run y describe con mercado ---------------------------------------


def test_the_european_daily_cycle_runs_after_the_close():
    """18:00 Madrid time is the equivalent of the American profile's 16:15 ET:
    session over and the day's bar complete."""
    allowed, reason = mc.should_run("1d", eu(2026, 8, 10, 18, 0), market="eu")

    assert allowed
    assert "dia de mercado" in reason


def test_the_european_hourly_cycle_does_not_run_at_night():
    allowed, reason = mc.should_run("1h", eu(2026, 8, 10, 21, 0), market="eu")

    assert not allowed
    assert "sesion viva" in reason


def test_the_same_instant_gives_different_answers_per_market():
    """It is the whole reason for this: at 10:00 CET a European hourly cycle has
    fresh data and an American one does not."""
    momento = eu(2026, 8, 10, 10, 0)

    assert mc.should_run("1h", momento, market="eu")[0]
    assert not mc.should_run("1h", momento, market="us")[0]


def test_describe_european_gives_the_time_in_its_own_zone():
    text = mc.describe(eu(2026, 8, 5, 16, 30), market="eu")

    assert "abierto" in text
    assert "60 min" in text
    assert "16:30" in text
    assert "CEST" in text, "en agosto Madrid va en horario de verano"


def test_describe_american_still_talks_about_new_york():
    text = mc.describe(datetime(2026, 8, 5, 15, 0, tzinfo=mc.EASTERN))

    assert "60 min" in text
    assert "EDT" in text


# -- Pertenencia de simbolos -------------------------------------------------


@pytest.mark.parametrize("symbol", ["SAN.MC", "ASML.AS", "SAP.DE", "MC.PA",
                                    "ISP.MI", "ABI.BR", "NDA-FI.HE"])
def test_symbols_with_a_suffix_are_european(symbol):
    assert mc.EU.owns_symbol(symbol)
    assert not mc.US.owns_symbol(symbol)


@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "BRK-B"])
def test_symbols_without_a_suffix_are_american(symbol):
    """BRK-B carries a hyphen, not a dot: Yahoo uses the hyphen for American
    share classes, so it cannot be confused with an exchange suffix."""
    assert mc.US.owns_symbol(symbol)
    assert not mc.EU.owns_symbol(symbol)


@pytest.mark.parametrize("symbol", ["VOD.L", "NESN.SW", "VOLV-B.ST"])
def test_exchanges_outside_the_euro_do_not_belong_to_eu(symbol):
    """Londres cotiza en peniques, Zurich en francos y Estocolmo en coronas.
    Colarlos en el universo europeo romperia min_order_notional en silencio."""
    assert not mc.EU.owns_symbol(symbol)
    assert not mc.US.owns_symbol(symbol)


def test_foreign_symbols_keeps_the_order_so_they_can_be_named():
    assert mc.EU.foreign_symbols(["SAN.MC", "AAPL", "SAP.DE", "MSFT"]) == [
        "AAPL", "MSFT"
    ]


# -- El fichero de universo --------------------------------------------------


def _cargar_universo() -> list[str]:
    ruta = Path(mc.EU.universe_file)
    if not ruta.is_absolute():
        ruta = Path(__file__).resolve().parent.parent / mc.EU.universe_file
    return [
        line.strip().upper()
        for line in ruta.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_the_european_universe_exists_and_has_the_89_symbols():
    universe = _cargar_universo()

    assert len(universe) == 89
    assert len(set(universe)) == 89, "hay simbolos repetidos"


def test_every_symbol_of_the_universe_belongs_to_the_european_market():
    """It is the check that prevents the most expensive and most silent failure:
    a misspelt ticker gives no error, it is simply left with no data and
    disappears from the analysis."""
    assert mc.EU.foreign_symbols(_cargar_universo()) == []


def test_the_american_universe_is_still_american():
    ruta = Path(__file__).resolve().parent.parent / mc.US.universe_file
    universe = [
        line.strip().upper()
        for line in ruta.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert mc.US.foreign_symbols(universe) == []


# -- Resolucion del perfil ---------------------------------------------------


@pytest.fixture
def perfil_eu(db):
    profile_id = db.create_profile(name="europa-01", description="IBEX + ESTX")
    db.update_settings(profile_id, {"market": "eu", "benchmark": mc.EU.benchmark})
    db.set_profile_universe(profile_id, ["SAN.MC", "ASML.AS", "SAP.DE"])
    db.set_profile_status(profile_id, "active")
    return profile_id


def test_a_profile_is_born_in_the_american_market(db):
    """The default is 'us' because of the databases that already exist, not out of preference."""
    profile_id = db.create_profile(name="p")

    assert db.get_settings(profile_id)["market"] == "us"


def test_the_profiles_market_reaches_the_settings(db, perfil_eu):
    settings = resolve_settings(db, perfil_eu, infra=INFRA)

    assert settings.market == "eu"


def test_the_market_enters_the_cycle_snapshot(db, perfil_eu):
    """Without the market in `cycles.settings_json`, a history cannot be
    interpreted: the same hours mean different things depending on the exchange."""
    settings = resolve_settings(db, perfil_eu, infra=INFRA)

    assert settings.snapshot()["market"] == "eu"


def test_an_american_symbol_in_a_european_profile_does_not_start(db, perfil_eu):
    """It fails while resolving, with the symbol in front. Letting it through
    would mean the analyst decides on yesterday's close believing it is today's."""
    db.set_profile_universe(perfil_eu, ["SAN.MC", "AAPL", "SAP.DE"])

    with pytest.raises(ConfigError) as exc:
        resolve_settings(db, perfil_eu, infra=INFRA)

    assert "AAPL" in str(exc.value)
    assert "eu" in str(exc.value)


def test_a_european_symbol_in_an_american_profile_does_not_either(db):
    profile_id = db.create_profile(name="us-01")
    db.set_profile_universe(profile_id, ["AAPL", "SAN.MC"])
    db.set_profile_status(profile_id, "active")

    with pytest.raises(ConfigError) as exc:
        resolve_settings(db, profile_id, infra=INFRA)

    assert "SAN.MC" in str(exc.value)


def test_the_database_refuses_a_market_not_in_the_registry(db, perfil_eu):
    """First barrier: the schema's CHECK. It is what stops an invalid `market`
    from even being stored."""
    from src.db import DatabaseError

    with pytest.raises(DatabaseError, match="CHECK constraint failed"):
        db._execute(
            "update agent_settings set market = 'jp' where profile_id = ?",
            (perfil_eu,),
        )


def test_an_invalid_market_fails_with_the_profiles_name():
    """Second barrier, in case the row arrives from elsewhere (a database edited
    by hand, an import). The message has to say which profile it is about: with
    several experiments open, a bare 'unknown market' points nowhere."""
    from src.profile_settings import _resolve_market

    with pytest.raises(ConfigError) as exc:
        _resolve_market({"market": "jp"}, label="europa-01")

    assert "europa-01" in str(exc.value)
    assert "jp" in str(exc.value)


def test_the_european_cycle_skips_the_weekend_all_the_same(db, perfil_eu):
    """should_run's semantics do not change with the market, only the calendar."""
    settings = resolve_settings(db, perfil_eu, infra=INFRA)
    allowed, reason = mc.should_run("1d", eu(2026, 8, 8, 12), market=settings.market)

    assert not allowed
    assert "sin sesion" in reason


# -- Importacion de un .env heredado -----------------------------------------


def test_importing_an_env_with_european_stocks_infers_the_market(db):
    """Assuming 'us' would make the freshly created profile fail to resolve, which
    is the worst possible moment to find out."""
    from src.config import Settings

    env = Settings(
        sim_slippage_bps=5.0, sim_commission=0.0, model_api_key="k",
        model_base_url="http://stub", llm_model="m", llm_temperature=0.2,
        llm_timeout_seconds=120.0, llm_max_retries=3, db_path="x.db",
        portfolio_name="heredado", initial_budget=10_000.0,
        watchlist=("SAN.MC", "ITX.MC"), lookback_days=200, dry_run=False,
        log_level="CRITICAL",
    )

    profile_id = import_env_profile(db, env, name="heredado")

    assert db.get_settings(profile_id)["market"] == "eu"
    assert db.get_settings(profile_id)["benchmark"] == mc.EU.benchmark
    # Y el perfil resultante resuelve sin quejarse.
    assert resolve_settings(db, profile_id, infra=INFRA).market == "eu"


def test_importing_an_american_env_still_gives_us(db):
    from src.config import Settings

    env = Settings(
        sim_slippage_bps=5.0, sim_commission=0.0, model_api_key="k",
        model_base_url="http://stub", llm_model="m", llm_temperature=0.2,
        llm_timeout_seconds=120.0, llm_max_retries=3, db_path="x.db",
        portfolio_name="heredado", initial_budget=10_000.0,
        watchlist=("AAPL", "MSFT"), lookback_days=200, dry_run=False,
        log_level="CRITICAL",
    )

    profile_id = import_env_profile(db, env, name="heredado")

    assert db.get_settings(profile_id)["market"] == "us"
    assert db.get_settings(profile_id)["benchmark"] == "SPY"


# -- Universo del ingestor por mercado ---------------------------------------


def test_the_ingestors_universe_comes_split_by_exchange(db, perfil_eu):
    otro = db.create_profile(name="us-01")
    db.set_profile_universe(otro, ["AAPL", "MSFT"])
    db.set_profile_status(otro, "active")

    universos = db.active_universe_by_market()

    assert universos == {
        "eu": ["ASML.AS", "SAN.MC", "SAP.DE"],
        "us": ["AAPL", "MSFT"],
    }


def test_paused_profiles_do_not_get_in(db, perfil_eu):
    db.set_profile_status(perfil_eu, "paused")

    assert db.active_universe_by_market() == {}


def test_a_profile_with_only_a_universe_file_does_not_reach_the_ingestor(db):
    """A trap inherited from F2.4 that is why `new-profile` fills both:
    `universe_file` is what the screener sifts for the cycle, and
    `profile_universe` is what the ingestor follows minute by minute. A profile
    with only the first is left with no live prices without anything saying so."""
    profile_id = db.create_profile(
        name="solo-fichero", settings={"universe_file": mc.EU.universe_file}
    )
    db.set_profile_status(profile_id, "active")

    assert db.active_universe_by_market() == {}


# -- El comando new-profile --------------------------------------------------


@pytest.fixture
def infra_tmp(tmp_path):
    return Infra(db_path=str(tmp_path / "cli.db"), log_level="CRITICAL")


def test_new_profile_fills_both_live_universe_and_file(infra_tmp):
    from run import command_new_profile
    from src.db import Database

    assert command_new_profile(
        infra_tmp, name="europa-01", market="eu", watch=0, budget=10_000.0
    ) == 0

    with Database(path=infra_tmp.db_path) as database:
        profile_id = database.get_profile_by_name("europa-01")["id"]
        settings = database.get_settings(profile_id)

        assert settings["market"] == "eu"
        assert settings["benchmark"] == mc.EU.benchmark
        assert settings["universe_file"] == mc.EU.universe_file
        assert len(database.get_profile_universe(profile_id)) == 89


def test_new_profile_nace_en_borrador(infra_tmp):
    """Activating it is a separate step: a profile born active would start
    consuming ingestion before anyone reviewed its parameters."""
    from run import command_new_profile
    from src.db import Database

    command_new_profile(
        infra_tmp, name="europa-01", market="eu", watch=0, budget=10_000.0
    )

    with Database(path=infra_tmp.db_path) as database:
        assert database.get_profile_by_name("europa-01")["status"] == "draft"
        # Y por tanto todavia no pesa en el ingestor.
        assert database.active_universe_by_market() == {}


def test_new_profile_refuses_to_follow_the_whole_sp500(infra_tmp):
    """503 symbols a minute against Yahoo from a domestic IP is R2. The command
    forces a choice instead of making one on its own."""
    from run import command_new_profile

    assert command_new_profile(
        infra_tmp, name="us-01", market="us", watch=0, budget=10_000.0
    ) == 2


def test_new_profile_accepts_the_sp500_with_an_explicit_cap(infra_tmp):
    from run import command_new_profile
    from src.db import Database

    assert command_new_profile(
        infra_tmp, name="us-01", market="us", watch=50, budget=10_000.0
    ) == 0

    with Database(path=infra_tmp.db_path) as database:
        profile_id = database.get_profile_by_name("us-01")["id"]
        assert len(database.get_profile_universe(profile_id)) == 50


def test_new_profile_refuses_an_unknown_market(infra_tmp):
    from run import command_new_profile

    assert command_new_profile(
        infra_tmp, name="x", market="eur", watch=0, budget=10_000.0
    ) == 2


def test_new_profile_demands_a_name(infra_tmp):
    from run import command_new_profile

    assert command_new_profile(
        infra_tmp, name="", market="eu", watch=0, budget=10_000.0
    ) == 2


def test_the_profile_created_by_the_command_resolves_without_complaining(infra_tmp):
    """The test that ties the two halves together: what the command creates is
    exactly what `resolve_settings` accepts."""
    from run import command_new_profile
    from src.db import Database

    command_new_profile(
        infra_tmp, name="europa-01", market="eu", watch=0, budget=7_500.0
    )

    with Database(path=infra_tmp.db_path) as database:
        profile_id = database.get_profile_by_name("europa-01")["id"]
        settings = resolve_settings(database, profile_id, infra=INFRA)

    assert settings.market == "eu"
    assert settings.initial_budget == 7_500.0
    assert len(settings.watchlist) == 89


# -- Liquidez minima por mercado (FE.11) -------------------------------------


def test_the_european_liquidity_floor_is_lower_than_the_american_one():
    """Measured on 2026-08-08: with the S&P 500's 20 M, the European screener
    discards 15 of the 89 —the IBEX mid-caps the index was added for— and it does
    so silently, because a filtered stock is not an error."""
    assert mc.EU.min_turnover == 5_000_000.0
    assert mc.US.min_turnover == 20_000_000.0


def test_a_non_positive_liquidity_floor_is_refused_on_validation():
    """A 0 does not blow up: it switches the filter off without saying so."""
    roto = dataclasses.replace(mc.EU, min_turnover=0.0)

    with pytest.raises(ValueError, match="min_turnover"):
        mc._check_markets([roto])


@pytest.mark.parametrize("market, watch, esperado", [
    ("eu", 0, 5_000_000.0),
    ("us", 50, 20_000_000.0),
])
def test_new_profile_sets_the_markets_liquidity_floor(
    infra_tmp, market, watch, esperado
):
    """FE.11: this used to be a printed warning that had to be applied by hand by
    opening the database. A warning that demands manual work ends up unapplied,
    and the symptom is a universe smaller than believed."""
    from run import command_new_profile
    from src.db import Database

    command_new_profile(
        infra_tmp, name=f"perfil-{market}", market=market, watch=watch,
        budget=10_000.0,
    )

    with Database(path=infra_tmp.db_path) as database:
        profile_id = database.get_profile_by_name(f"perfil-{market}")["id"]
        settings = database.get_settings(profile_id)
        resuelto = resolve_settings(database, profile_id, infra=INFRA)

    assert settings["screener_min_dollar_volume"] == esperado
    # And it reaches the screener, which is the only place the number does anything.
    assert resuelto.screener.min_dollar_volume == esperado


def test_the_split_adds_up_to_the_flat_universe(db, perfil_eu):
    """`active_universe` and `active_universe_by_market` cannot disagree: the
    second is the first with the exchange attached."""
    otro = db.create_profile(name="us-01")
    db.set_profile_universe(otro, ["AAPL"])
    db.set_profile_status(otro, "active")

    plano = set(db.active_universe())
    repartido = {s for symbols in db.active_universe_by_market().values()
                 for s in symbols}

    assert plano == repartido
