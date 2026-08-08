"""De una fila de `agent_settings` a los `Settings` con los que corre un ciclo.

Este modulo es la bisagra de F6.4. Antes el ciclo leia sus ~35 parametros del
`.env`, lo que ataba un experimento a un fichero: para comparar dos
configuraciones habia que editar el `.env`, y entonces el historico anterior
dejaba de ser interpretable porque nadie sabia con que valores se habia generado.

Ahora cada perfil lleva los suyos en la base y el ciclo los resuelve al arrancar.
Tres decisiones que conviene entender:

  * **`Settings` no desaparece.** Sigue siendo el contrato que consumen
    `cycle.py`, `market_data.py` y el analista; lo unico que cambia es que se
    rellena desde SQLite en lugar de desde `os.environ`. Eso deja intacto todo el
    codigo del ciclo y sus tests.
  * **La resolucion falla pronto y con nombre del perfil.** Un `bar_interval`
    invalido se detecta aqui, no tres funciones mas adelante dentro de yfinance.
  * **Elegir perfil es explicito.** Si hay varios activos, se exige `--profile`
    en lugar de coger uno "razonable": ejecutar un ciclo contra el experimento
    equivocado ensucia dos historicos a la vez y no se puede deshacer.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import llm, market_calendar, risk_presets
from .config import ConfigError, Infra, ScreenerSettings, Settings
from .db import Database, DatabaseError

log = logging.getLogger(__name__)

# Intervalos que el agente sabe analizar. `agent_settings.bar_interval` admite
# tambien '1m' porque esa columna la comparte con el ingestor, que si trabaja a
# un minuto; el ciclo, en cambio, necesita historico suficiente para SMA200.
CYCLE_INTERVALS = ("1d", "1h")


# ----------------------------------------------------------------------
# Eleccion de perfil
# ----------------------------------------------------------------------

def select_profile(db: Database, *, name: str = "") -> str:
    """Id del perfil contra el que operar.

    Con `name`, ese y solo ese. Sin `name`, el unico perfil activo. Cualquier
    ambiguedad es un error con la lista de candidatos, no una eleccion silenciosa.
    """
    name = (name or "").strip()
    profiles = db.list_profiles()

    # "No hay ningun perfil" va antes que "ese nombre no existe" a proposito: es
    # el diagnostico util cuando se viene de la version anterior, y con
    # `PROFILE`/`PORTFOLIO_NAME` en el .env siempre llega un nombre, asi que sin
    # esta precedencia el mensaje de arranque nunca aparecia.
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
    """Los `Settings` efectivos de un perfil.

    Junta las tres fuentes: la fila de `agent_settings`, el universo del perfil y
    la infraestructura del entorno. Los limites duros pasan por
    [risk_presets.py](risk_presets.py), que decide si mandan los deslizadores o
    los valores del modo avanzado.
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
        # La cartera del perfil se llama igual que el perfil (`create_profile`),
        # asi que `portfolio_name` es la forma en que el ciclo la encuentra.
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
    """La bolsa del perfil, ya resuelta a su `Market`.

    Se resuelve aqui y no en cada consulta al calendario para que un codigo
    invalido salte al arrancar, con el nombre del perfil delante, en lugar de
    dentro del bucle del ingestor tres horas despues.
    """
    code = str(row.get("market") or market_calendar.DEFAULT_MARKET).strip().lower()
    try:
        return market_calendar.get_market(code)
    except market_calendar.UnknownMarket as exc:
        raise ConfigError(f"Perfil {label!r}: {exc}") from exc


def _check_symbols_match_market(
    symbols: tuple[str, ...], market: market_calendar.Market, *, label: str
) -> None:
    """Falla si el universo del perfil trae simbolos de otra bolsa.

    Es un error y no un aviso porque el sintoma sin esta comprobacion es
    silencioso y caro: los simbolos forasteros no revientan, simplemente no
    tienen barra nueva mientras la bolsa del perfil esta abierta, asi que el
    analista los ve con el precio del cierre anterior y decide sobre datos
    rancios sin que nada en el log lo delate.
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
    """Proveedor, clave y URL base efectivos. Devuelve `(provider, key, base_url)`.

    La clave la manda el perfil (F6.7); `NVIDIA_API_KEY` del entorno queda como
    respaldo **solo para NVIDIA**. Es deliberado: usar la clave de NIM contra
    OpenAI no fallaria en la resolucion, fallaria a mitad del ciclo con un 401 que
    nadie relaciona con el perfil.
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

    # `NVIDIA_BASE_URL` solo aplica a NIM. Para los demas manda la del proveedor,
    # que `LLMClient` resuelve cuando recibe cadena vacia.
    base_url = infra.model_base_url if provider == "nvidia" else ""
    return provider, api_key, base_url


def mask_secret(value: str | None, *, keep: int = 4, empty: str = "(sin clave)") -> str:
    """`nvapi-...7f3a`, para ensenar una clave sin ensenarla (F6.7).

    No es seguridad de verdad -quien pueda leer la base tiene la clave entera-
    sino evitar que aparezca en una pantalla que se comparte o se graba.

    Los puntos son ASCII y no el caracter de elipsis: esto se imprime en la
    consola de Windows, que con su pagina de codigos por defecto lo convierte en
    un rombo con un interrogante.
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
    """Abre la base, elige perfil y resuelve. Devuelve `(profile_id, settings)`.

    La conexion se cierra al salir: el ciclo abre la suya en `TradingCycle.build`.
    Son dos conexiones al mismo fichero, que con WAL es lo normal aqui.
    """
    with Database(path=infra.db_path) as db:
        profile_id = select_profile(db, name=profile_name or infra.default_profile)
        return profile_id, resolve_settings(db, profile_id, infra=infra)


# ----------------------------------------------------------------------
# Importacion de un .env heredado
# ----------------------------------------------------------------------

def import_env_profile(
    db: Database, env_settings: Settings, *, name: str = "", activate: bool = True
) -> str:
    """Crea un perfil que reproduce un `.env` existente.

    Es el puente de un solo uso entre el mundo anterior y F6.4. Detalle
    importante: **los limites de riesgo se importan como modo avanzado**, no como
    deslizadores. El `.env` traia nueve numeros explicitos, y sustituirlos por los
    que salen de `risk_profile=5` cambiaria el comportamiento del agente en la
    misma operacion en la que solo se pretendia mover la configuracion de sitio.
    Para pasarse a los deslizadores luego basta con apagar `advanced_overrides`.
    """
    name = (name or env_settings.portfolio_name).strip()
    risk = env_settings.risk
    screener = env_settings.screener

    # El mercado se deduce de la watchlist en lugar de darlo por 'us'. Un `.env`
    # heredado no tiene columna de mercado, y si alguien ya estaba siguiendo
    # valores europeos a mano, importarlo como 'us' fallaria en la validacion de
    # `resolve_settings` justo despues de crear el perfil.
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
    """Los parametros con los que corrio un ciclo, o None si no se registraron.

    Devuelve None para los ciclos anteriores a F6.3, que no llevan la copia. Es
    informacion que falta, no un cero: quien compare experimentos necesita
    distinguir "corrio con estos ajustes" de "no se sabe con que ajustes corrio".
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
