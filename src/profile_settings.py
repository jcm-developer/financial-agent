"""From a row of `agent_settings` to the `Settings` a cycle runs with.

This module is the hinge of F6.4. The cycle used to read its ~35 parameters from
the `.env`, which tied an experiment to a file: comparing two configurations
meant editing the `.env`, and then the earlier history stopped being
interpretable because nobody knew which values had generated it.

Now each profile carries its own in the database and the cycle resolves them at
startup. Three decisions worth understanding:

  * **`Settings` does not disappear.** It is still the contract `cycle.py`,
    `market_data.py` and the analyst consume; the only change is that it gets
    filled from SQLite instead of from `os.environ`. That leaves all of the
    cycle's code and its tests untouched.
  * **Resolution fails early and with the profile's name.** An invalid
    `bar_interval` is caught here, not three functions later inside yfinance.
  * **Choosing a profile is explicit.** If several are active, `--profile` is
    demanded instead of picking a "reasonable" one: running a cycle against the
    wrong experiment dirties two histories at once and cannot be undone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from . import llm, market_calendar, risk_presets
from .config import ConfigError, Infra, ScreenerSettings, Settings
from .db import Database, DatabaseError

log = logging.getLogger(__name__)

# Intervals the agent knows how to analyse. `agent_settings.bar_interval` also
# admits '1m' because that column is shared with the ingestor, which does work at
# one minute; the cycle, by contrast, needs enough history for SMA200.
CYCLE_INTERVALS = ("1d", "1h")


# ----------------------------------------------------------------------
# Eleccion de perfil
# ----------------------------------------------------------------------

def select_profile(db: Database, *, name: str = "") -> str:
    """Id of the profile to trade against.

    With `name`, that one and only that one. Without `name`, the single active
    profile. Any ambiguity is an error listing the candidates, not a silent
    choice.
    """
    name = (name or "").strip()
    profiles = db.list_profiles()

    # "There is no profile at all" comes before "that name does not exist" on
    # purpose: it is the useful diagnosis when coming from the previous version,
    # and with `PROFILE`/`PORTFOLIO_NAME` in the .env a name always arrives, so
    # without this precedence the startup message never appeared.
    if not profiles:
        raise ConfigError(
            "No hay ningun perfil en la base de datos, y desde F6.4 el ciclo toma\n"
            "  sus parametros del perfil y no del .env.\n"
            "  Importa la configuracion actual del .env a un perfil nuevo con:\n"
            "      python run.py import-profile --name experimento-01\n"
            "  Eso crea el perfil, sus parametros y su cartera, y lo deja activo."
        )

    if name:
        profile = db.get_profile_by_name(name)
        if profile is None:
            raise ConfigError(
                f"No existe ningun perfil llamado {name!r}.\n"
                f"  Perfiles disponibles: "
                + ", ".join(p["name"] for p in profiles)
            )
        return str(profile["id"])

    active = [p for p in profiles if p["status"] == "active"]
    if len(active) == 1:
        return str(active[0]["id"])
    if not active:
        raise ConfigError(
            "Ningun perfil esta activo, asi que no hay contra que operar.\n"
            "  Perfiles: "
            + ", ".join(f"{p['name']} ({p['status']})" for p in profiles)
            + "\n  Activalo con:  python run.py activate --profile <nombre>"
        )
    raise ConfigError(
        f"Hay {len(active)} perfiles activos y ninguno es el evidente. Elige uno:\n"
        + "\n".join(f"      python run.py cycle --profile {p['name']}" for p in active)
        + "\n  Ejecutar el ciclo contra el experimento equivocado ensucia dos\n"
        "  historicos a la vez y no se puede deshacer."
    )


# ----------------------------------------------------------------------
# Resolucion
# ----------------------------------------------------------------------

def resolve_settings(db: Database, profile_id: str, *, infra: Infra) -> Settings:
    """A profile's effective `Settings`.

    It joins the three sources: the `agent_settings` row, the profile's universe
    and the environment's infrastructure. The hard limits go through
    [risk_presets.py](risk_presets.py), which decides whether the sliders or the
    advanced-mode values win.
    """
    profile = db.get_profile(profile_id)
    if profile is None:
        raise ConfigError(f"El perfil {profile_id} no existe.")
    row = db.get_settings(profile_id)
    label = str(profile["name"])

    bar_interval = str(row["bar_interval"] or "1d").strip().lower()
    if bar_interval not in CYCLE_INTERVALS:
        raise ConfigError(
            f"El perfil {label!r} tiene bar_interval={bar_interval!r}, y el ciclo "
            f"solo analiza {' o '.join(CYCLE_INTERVALS)}. El valor '1m' es para el "
            "ingestor de precios, no para el analisis: con barras de un minuto no "
            "hay historico suficiente para los indicadores largos."
        )

    market = _resolve_market(row, label=label)

    universe_file = str(row["universe_file"] or "").strip()
    watchlist = tuple(db.get_profile_universe(profile_id))
    if not watchlist and not universe_file:
        raise ConfigError(
            f"El perfil {label!r} no tiene nada que analizar: ni universo propio "
            "ni fichero de universo.\n"
            "  Anade simbolos al perfil o apunta universe_file a un fichero como "
            f"{market.universe_file}."
        )

    _check_symbols_match_market(watchlist, market, label=label)

    screener = ScreenerSettings(
        universe_file=universe_file,
        top_n=int(row["screener_top_n"]),
        mode=str(row["screener_mode"]),
        min_dollar_volume=float(row["screener_min_dollar_volume"]),
        min_price=float(row["screener_min_price"]),
        max_volatility_pct=float(row["screener_max_volatility_pct"]),
    )

    try:
        risk = risk_presets.resolve_limits(row)
    except ConfigError as exc:
        raise ConfigError(f"Perfil {label!r}: {exc}") from exc

    provider, api_key, base_url = _resolve_model_access(row, infra, label=label)

    return Settings(
        sim_slippage_bps=float(row["sim_slippage_bps"]),
        sim_commission=float(row["sim_commission"]),
        model_api_key=api_key,
        model_base_url=base_url,
        llm_provider=provider,
        llm_model=str(row["llm_model"]),
        llm_temperature=float(row["llm_temperature"]),
        llm_timeout_seconds=float(row["llm_timeout_seconds"]),
        llm_max_retries=int(row["llm_max_retries"]),
        db_path=infra.db_path,
        # The profile's book is named after the profile (`create_profile`), so
        # `portfolio_name` is how the cycle finds it.
        portfolio_name=label,
        initial_budget=float(row["initial_budget"]),
        watchlist=watchlist,
        lookback_days=int(row["lookback_days"]),
        dry_run=bool(row["dry_run"]),
        log_level=infra.log_level,
        bar_interval=bar_interval,
        market=market.code,
        skip_when_market_closed=bool(row["skip_when_market_closed"]),
        risk=risk,
        screener=screener,
        profile_id=profile_id,
        risk_summary=risk_presets.describe(row),
    )


def _resolve_market(row: dict[str, Any], *, label: str) -> market_calendar.Market:
    """The profile's exchange, already resolved to its `Market`.

    It is resolved here and not on every calendar query so an invalid code blows
    up at startup, with the profile's name in front, instead of inside the
    ingestor's loop three hours later.
    """
    code = str(row.get("market") or market_calendar.DEFAULT_MARKET).strip().lower()
    try:
        return market_calendar.get_market(code)
    except market_calendar.UnknownMarket as exc:
        raise ConfigError(f"Perfil {label!r}: {exc}") from exc


def _check_symbols_match_market(
    symbols: tuple[str, ...], market: market_calendar.Market, *, label: str
) -> None:
    """Fails if the profile's universe carries symbols from another exchange.

    It is an error and not a warning because the symptom without this check is
    silent and expensive: the foreign symbols do not blow up, they simply have no
    new bar while the profile's exchange is open, so the analyst sees them at the
    previous close and decides on stale data without anything in the log giving
    it away.
    """
    foreign = market.foreign_symbols(symbols)
    if not foreign:
        return
    muestra = ", ".join(foreign[:8]) + ("..." if len(foreign) > 8 else "")
    raise ConfigError(
        f"El perfil {label!r} opera en {market.code} ({market.label}) pero su "
        f"universo trae {len(foreign)} simbolo(s) de otra bolsa: {muestra}\n"
        "  Un perfil cubre un solo mercado: de ahi salen el horario, el "
        "calendario y la divisa,\n"
        "  y el proyecto no convierte divisa en ningun sitio.\n"
        "  Saca esos simbolos del perfil, o crea otro perfil con su mercado."
    )


def _resolve_model_access(
    row: dict[str, Any], infra: Infra, *, label: str
) -> tuple[str, str, str]:
    """Effective provider, key and base URL. Returns `(provider, key, base_url)`.

    The key comes from the profile (F6.7); the environment's `NVIDIA_API_KEY` is
    kept as a fallback **for NVIDIA only**. That is deliberate: using the NIM key
    against OpenAI would not fail at resolution, it would fail halfway through the
    cycle with a 401 nobody relates to the profile.
    """
    provider = str(row["llm_provider"] or "nvidia").strip().lower()
    try:
        known = llm.resolve_provider(provider)
    except llm.LLMError as exc:
        raise ConfigError(f"Perfil {label!r}: {exc}") from exc

    api_key = str(row["llm_api_key"] or "").strip()
    if not api_key:
        if provider == "nvidia":
            api_key = infra.require_model_key()
        else:
            raise ConfigError(
                f"El perfil {label!r} usa {known.label} pero no tiene clave de API.\n"
                "  La clave va en el perfil, no en el .env: solo NVIDIA NIM usa\n"
                "  NVIDIA_API_KEY como respaldo.\n"
                f"      db.update_settings(<perfil>, {{'llm_api_key': '...'}})"
            )

    # `NVIDIA_BASE_URL` only applies to NIM. For the others the provider's wins,
    # which `LLMClient` resolves when it receives an empty string.
    base_url = infra.model_base_url if provider == "nvidia" else ""
    return provider, api_key, base_url


def mask_secret(value: str | None, *, keep: int = 4, empty: str = "(sin clave)") -> str:
    """`nvapi-...7f3a`, to show a key without showing it (F6.7).

    It is not real security -whoever can read the database has the whole key- but
    a way of keeping it out of a screen that gets shared or recorded.

    The dots are ASCII and not the ellipsis character: this gets printed to the
    Windows console, which with its default code page turns that into a diamond
    with a question mark.
    """
    secret = (value or "").strip()
    if not secret:
        return empty
    prefix = secret.split("-", 1)[0] + "-" if "-" in secret[:8] else ""
    tail = secret[-keep:] if len(secret) > keep else ""
    return f"{prefix}...{tail}"


def load_for_cycle(
    infra: Infra, *, profile_name: str = ""
) -> tuple[str, Settings]:
    """Opens the database, picks a profile and resolves. Returns `(profile_id, settings)`.

    The connection is closed on exit: the cycle opens its own in
    `TradingCycle.build`. That is two connections to the same file, which with
    WAL is normal here.
    """
    with Database(path=infra.db_path) as db:
        profile_id = select_profile(db, name=profile_name or infra.default_profile)
        return profile_id, resolve_settings(db, profile_id, infra=infra)


# ----------------------------------------------------------------------
# Creacion de perfiles
# ----------------------------------------------------------------------

#: Above this, following the whole universe minute by minute stops being
#: reasonable: these are requests per minute to Yahoo from a domestic IP (R2).
#: The S&P 500 falls on this side; the European 89 does not.
MAX_LIVE_SYMBOLS = 120


class UniverseError(RuntimeError):
    """The market's universe file cannot be used as-is.

    Kept apart from `ConfigError` because they are two different problems for
    whoever hits them: `ConfigError` is "pick something else" and this one is
    "the repository's file is wrong". The CLI translates them into different exit
    codes.
    """


@dataclass(frozen=True)
class CreatedProfile:
    """What has to be reported after creating a profile.

    It is returned instead of just the id because both front ends show it —the
    console prints it and the API returns it in the body— and without this each
    of them would have to re-read the universe file to count the same thing.
    """

    profile_id: str
    market: market_calendar.Market
    universe_size: int   # simbolos que criba el screener
    watched: int         # simbolos que el ingestor sigue minuto a minuto


def create_market_profile(
    db: Database,
    *,
    name: str,
    market: str,
    watch: int = 0,
    budget: float = 10_000.0,
    description: str = "",
) -> CreatedProfile:
    """Creates a profile from scratch for a market, with its universe in place.

    It lives here and not in `run.py` because it has **two front ends**: the
    `new-profile` command and F3.3's `POST /api/profiles`. With a copy in each,
    the first rule to diverge would be FE.11's —the liquidity floor comes from
    the market— and the symptom would be a profile created from the interface
    silently discarding 15 stocks that the one created from the console does
    analyse.

    It leaves the profile in `draft`: activating it is a separate, explicit step.
    A profile born active would start consuming ingestion before anyone had
    reviewed its parameters.
    """
    from .screener import load_universe

    name = (name or "").strip()
    if not name:
        raise ConfigError("El perfil necesita un nombre.")

    try:
        market = market_calendar.get_market(market)
    except market_calendar.UnknownMarket as exc:
        raise ConfigError(str(exc)) from exc

    try:
        universe = load_universe(market.universe_file)
    except Exception as exc:  # noqa: BLE001 - load_universe lanza tipos variados
        raise UniverseError(
            f"No se pudo leer {market.universe_file}: {exc}"
        ) from exc

    forasteros = market.foreign_symbols(universe)
    if forasteros:
        # If the market's file carries symbols from another exchange, the profile
        # would not even resolve. Better said here than on the first cycle.
        raise UniverseError(
            f"{market.universe_file} tiene simbolos que no son de "
            f"{market.code}: {', '.join(forasteros[:8])}"
        )

    if watch > 0:
        seguidos = universe[:watch]
    elif len(universe) > MAX_LIVE_SYMBOLS:
        raise ConfigError(
            f"{market.universe_file} tiene {len(universe)} simbolos y el "
            f"ingestor pide uno por peticion cada minuto.\n"
            f"  Elige cuantos seguir en vivo:  --watch 50\n"
            f"  (el screener sigue cribando el universo entero para el ciclo)"
        )
    else:
        seguidos = universe

    profile_id = db.create_profile(
        name=name,
        description=description or f"Mercado {market.code}: {market.label}",
        settings={
            "market": market.code,
            "benchmark": market.benchmark,
            "universe_file": market.universe_file,
            # The liquidity floor comes from the market, not from the schema's
            # default (FE.11): with 'us''s 20 M the European screener silently
            # discards 15 of the 89.
            "screener_min_dollar_volume": market.min_turnover,
            "initial_budget": budget,
        },
    )
    # The live universe is what the ingestor follows minute by minute;
    # `universe_file` is what the screener sifts for the cycle. They are two
    # different things and that is why both are filled: a profile with only a file
    # does not appear in `active_universe_by_market` and is left with no live
    # prices without anything saying so.
    db.set_profile_universe(profile_id, seguidos)
    return CreatedProfile(
        profile_id=profile_id,
        market=market,
        universe_size=len(universe),
        watched=len(seguidos),
    )


def duplicate_profile(
    db: Database, source_id: str, *, name: str, description: str = ""
) -> str:
    """Clones a profile with its settings and its universe, as a `draft`.

    It is the experiment's central gesture (F5.4): cloning and changing **one
    single** parameter is the only way to know what a difference in results is due
    to. What is not cloned is the history: the copy starts from zero with the
    original's initial budget, because inheriting another experiment's equity
    curve would make the two incomparable.
    """
    origen = db.get_profile(source_id)
    if origen is None:
        raise ConfigError(f"El perfil {source_id} no existe.")

    row = dict(db.get_settings(source_id))
    # `profile_id` and `updated_at` are set by the new row; the rest is copied
    # as-is, including the model key: a clone that lost the key would fail on its
    # first cycle for a reason nobody would relate to the copy.
    row.pop("profile_id", None)
    row.pop("updated_at", None)

    profile_id = db.create_profile(
        name=name,
        description=description or f"Copia de {origen['name']}.",
        settings=row,
    )
    db.set_profile_universe(profile_id, db.get_profile_universe(source_id))
    return profile_id


# ----------------------------------------------------------------------
# Importacion de un .env heredado
# ----------------------------------------------------------------------

def import_env_profile(
    db: Database, env_settings: Settings, *, name: str = "", activate: bool = True
) -> str:
    """Creates a profile that reproduces an existing `.env`.

    It is the single-use bridge between the previous world and F6.4. An important
    detail: **the risk limits are imported as advanced mode**, not as sliders. The
    `.env` carried nine explicit numbers, and replacing them with the ones coming
    out of `risk_profile=5` would change the agent's behaviour in the very
    operation that was only meant to move the configuration somewhere else. To
    switch to the sliders afterwards it is enough to turn `advanced_overrides` off.
    """
    name = (name or env_settings.portfolio_name).strip()
    risk = env_settings.risk
    screener = env_settings.screener

    # The market is inferred from the watchlist instead of assumed to be 'us'. An
    # inherited `.env` has no market column, and if someone was already following
    # European stocks by hand, importing it as 'us' would fail in
    # `resolve_settings`'s validation right after creating the profile.
    market = market_calendar.US
    if env_settings.watchlist and not market_calendar.EU.foreign_symbols(
        env_settings.watchlist
    ):
        market = market_calendar.EU

    changes: dict[str, Any] = {
        "market": market.code,
        "benchmark": market.benchmark,
        "llm_provider": env_settings.llm_provider,
        "llm_model": env_settings.llm_model,
        "llm_temperature": env_settings.llm_temperature,
        "llm_timeout_seconds": env_settings.llm_timeout_seconds,
        "llm_max_retries": env_settings.llm_max_retries,
        "initial_budget": env_settings.initial_budget,
        "bar_interval": env_settings.bar_interval,
        "lookback_days": env_settings.lookback_days,
        "dry_run": int(env_settings.dry_run),
        "skip_when_market_closed": int(env_settings.skip_when_market_closed),
        "sim_slippage_bps": env_settings.sim_slippage_bps,
        "sim_commission": env_settings.sim_commission,
        "universe_file": screener.universe_file or None,
        "screener_mode": screener.mode,
        "screener_top_n": screener.top_n,
        "screener_min_dollar_volume": screener.min_dollar_volume,
        "screener_min_price": screener.min_price,
        "screener_max_volatility_pct": screener.max_volatility_pct,
        "advanced_overrides": 1,
        "risk_per_trade_pct": risk.risk_per_trade_pct,
        "max_position_pct": risk.max_position_pct,
        "max_total_exposure_pct": risk.max_total_exposure_pct,
        "max_open_positions": risk.max_open_positions,
        "max_daily_loss_pct": risk.max_daily_loss_pct,
        "min_conviction": risk.min_conviction,
        "stop_atr_multiple": risk.stop_atr_multiple,
        "min_reward_risk": risk.min_reward_risk,
        "min_order_notional": risk.min_order_notional,
    }

    profile_id = db.create_profile(
        name=name,
        description="Importado del .env por run.py import-profile.",
        settings=changes,
    )
    if env_settings.watchlist:
        db.set_profile_universe(profile_id, list(env_settings.watchlist))
    if activate:
        db.set_profile_status(profile_id, "active")

    log.info(
        "Perfil %r importado del .env: %d simbolos, limites de riesgo en modo "
        "avanzado.", name, len(env_settings.watchlist),
    )
    return profile_id


# ----------------------------------------------------------------------
# Lectura del historico
# ----------------------------------------------------------------------

def cycle_settings(db: Database, cycle_id: str) -> dict[str, Any] | None:
    """The parameters a cycle ran with, or None if they were not recorded.

    It returns None for cycles predating F6.3, which do not carry the copy. It is
    missing information, not a zero: whoever compares experiments needs to tell
    "it ran with these settings" from "we do not know which settings it ran with".
    """
    rows = db.query("select settings_json from cycles where id = ?", (cycle_id,))
    if not rows:
        raise DatabaseError(f"No existe el ciclo {cycle_id}.")
    raw = rows[0]["settings_json"]
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        log.warning("El settings_json del ciclo %s no es JSON valido.", cycle_id)
        return None
