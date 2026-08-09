"""Configuration: what is infrastructure and what belongs to the experiment.

Since F6.4 there are two different things and they are worth not confusing:

  * **`Infra`** are the environment variables that remain: where the database
    is, the model's key and the log level. Things belonging to the machine, not
    to the experiment. It is the only part still coming from the `.env`.
  * **`Settings`** are the parameters a cycle runs with. They are **no longer**
    read from the environment: they come from `agent_settings`, the profile's
    table, via [profile_settings.py](profile_settings.py). `Settings` still
    exists because it is the contract `cycle.py`, `market_data.py` and the
    analyst consume; what changed is where it gets filled from.

`Settings.load()` and the `from_env()` helpers around it survive for one single
purpose: **importing an existing `.env` into a profile**
(`run.py import-profile`) and giving diagnostics in `run.py check` when there is
no profile yet. The cycle does not use them.

On both paths validation is strict and early: we would rather fail at startup
with a clear message than discover an empty key halfway through a cycle with
orders already sent.
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
    """Limits the Risk Manager applies deterministically.

    None of these values is decided by the LLM: they are the barrier that turns
    an absurd proposal from the model into a rejected order.
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
            # This is not an error: it only means the total exposure limit will
            # never be reached because the other two bind first.
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
    """The only part still coming from the environment: paths, model key, logs.

    It carries no strategy parameter. If one ever needs adding here, that is the
    sign its place was `agent_settings`.

    `load()` demands nothing: it can be built on a machine without credentials to
    look at the history. The commands that do need the model key call
    `require_model_key()`, which fails with a concrete message instead of letting
    the failure surface inside the first call to the LLM.
    """

    db_path: str = "data/trading.db"
    log_level: str = "INFO"
    model_api_key: str = ""
    model_base_url: str = "https://integrate.api.nvidia.com/v1"
    # The profile used when `--profile` is not passed. Empty = if there is a
    # single active profile, that one.
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
    """Configuration for the read-only commands (`report`, `serve`).

    It exists separately on purpose: looking at the history must not require the
    model key. That way the trading can be reviewed from a machine without
    credentials, and a half-filled `.env` does not stop the data being seen.
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
    """Configuration of the universe -> candidates funnel.

    If `universe_file` is empty there is no funnel: the watchlist is analysed as-is.
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
    """The effective parameters of a cycle.

    It is filled from `agent_settings` (see
    [profile_settings.py](profile_settings.py)); `load()` is only the import path
    from an inherited `.env`.
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

    # The exchange the profile trades against: 'us' or 'eu'. It fixes hours,
    # holidays and currency. See [market_calendar.py](market_calendar.py). It goes
    # into `snapshot()` on purpose: a history without the market cannot be
    # interpreted, because the same hours mean different things depending on it.
    market: str = "us"

    # 1d = barras diarias. 1h = barras horarias, para acumular operaciones
    # cerradas en semanas en lugar de meses.
    bar_interval: str = "1d"
    # If true, a cycle with the market closed analyses nothing and ends.
    skip_when_market_closed: bool = True

    risk: RiskLimits = field(default_factory=RiskLimits)
    screener: ScreenerSettings = field(default_factory=ScreenerSettings)

    # The profile these parameters came from. None when they come from a `.env`.
    profile_id: str | None = None
    # The F6.5 text with what the sliders imply, or None when the limits were set
    # by hand in the `.env`.
    risk_summary: str | None = None

    @property
    def mode(self) -> str:
        """Always paper: the only broker implementation is the simulator.

        The property is kept because `portfolios.mode` tells paper from live and
        the day there is a real broker it has two values again.
        """
        return "paper"

    # -- Record of what ran ------------------------------------------------

    def snapshot(self) -> dict:
        """The effective parameters, without secrets, for `cycles.settings_json`.

        It is what makes an experiment whose settings are edited midway
        interpretable (F6.3): without this copy there would be no way to know
        which configuration produced each decision, only the one in force now.

        The model key is excluded. That is not misplaced paranoia: the history
        gets exported, shared to ask for an opinion and opened with DB Browser,
        and a key inside a JSON column is not something you see coming.
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
        """Reads a complete `.env`. **Only for `import-profile` and `check`.**

        The cycle has not come through here since F6.4: its parameters come from
        the profile.
        """
        load_dotenv(dotenv_path=env_file, override=False)

        # Diagnosing the most common case: there is no .env yet. Without this the
        # first error would be "WATCHLIST is empty", which points nowhere.
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
        # With the funnel, the watchlist is optional: the universe replaces it.
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
        """Readable summary for the startup log, with no secrets leaked."""
        suffix = "  [DRY_RUN]" if self.dry_run else ""
        if self.screener.enabled:
            universe = (
                f"universo={self.screener.universe_file} "
                f"top={self.screener.top_n} filtro={self.screener.mode}"
            )
        else:
            universe = f"watchlist={len(self.watchlist)}"
        origin = f"perfil={self.portfolio_name}" if self.profile_id else ".env"
        # The currency symbol comes from the market: a European budget written
        # with '$' invites comparing it against another profile's as if it were
        # the same unit, and it is not.
        from .market_calendar import get_market

        money = get_market(self.market).currency_symbol
        return (
            f"{origin} mercado={self.market} datos=yahoo/{self.bar_interval} "
            f"presupuesto={money}{self.initial_budget:,.2f} "
            f"{universe} modelo={self.llm_model}"
            f"{suffix}"
        )
