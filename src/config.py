"""Carga y validacion de configuracion desde el entorno.

Toda la configuracion se resuelve una vez al arrancar y se valida de forma
estricta: preferimos fallar al inicio con un mensaje claro antes que descubrir
una clave vacia a mitad de un ciclo con ordenes ya enviadas.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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
class DashboardSettings:
    """Configuracion de los comandos que solo leen (`report`, `serve`).

    Existe por separado a proposito: mirar el historico no debe requerir las
    claves de Alpaca ni de NVIDIA. Asi se puede revisar la operativa desde una
    maquina sin credenciales, y un `.env` a medio rellenar no impide ver datos.
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
    # sim = broker simulado sobre SQLite (por defecto, no necesita cuenta).
    # alpaca = cuenta real de Alpaca, paper o live.
    broker: str
    # yahoo = yfinance (sin clave). alpaca = feed de Alpaca.
    data_provider: str
    sim_slippage_bps: float
    sim_commission: float

    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_paper: bool
    alpaca_data_feed: str

    nvidia_api_key: str
    nvidia_base_url: str
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

    # 1d = barras diarias. 1h = barras horarias, para acumular operaciones
    # cerradas en semanas en lugar de meses.
    bar_interval: str = "1d"
    # Si es cierto, un ciclo con el mercado cerrado no analiza nada y termina.
    skip_when_market_closed: bool = True

    risk: RiskLimits = field(default_factory=RiskLimits)
    screener: ScreenerSettings = field(default_factory=ScreenerSettings)

    @property
    def uses_alpaca(self) -> bool:
        """True si alguna pieza necesita credenciales de Alpaca."""
        return self.broker == "alpaca" or self.data_provider == "alpaca"

    @property
    def mode(self) -> str:
        """El simulador es paper por definicion: no hay dinero de verdad."""
        if self.broker == "sim":
            return "paper"
        return "paper" if self.alpaca_paper else "live"

    @classmethod
    def load(cls, *, env_file: str | None = None) -> Settings:
        load_dotenv(dotenv_path=env_file, override=False)

        broker = (os.getenv("BROKER") or "sim").strip().lower()
        if broker not in {"sim", "alpaca"}:
            raise ConfigError(f"BROKER debe ser sim o alpaca, no {broker!r}.")

        data_provider = (os.getenv("DATA_PROVIDER") or "yahoo").strip().lower()
        if data_provider not in {"yahoo", "alpaca"}:
            raise ConfigError(
                f"DATA_PROVIDER debe ser yahoo o alpaca, no {data_provider!r}."
            )

        needs_alpaca = broker == "alpaca" or data_provider == "alpaca"

        # Diagnostico del caso mas comun: no hay .env todavia. Sin esto el primer
        # error seria "WATCHLIST esta vacia", que no orienta a nada.
        if not os.getenv("NVIDIA_API_KEY"):
            expected = Path(env_file) if env_file else Path.cwd() / ".env"
            if not expected.exists():
                raise ConfigError(
                    f"No se encontro el fichero de configuracion {expected}.\n"
                    "  Crealo copiando la plantilla y rellena la clave del modelo:\n"
                    "      copy .env.example .env\n"
                    "  Con los valores por defecto (BROKER=sim, DATA_PROVIDER=yahoo)\n"
                    "  solo hace falta NVIDIA_API_KEY.\n"
                    "  Para ver el dashboard sin ninguna clave:\n"
                    "      python tools/seed_demo.py  &&  python run.py serve"
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

        feed = (os.getenv("ALPACA_DATA_FEED") or "iex").strip().lower()
        if feed not in {"iex", "sip"}:
            raise ConfigError(f"ALPACA_DATA_FEED debe ser iex o sip, no {feed!r}.")

        paper = _get_bool("ALPACA_PAPER", True)
        dry_run = _get_bool("DRY_RUN", False)

        # Las claves de Alpaca solo se exigen si algo las va a usar: con los
        # valores por defecto el experimento arranca con una sola clave, la del
        # modelo.
        if needs_alpaca:
            alpaca_key = _require("ALPACA_API_KEY")
            alpaca_secret = _require("ALPACA_SECRET_KEY")
        else:
            alpaca_key = alpaca_secret = ""

        settings = cls(
            broker=broker,
            data_provider=data_provider,
            sim_slippage_bps=_get_float("SIM_SLIPPAGE_BPS", 5.0, minimum=0.0, maximum=500.0),
            sim_commission=_get_float("SIM_COMMISSION", 0.0, minimum=0.0),
            alpaca_api_key=alpaca_key,
            alpaca_secret_key=alpaca_secret,
            alpaca_paper=paper,
            alpaca_data_feed=feed,
            nvidia_api_key=_require("NVIDIA_API_KEY"),
            nvidia_base_url=(os.getenv("NVIDIA_BASE_URL")
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
            dry_run=dry_run,
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
        flags = []
        if self.dry_run:
            flags.append("DRY_RUN")
        if self.broker == "alpaca" and not self.alpaca_paper:
            flags.append("!! DINERO REAL !!")
        suffix = f"  [{' '.join(flags)}]" if flags else ""
        if self.screener.enabled:
            universe = (
                f"universo={self.screener.universe_file} "
                f"top={self.screener.top_n} filtro={self.screener.mode}"
            )
        else:
            universe = f"watchlist={len(self.watchlist)}"
        return (
            f"cartera={self.portfolio_name} broker={self.broker} "
            f"datos={self.data_provider}/{self.bar_interval} modo={self.mode} "
            f"presupuesto=${self.initial_budget:,.2f} "
            f"{universe} modelo={self.llm_model}"
            f"{suffix}"
        )
