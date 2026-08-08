"""Configuracion: que es infraestructura y que es del experimento.

Desde F6.4 hay dos cosas distintas y conviene no confundirlas:

  * **`Infra`** son las variables de entorno que quedan: donde esta la base de
    datos, la clave del modelo y el nivel de log. Cosas de la maquina, no del
    experimento. Es lo unico que sigue viniendo del `.env`.
  * **`Settings`** son los parametros con los que corre un ciclo. Ya **no** se
    leen del entorno: salen de `agent_settings`, la tabla del perfil, via
    [profile_settings.py](profile_settings.py). `Settings` sigue existiendo
    porque es el contrato que consumen `cycle.py`, `market_data.py` y el
    analista; lo que ha cambiado es de donde se rellena.

`Settings.load()` y los `from_env()` que la acompanan sobreviven con un unico
proposito: **importar un `.env` existente a un perfil** (`run.py import-profile`)
y dar diagnostico en `run.py check` cuando todavia no hay ningun perfil. No los
use el ciclo.

En los dos caminos la validacion es estricta y temprana: preferimos fallar al
arrancar con un mensaje claro antes que descubrir una clave vacia a mitad de un
ciclo con ordenes ya enviadas.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """La configuracion es invalida o incompleta."""


def _require(key: str) -> str:
    value = (os.getenv(key) or "").strip()
    if not value:
        raise ConfigError(
            f"Falta la variable de entorno {key}. "
            "Copia .env.example a .env y rellena los valores."
        )
    return value


def _get_bool(key: str, default: bool) -> bool:
    raw = (os.getenv(key) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise ConfigError(f"{key} debe ser true o false, no {raw!r}.")


def _get_float(key: str, default: float, *, minimum: float | None = None,
               maximum: float | None = None) -> float:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ConfigError(f"{key} debe ser numerico, no {raw!r}.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key}={value} es menor que el minimo permitido {minimum}.")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{key}={value} supera el maximo permitido {maximum}.")
    return value


def _get_int(key: str, default: int, *, minimum: int | None = None,
             maximum: int | None = None) -> int:
    value = _get_float(key, float(default))
    if value != int(value):
        raise ConfigError(f"{key} debe ser un entero, no {value!r}.")
    result = int(value)
    if minimum is not None and result < minimum:
        raise ConfigError(f"{key}={result} es menor que el minimo permitido {minimum}.")
    if maximum is not None and result > maximum:
        raise ConfigError(f"{key}={result} supera el maximo permitido {maximum}.")
    return result


@dataclass(frozen=True)
class RiskLimits:
    """Limites que el Risk Manager aplica de forma determinista.

    Ninguno de estos valores lo decide el LLM: son la barrera que convierte una
    propuesta absurda del modelo en una orden rechazada.
    """

    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 20.0
    max_total_exposure_pct: float = 80.0
    max_open_positions: int = 5
    max_daily_loss_pct: float = 5.0
    min_conviction: int = 65
    stop_atr_multiple: float = 2.0
    min_reward_risk: float = 1.5
    min_order_notional: float = 100.0

    def __post_init__(self) -> None:
        if self.risk_per_trade_pct > self.max_position_pct:
            raise ConfigError(
                "RISK_PER_TRADE_PCT no puede superar MAX_POSITION_PCT: se arriesgaria "
                "mas de lo que la posicion puede llegar a valer."
            )
        if self.max_position_pct * self.max_open_positions < self.max_total_exposure_pct:
            # No es un error: solo significa que el limite de exposicion total
            # nunca se alcanzara porque los otros dos limites atan antes.
            pass

    @classmethod
    def from_env(cls) -> RiskLimits:
        return cls(
            risk_per_trade_pct=_get_float("RISK_PER_TRADE_PCT", 1.0, minimum=0.01, maximum=100.0),
            max_position_pct=_get_float("MAX_POSITION_PCT", 20.0, minimum=0.1, maximum=100.0),
            max_total_exposure_pct=_get_float("MAX_TOTAL_EXPOSURE_PCT", 80.0, minimum=0.1, maximum=100.0),
            max_open_positions=_get_int("MAX_OPEN_POSITIONS", 5, minimum=1, maximum=100),
            max_daily_loss_pct=_get_float("MAX_DAILY_LOSS_PCT", 5.0, minimum=0.1, maximum=100.0),
            min_conviction=_get_int("MIN_CONVICTION", 65, minimum=0, maximum=100),
            stop_atr_multiple=_get_float("STOP_ATR_MULTIPLE", 2.0, minimum=0.1, maximum=20.0),
            min_reward_risk=_get_float("MIN_REWARD_RISK", 1.5, minimum=0.1, maximum=100.0),
            min_order_notional=_get_float("MIN_ORDER_NOTIONAL", 100.0, minimum=0.0),
        )


@dataclass(frozen=True)
class Infra:
    """Lo unico que sigue viniendo del entorno: rutas, clave del modelo, logs.

    No lleva ningun parametro de estrategia. Si alguna vez hace falta anadir uno
    aqui, es senal de que su sitio era `agent_settings`.

    `load()` no exige nada: se puede construir en una maquina sin credenciales
    para mirar el historico. Los comandos que si necesitan la clave del modelo
    llaman a `require_model_key()`, que falla con un mensaje concreto en lugar de
    dejar que el fallo aparezca dentro de la primera llamada al LLM.
    """

    db_path: str = "data/trading.db"
    log_level: str = "INFO"
    model_api_key: str = ""
    model_base_url: str = "https://integrate.api.nvidia.com/v1"
    # Perfil que se usa cuando no se pasa `--profile`. Vacio = si hay un solo
    # perfil activo, ese.
    default_profile: str = ""

    @classmethod
    def load(cls, *, env_file: str | None = None) -> Infra:
        load_dotenv(dotenv_path=env_file, override=False)
        return cls(
            db_path=(os.getenv("DB_PATH") or "data/trading.db").strip(),
            log_level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
            model_api_key=(os.getenv("NVIDIA_API_KEY") or "").strip(),
            model_base_url=(os.getenv("NVIDIA_BASE_URL")
                            or "https://integrate.api.nvidia.com/v1").rstrip("/"),
            default_profile=(os.getenv("PROFILE") or os.getenv("PORTFOLIO_NAME") or "").strip(),
        )

    def require_model_key(self) -> str:
        if not self.model_api_key:
            expected = Path.cwd() / ".env"
            raise ConfigError(
                "Falta NVIDIA_API_KEY: sin clave no se puede llamar al modelo.\n"
                f"  Ponla en {expected} (copia la plantilla con: copy .env.example .env).\n"
                "  Para mirar el historico sin ninguna clave:  python run.py report"
            )
        return self.model_api_key


@dataclass(frozen=True)
class DashboardSettings:
    """Configuracion de los comandos que solo leen (`report`, `serve`).

    Existe por separado a proposito: mirar el historico no debe requerir la clave
    del modelo. Asi se puede revisar la operativa desde una maquina sin
    credenciales, y un `.env` a medio rellenar no impide ver datos.
    """

    db_path: str
    portfolio_name: str

    @classmethod
    def load(cls, *, env_file: str | None = None) -> DashboardSettings:
        load_dotenv(dotenv_path=env_file, override=False)
        return cls(
            db_path=(os.getenv("DB_PATH") or "data/trading.db").strip(),
            portfolio_name=(os.getenv("PORTFOLIO_NAME") or "experimento-01").strip(),
        )


@dataclass(frozen=True)
class ScreenerSettings:
    """Configuracion del embudo universo -> candidatos.

    Si `universe_file` esta vacio, no hay embudo: se analiza la watchlist tal cual.
    """

    universe_file: str = ""
    top_n: int = 20
    mode: str = "score"
    min_dollar_volume: float = 20_000_000.0
    min_price: float = 5.0
    max_volatility_pct: float = 120.0

    @property
    def enabled(self) -> bool:
        return bool(self.universe_file)

    @classmethod
    def from_env(cls) -> ScreenerSettings:
        mode = (os.getenv("SCREENER_MODE") or "score").strip().lower()
        if mode not in {"score", "random"}:
            raise ConfigError(
                f"SCREENER_MODE debe ser score o random, no {mode!r}. "
                "'random' es el grupo de control: candidatos arbitrarios."
            )
        return cls(
            universe_file=(os.getenv("UNIVERSE_FILE") or "").strip(),
            top_n=_get_int("SCREENER_TOP_N", 20, minimum=1, maximum=200),
            mode=mode,
            min_dollar_volume=_get_float("SCREENER_MIN_DOLLAR_VOLUME", 20_000_000.0, minimum=0.0),
            min_price=_get_float("SCREENER_MIN_PRICE", 5.0, minimum=0.0),
            max_volatility_pct=_get_float("SCREENER_MAX_VOLATILITY_PCT", 120.0, minimum=1.0),
        )


@dataclass(frozen=True)
class Settings:
    """Los parametros efectivos de un ciclo.

    Se rellena desde `agent_settings` (ver
    [profile_settings.py](profile_settings.py)); `load()` es solo el camino de
    importacion desde un `.env` heredado.
    """

    sim_slippage_bps: float
    sim_commission: float

    model_api_key: str
    model_base_url: str
    llm_model: str
    llm_temperature: float
    llm_timeout_seconds: float
    llm_max_retries: int

    db_path: str

    portfolio_name: str
    initial_budget: float
    watchlist: tuple[str, ...]
    lookback_days: int
    dry_run: bool
    log_level: str

    # nvidia (por defecto) u openai. Ver [llm.py](llm.py).
    llm_provider: str = "nvidia"

    # Bolsa contra la que opera el perfil: 'us' o 'eu'. Fija horario, festivos y
    # divisa. Ver [market_calendar.py](market_calendar.py). Entra en `snapshot()`
    # a proposito: un historico sin el mercado no se puede interpretar, porque
    # las mismas horas significan cosas distintas segun cual fuera.
    market: str = "us"

    # 1d = barras diarias. 1h = barras horarias, para acumular operaciones
    # cerradas en semanas en lugar de meses.
    bar_interval: str = "1d"
    # Si es cierto, un ciclo con el mercado cerrado no analiza nada y termina.
    skip_when_market_closed: bool = True

    risk: RiskLimits = field(default_factory=RiskLimits)
    screener: ScreenerSettings = field(default_factory=ScreenerSettings)

    # Perfil del que salieron estos parametros. None cuando vienen de un `.env`.
    profile_id: str | None = None
    # Texto de F6.5 con lo que implican los deslizadores, o None si los limites
    # se fijaron a mano en el `.env`.
    risk_summary: str | None = None

    @property
    def mode(self) -> str:
        """Siempre paper: la unica implementacion de broker es el simulador.

        Se conserva la propiedad porque `portfolios.mode` distingue paper de live
        y el dia que haya un broker real vuelve a tener dos valores.
        """
        return "paper"

    # -- Registro de lo que corrio ----------------------------------------

    def snapshot(self) -> dict:
        """Los parametros efectivos, sin secretos, para `cycles.settings_json`.

        Es lo que hace interpretable un experimento cuyos ajustes se editan a
        mitad (F6.3): sin esta copia no se sabria que configuracion produjo cada
        decision, solo la que hay ahora.

        Se excluye la clave del modelo. No es paranoia mal puesta: el historico se
        exporta, se comparte para pedir opinion y se abre con DB Browser, y una
        clave dentro de una columna JSON no se ve venir.
        """
        data = asdict(self)
        for secret in ("model_api_key", "db_path"):
            data.pop(secret, None)
        data["watchlist"] = list(self.watchlist)
        data["mode"] = self.mode
        return data

    # -- Importacion desde un .env heredado -------------------------------

    @classmethod
    def load(cls, *, env_file: str | None = None) -> Settings:
        """Lee un `.env` completo. **Solo para `import-profile` y `check`.**

        El ciclo no pasa por aqui desde F6.4: sus parametros salen del perfil.
        """
        load_dotenv(dotenv_path=env_file, override=False)

        # Diagnostico del caso mas comun: no hay .env todavia. Sin esto el primer
        # error seria "WATCHLIST esta vacia", que no orienta a nada.
        if not os.getenv("NVIDIA_API_KEY"):
            expected = Path(env_file) if env_file else Path.cwd() / ".env"
            if not expected.exists():
                raise ConfigError(
                    f"No se encontro el fichero de configuracion {expected}.\n"
                    "  Crealo copiando la plantilla y rellena la clave del modelo:\n"
                    "      copy .env.example .env\n"
                    "  Solo hace falta NVIDIA_API_KEY.\n"
                    "  Para ver la interfaz sin ninguna clave:\n"
                    "      python tools/seed_demo.py  &&  python run.py api"
                )

        screener = ScreenerSettings.from_env()

        watchlist_raw = os.getenv("WATCHLIST") or ""
        watchlist = tuple(
            sorted({s.strip().upper() for s in watchlist_raw.split(",") if s.strip()})
        )
        # Con embudo, la watchlist es opcional: el universo la sustituye.
        if not watchlist and not screener.enabled:
            raise ConfigError(
                "WATCHLIST esta vacia y no hay UNIVERSE_FILE: no hay nada que analizar."
            )

        bar_interval = (os.getenv("BAR_INTERVAL") or "1d").strip().lower()
        if bar_interval not in {"1d", "1h"}:
            raise ConfigError(
                f"BAR_INTERVAL debe ser 1d o 1h, no {bar_interval!r}. Yahoo solo "
                "sirve unos 700 dias de historico horario."
            )

        settings = cls(
            sim_slippage_bps=_get_float("SIM_SLIPPAGE_BPS", 5.0, minimum=0.0, maximum=500.0),
            sim_commission=_get_float("SIM_COMMISSION", 0.0, minimum=0.0),
            model_api_key=_require("NVIDIA_API_KEY"),
            model_base_url=(os.getenv("NVIDIA_BASE_URL")
                            or "https://integrate.api.nvidia.com/v1").rstrip("/"),
            llm_model=(os.getenv("LLM_MODEL") or "meta/llama-3.3-70b-instruct").strip(),
            llm_temperature=_get_float("LLM_TEMPERATURE", 0.2, minimum=0.0, maximum=2.0),
            llm_timeout_seconds=_get_float("LLM_TIMEOUT_SECONDS", 120.0, minimum=5.0),
            llm_max_retries=_get_int("LLM_MAX_RETRIES", 3, minimum=1, maximum=10),
            db_path=(os.getenv("DB_PATH") or "data/trading.db").strip(),
            portfolio_name=(os.getenv("PORTFOLIO_NAME") or "experimento-01").strip(),
            initial_budget=_get_float("INITIAL_BUDGET", 10_000.0, minimum=1.0),
            watchlist=watchlist,
            lookback_days=_get_int("LOOKBACK_DAYS", 200, minimum=60, maximum=2000),
            dry_run=_get_bool("DRY_RUN", False),
            log_level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
            bar_interval=bar_interval,
            skip_when_market_closed=_get_bool("SKIP_WHEN_MARKET_CLOSED", True),
            risk=RiskLimits.from_env(),
            screener=screener,
        )
        return settings

    @property
    def dashboard(self) -> DashboardSettings:
        return DashboardSettings(
            db_path=self.db_path, portfolio_name=self.portfolio_name
        )

    def describe(self) -> str:
        """Resumen legible para el log de arranque, sin filtrar secretos."""
        suffix = "  [DRY_RUN]" if self.dry_run else ""
        if self.screener.enabled:
            universe = (
                f"universo={self.screener.universe_file} "
                f"top={self.screener.top_n} filtro={self.screener.mode}"
            )
        else:
            universe = f"watchlist={len(self.watchlist)}"
        origin = f"perfil={self.portfolio_name}" if self.profile_id else ".env"
        # El simbolo de moneda sale del mercado: un presupuesto europeo escrito
        # con '$' invita a compararlo con el de otro perfil como si fuera la
        # misma unidad, y no lo es.
        from .market_calendar import get_market

        money = get_market(self.market).currency_symbol
        return (
            f"{origin} mercado={self.market} datos=yahoo/{self.bar_interval} "
            f"presupuesto={money}{self.initial_budget:,.2f} "
            f"{universe} modelo={self.llm_model}"
            f"{suffix}"
        )
