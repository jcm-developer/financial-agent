"""Modelos de peticion y respuesta de la API (F3.6).

Son la fuente de la que sale el OpenAPI que FastAPI publica solo, y de ahi los
tipos de TypeScript del frontend (`tools/gen_api_types.py`). Por eso importa que
esten aqui y no repartidos por los endpoints: si el frontend repitiera las
definiciones a mano, la primera columna que cambiara dejaria la interfaz
mintiendo sin que nada fallara.

La decision que conviene entender: **`SettingsUpdate` enumera las columnas de
`agent_settings` una a una**, con sus rangos. Es largo y es a proposito: es el
formulario de F6.8, y un `dict[str, Any]` daria un `Record<string, unknown>` en
TypeScript, o sea ninguna ayuda justo en la pantalla con cuarenta campos. Un
test compara esta lista con las columnas reales de la tabla, asi que no puede
quedarse atras.

Aqui habia una segunda decision, y F4.11 la ha dejado sin objeto: `/api/dashboard`
era el unico endpoint sin modelo Pydantic —devolvia el ensamblado de doce
consultas de `build_dashboard` tal cual— y se retiro con `web/`. Desde entonces
**todos los endpoints tienen modelo**, que es lo que hace que un cambio del
backend rompa el build del frontend en vez de romper la pantalla en caliente.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

MarketCode = Literal["us", "eu"]
ProfileStatus = Literal["draft", "active", "paused", "archived"]


class Page(BaseModel, Generic[T]):
    """Envoltorio de las listas paginadas.

    Lleva `total` ademas de las filas porque sin el la interfaz no puede pintar
    "23 de 480" ni saber si merece la pena ofrecer la pagina siguiente.
    """

    items: list[T]
    total: int
    limit: int
    offset: int


# ----------------------------------------------------------------------
# Mercados
# ----------------------------------------------------------------------

class MarketInfo(BaseModel):
    """Una bolsa del registro de `src/market_calendar.py`.

    La interfaz la necesita para el alta de perfil (F5.3) y para no escribir '$'
    en un perfil europeo. Nada de esto se deduce en el frontend: la divisa, el
    horario y el suelo de liquidez son propiedades del mercado y viven en un
    solo sitio.
    """

    code: str
    label: str
    timezone: str
    currency: str
    currency_symbol: str
    benchmark: str
    universe_file: str
    universe_size: int
    min_turnover: float
    session_open: str
    session_close: str
    operating_open: str
    operating_close: str
    session_minutes: int
    operating_minutes: int
    is_trading_day: bool
    is_session_open: bool
    is_operating: bool
    #: Texto de `market_calendar.describe()`: "mercado abierto, cierra en 2h10".
    status_text: str


# ----------------------------------------------------------------------
# Perfiles
# ----------------------------------------------------------------------

class ProfileMetrics(BaseModel):
    """Las cifras de la tarjeta de perfil (F5.2)."""

    equity: float | None = None
    initial_budget: float | None = None
    total_return_pct: float | None = None
    day_pnl_pct: float | None = None
    open_positions: int = 0
    closed_trades: int = 0
    win_rate_pct: float | None = None
    realized_pnl: float | None = None
    cycles: int = 0
    decisions: int = 0
    last_cycle_at: str | None = None
    last_cycle_status: str | None = None


class ProfileSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    status: ProfileStatus
    created_at: str
    updated_at: str
    portfolio_id: str | None = None

    market: str
    currency: str
    currency_symbol: str

    llm_provider: str
    llm_model: str
    #: Enmascarada (`nvapi-...7f3a`). La clave entera no sale de la base nunca:
    #: la pantalla se comparte y se graba mas de lo que uno cree (F6.7).
    llm_api_key_masked: str

    universe_file: str | None = None
    #: Simbolos que el ingestor sigue minuto a minuto. Es distinto del universo
    #: del screener y confundirlos es la trampa de FE.7.
    watched_symbols: int = 0
    #: El texto de F6.5: "riesgo 5/10, diversificacion 5/10: max. 12 posiciones…"
    risk_summary: str
    metrics: ProfileMetrics


class DerivedLimits(BaseModel):
    """Los nueve limites efectivos y de donde sale cada uno.

    `derived_fields` es lo que la interfaz pinta en gris: los limites que salen
    de los deslizadores y no de un numero escrito a mano (F6.8).
    """

    risk_per_trade_pct: float
    max_position_pct: float
    max_total_exposure_pct: float
    max_open_positions: int
    max_daily_loss_pct: float
    min_conviction: int
    stop_atr_multiple: float
    min_reward_risk: float
    min_order_notional: float
    sector_cap: int | None = None
    derived_fields: list[str]
    summary: str


class ProfileDetail(ProfileSummary):
    settings: dict[str, Any]
    limits: DerivedLimits
    universe: list[str]
    market_info: MarketInfo


class SettingsHistoryRow(BaseModel):
    id: int
    field: str
    old_value: str | None = None
    new_value: str | None = None
    source: str | None = None
    changed_at: str


# ----------------------------------------------------------------------
# Escrituras (F3.3)
# ----------------------------------------------------------------------

class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    market: MarketCode = "eu"
    description: str = ""
    budget: float = Field(default=10_000.0, gt=0)
    #: Cuantos simbolos del universo seguir en vivo. 0 = todos, y se rechaza si
    #: el universo pasa de `MAX_LIVE_SYMBOLS`: son peticiones por minuto a Yahoo
    #: desde una IP domestica (R2).
    watch: int = Field(default=0, ge=0, le=500)


class ProfileDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = ""


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = None
    status: ProfileStatus | None = None


class UniverseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(max_length=500)


class SettingsUpdate(BaseModel):
    """Un parche sobre `agent_settings`.

    Solo se aplican los campos presentes en el cuerpo (`exclude_unset`), lo que
    permite distinguir "no lo toques" de "ponlo a NULL". La diferencia no es
    teorica: en los limites duros, NULL significa "vuelve a derivarlo de los
    deslizadores" (F6.5).
    """

    model_config = ConfigDict(extra="forbid")

    # -- Modelo
    llm_provider: Literal["nvidia", "openai", "anthropic"] | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    llm_timeout_seconds: float | None = Field(default=None, ge=5)
    llm_max_retries: int | None = Field(default=None, ge=1, le=10)
    analyst_persona: str | None = None

    # -- Estrategia
    risk_profile: int | None = Field(default=None, ge=1, le=10)
    diversification: int | None = Field(default=None, ge=1, le=10)
    horizon_days: int | None = Field(default=None, gt=0)
    market: MarketCode | None = None
    universe_file: str | None = None
    screener_mode: Literal["score", "random"] | None = None
    screener_top_n: int | None = Field(default=None, ge=1, le=200)
    screener_min_dollar_volume: float | None = Field(default=None, ge=0)
    screener_min_price: float | None = Field(default=None, ge=0)
    screener_max_volatility_pct: float | None = Field(default=None, ge=1)
    allow_shorts: bool | None = None
    excluded_sectors_json: str | None = None
    cash_reserve_pct: float | None = Field(default=None, ge=0, le=100)
    benchmark: str | None = None

    # -- Ejecucion
    initial_budget: float | None = Field(default=None, gt=0)
    bar_interval: Literal["1m", "1h", "1d"] | None = None
    lookback_days: int | None = Field(default=None, ge=60, le=2000)
    cycle_times: str | None = None
    cycle_tz: str | None = None
    sim_slippage_bps: float | None = Field(default=None, ge=0)
    sim_commission: float | None = Field(default=None, ge=0)
    dry_run: bool | None = None
    skip_when_market_closed: bool | None = None

    # -- Limites duros. NULL = derivalo de los deslizadores.
    advanced_overrides: bool | None = None
    risk_per_trade_pct: float | None = Field(default=None, ge=0.01, le=100)
    max_position_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_total_exposure_pct: float | None = Field(default=None, ge=0.1, le=100)
    max_open_positions: int | None = Field(default=None, ge=1, le=100)
    max_daily_loss_pct: float | None = Field(default=None, ge=0.1, le=100)
    min_conviction: int | None = Field(default=None, ge=0, le=100)
    stop_atr_multiple: float | None = Field(default=None, ge=0.1, le=20)
    min_reward_risk: float | None = Field(default=None, ge=0.1, le=100)
    min_order_notional: float | None = Field(default=None, ge=0)

    extra_json: str | None = None


class SettingsApplied(BaseModel):
    """Que cambio de verdad. Vacio significa que el cuerpo no cambiaba nada.

    Se devuelve la lista y no un simple `ok` porque `update_settings` ignora los
    campos que llegan con el valor que ya tenian: sin esto, la interfaz no podria
    distinguir "guardado" de "guardado y ademas cambio algo".
    """

    applied: list[str]
    limits: DerivedLimits


# ----------------------------------------------------------------------
# Historico de operativa (solo lectura)
# ----------------------------------------------------------------------

class PositionRow(BaseModel):
    id: str
    symbol: str
    status: str
    qty: float
    entry_price: float
    stop_price: float | None = None
    target_price: float | None = None
    thesis: str | None = None
    horizon_days: int | None = None
    opened_at: str
    closed_at: str | None = None
    exit_price: float | None = None
    realized_pnl: float | None = None
    exit_reason: str | None = None

    last_price: float | None = None
    last_price_as_of: str | None = None
    #: 'live' = cotizacion del ingestor; 'cycle' = el precio que vio el analista
    #: en su ultimo ciclo; None = no hay ninguno. Se etiqueta porque mentir sobre
    #: la frescura de un precio es peor que no darlo.
    price_source: Literal["live", "cycle"] | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    unrealized_pnl_pct: float | None = None
    stop_distance_pct: float | None = None


class DecisionRow(BaseModel):
    id: str
    cycle_id: str
    created_at: str
    symbol: str
    kind: str
    action: str
    conviction: int
    thesis: str | None = None
    risks: str | None = None
    horizon_days: int | None = None
    reference_price: float | None = None
    suggested_stop: float | None = None
    suggested_target: float | None = None
    llm_model: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    verdict: str | None = None
    rule: str | None = None
    risk_reason: str | None = None
    approved_qty: float | None = None
    approved_notional: float | None = None
    order_status: str | None = None
    filled_avg_price: float | None = None


class OrderRow(BaseModel):
    id: str
    cycle_id: str | None = None
    decision_id: str | None = None
    submitted_at: str
    updated_at: str
    symbol: str
    side: str
    qty: float
    order_type: str
    status: str
    filled_qty: float | None = None
    filled_avg_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    broker_order_id: str | None = None
    error: str | None = None


class RiskEventRow(BaseModel):
    id: str
    cycle_id: str
    decision_id: str | None = None
    created_at: str
    symbol: str | None = None
    verdict: str
    rule: str | None = None
    reason: str
    approved_qty: float | None = None
    approved_notional: float | None = None
    stop_price: float | None = None
    target_price: float | None = None


class CycleRow(BaseModel):
    id: str
    started_at: str
    finished_at: str | None = None
    status: str
    equity_start: float | None = None
    equity_end: float | None = None
    equity_delta: float | None = None
    market_open: bool | None = None
    llm_model: str | None = None
    error: str | None = None
    decisions: int = 0
    approved: int = 0
    rejected: int = 0
    orders: int = 0
    symbols_scanned: list[str] = Field(default_factory=list)
    #: Llamadas al modelo y cuantas se quedaron sin respuesta (F6.9). Con
    #: `analyst_failures == analyst_calls` el ciclo esta en 'failed' y no analizo
    #: nada; con un valor intermedio si analizo, pero le faltan simbolos. Sin este
    #: par, "0 decisiones" no se puede leer.
    analyst_calls: int = 0
    analyst_failures: int = 0


class CycleDetail(CycleRow):
    #: Los parametros con los que corrio (F6.3). None en los ciclos anteriores a
    #: esa tarea: es informacion que falta, no un cero, y la interfaz tiene que
    #: poder decir "no se sabe con que ajustes corrio".
    settings: dict[str, Any] | None = None


# ----------------------------------------------------------------------
# Datos de mercado e ingesta
# ----------------------------------------------------------------------

class EquityPoint(BaseModel):
    as_of: str
    equity: float
    cash: float | None = None
    positions_value: float | None = None
    open_positions: int = 0
    day_pnl_pct: float | None = None
    #: Caida desde el maximo previo, en %. Negativo o cero, nunca positivo.
    #:
    #: Se calcula en el servidor a proposito: es la misma definicion que usa
    #: `run.py report`, y tenerla tambien en TypeScript seria tenerla dos veces
    #: y condenarlas a discrepar.
    drawdown_pct: float = 0.0


class CalibrationBucket(BaseModel):
    """Una barra del grafico que decide el experimento.

    Si el `win_rate_pct` no crece con el `conviction_bucket`, la conviccion que
    declara el modelo no informa de nada y se esta operando con ruido caro.
    """

    conviction_bucket: int
    trades: int
    avg_pnl: float | None = None
    win_rate_pct: float | None = None


class RejectionCount(BaseModel):
    rule: str
    rejections: int
    last_seen: str | None = None


class SymbolPerformance(BaseModel):
    symbol: str
    trades: int
    wins: int
    win_rate_pct: float | None = None
    total_pnl: float | None = None
    avg_pnl: float | None = None
    avg_holding_days: float | None = None


class ConvictionBucket(BaseModel):
    bucket: int
    buys: int = 0
    holds: int = 0
    sells: int = 0
    total: int = 0


class Analytics(BaseModel):
    """Las cinco series de las graficas (F4.6), en un solo viaje.

    Juntas y no en cinco endpoints porque son una sola pantalla: cinco peticiones
    darian cinco estados de carga y cinco formas de fallar a medias para leer
    cinco agregados del mismo fichero local.
    """

    equity_curve: list[EquityPoint] = Field(default_factory=list)
    calibration: list[CalibrationBucket] = Field(default_factory=list)
    rejections: list[RejectionCount] = Field(default_factory=list)
    by_symbol: list[SymbolPerformance] = Field(default_factory=list)
    conviction_histogram: list[ConvictionBucket] = Field(default_factory=list)


class QuoteRow(BaseModel):
    symbol: str
    price: float
    prev_close: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    #: Inicio de la barra segun el proveedor.
    as_of: str
    #: Cuando lo escribimos nosotros. La distancia entre los dos es el retraso
    #: real del dato, que es la pregunta abierta de F2.1c en Europa.
    updated_at: str
    age_seconds: float | None = None


class IngestRun(BaseModel):
    id: int
    started_at: str
    finished_at: str | None = None
    kind: str
    symbols_requested: int
    symbols_ok: int
    symbols_failed: int
    latency_ms: int | None = None
    rate_limited: bool
    error: str | None = None


class IngestStatus(BaseModel):
    """Salud del ingestor.

    Las medias se calculan **solo sobre los ticks**: un relleno de huecos
    descarga varios dias de golpe, asi que una sola de sus filas desplazaria
    cualquier media de latencia y este panel pasaria a medir otra cosa (F2.10).
    """

    healthy: bool
    last_tick_at: str | None = None
    seconds_since_last_tick: float | None = None
    consecutive_failures: int = 0
    rate_limited_recently: bool = False
    avg_latency_ms: float | None = None
    symbols_tracked: int = 0
    symbols_by_market: dict[str, int] = Field(default_factory=dict)
    bars_stored: int = 0
    quotes_stored: int = 0
    last_backfill_at: str | None = None
    recent: list[IngestRun] = Field(default_factory=list)
    #: Por que decimos que esta sano o no, en una frase.
    message: str


# ----------------------------------------------------------------------
# Control de ciclos (F3.4)
# ----------------------------------------------------------------------

class CycleRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str | None = None
    dry_run: bool = False


class CycleControl(BaseModel):
    """Estado del ciclo lanzado desde la interfaz.

    `enabled=False` significa que los controles estan apagados: la API sirve
    igual, pero sin boton de disparar nada (F3.8).
    """

    enabled: bool
    running: bool
    profile: str | None = None
    dry_run: bool = False
    stage: str
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_seconds: int | None = None
    returncode: int | None = None
    lines: list[str] = Field(default_factory=list)


class ActionResult(BaseModel):
    ok: bool
    message: str
