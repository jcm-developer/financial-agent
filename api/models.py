"""The API's request and response models (F3.6).

They are the source the OpenAPI document FastAPI publishes comes from, and from
there the frontend's TypeScript types (`tools/gen_api_types.py`). That is why it
matters that they live here and not scattered across the endpoints: if the
frontend repeated the definitions by hand, the first column to change would leave
the interface lying without anything failing.

The decision worth understanding: **`SettingsUpdate` enumerates the columns of
`agent_settings` one by one**, with their ranges. It is long and it is deliberate:
it is the form of F6.8, and a `dict[str, Any]` would give a
`Record<string, unknown>` in TypeScript, that is, no help at all on the screen
with forty fields. A test compares this list against the table's real columns, so
it cannot fall behind.

There used to be a second decision here, and F4.11 left it moot:
`/api/dashboard` was the only endpoint without a Pydantic model —it returned the
assembly of twelve `build_dashboard` queries as-is— and it was retired along with
`web/`. Since then **every endpoint has a model**, which is what makes a backend
change break the frontend's build instead of breaking the screen at runtime.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

MarketCode = Literal["us", "eu"]
ProfileStatus = Literal["draft", "active", "paused", "archived"]


class Page(BaseModel, Generic[T]):
    """Wrapper for the paginated lists.

    It carries `total` besides the rows because without it the interface cannot
    paint "23 de 480" nor know whether offering the next page is worthwhile.
    """

    items: list[T]
    total: int
    limit: int
    offset: int


# ----------------------------------------------------------------------
# Mercados
# ----------------------------------------------------------------------

class MarketInfo(BaseModel):
    """One exchange from the registry in `src/market_calendar.py`.

    The interface needs it for the profile creation form (F5.3) and so as not to
    write '$' in a European profile. None of this is inferred in the frontend:
    the currency, the hours and the liquidity floor are properties of the market
    and live in a single place.
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
    """The figures on the profile card (F5.2)."""

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
    #: Masked (`nvapi-...7f3a`). The whole key never leaves the database: the
    #: screen gets shared and recorded more than one thinks (F6.7).
    llm_api_key_masked: str

    universe_file: str | None = None
    #: `score` or `random`. `random` is the control group of F5.7, and it is here
    #: —and not only in the settings— so the list and the comparator can say
    #: which experiment is the control: comparing against a control you cannot
    #: identify is the same as not having one.
    screener_mode: str = "score"
    #: Symbols the ingestor follows minute by minute. This differs from the
    #: screener's universe, and confusing the two is the trap of FE.7.
    watched_symbols: int = 0
    #: El texto de F6.5: "riesgo 5/10, diversificacion 5/10: max. 12 posiciones…"
    risk_summary: str
    metrics: ProfileMetrics


class DerivedLimits(BaseModel):
    """The nine effective limits and where each one comes from.

    `derived_fields` is what the interface paints in grey: the limits that come
    from the sliders and not from a number written by hand (F6.8).
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
    #: How many symbols of the universe to follow live. 0 = all of them, and it is
    #: refused if the universe exceeds `MAX_LIVE_SYMBOLS`: these are requests per
    #: minute to Yahoo from a domestic IP (R2).
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


class AgentSettings(BaseModel):
    """One row of `agent_settings`, as it is read.

    ⚠️ **It is the read counterpart of `SettingsUpdate`, and it is not the same
    shape**, which is why they are two models and not one with everything
    optional:

      * The **hard limits come back NULL** when they are derived from the
        sliders. That NULL is the datum (F6.5): it means "recompute it", and a
        model that filled it in with a number would erase the difference between
        a limit that was chosen and one that was inherited.
      * The **booleans come back as 0/1**, because SQLite has no boolean. They
        are declared `bool` so Pydantic converts them once, here, instead of
        every screen deciding whether `0` is false.

    It exists because until F6.8 this endpoint answered a plain `dict` and
    therefore reached the frontend as `Record<string, unknown>` — exactly what
    F4.11 said no longer happened anywhere, and what the F4 header forbids: a
    change in the backend would not break the build, it would break the
    41-field form at runtime. A test compares these fields against the real
    columns, the same way it does for `SettingsUpdate`.
    """

    # -- Modelo
    llm_provider: str
    llm_model: str
    #: Never the key itself. What the screen shows is `llm_api_key_masked` of
    #: `ProfileSummary`; here it only says whether there is one (F6.7).
    llm_api_key: str | None = None
    llm_temperature: float
    llm_timeout_seconds: float
    llm_max_retries: int
    analyst_persona: str | None = None

    # -- Estrategia
    risk_profile: int
    diversification: int
    horizon_days: int
    market: MarketCode
    universe_file: str | None = None
    screener_mode: str
    screener_top_n: int
    screener_min_turnover: float
    screener_min_price: float
    screener_max_volatility_pct: float
    allow_shorts: bool
    excluded_sectors_json: str
    cash_reserve_pct: float
    benchmark: str

    # -- Ejecucion
    initial_budget: float
    bar_interval: str
    lookback_days: int
    cycle_times: str
    cycle_tz: str
    sim_slippage_bps: float
    sim_commission: float
    dry_run: bool
    skip_when_market_closed: bool

    # -- Limites duros. NULL = derivado de los deslizadores.
    advanced_overrides: bool
    risk_per_trade_pct: float | None = None
    max_position_pct: float | None = None
    max_total_exposure_pct: float | None = None
    max_open_positions: int | None = None
    max_daily_loss_pct: float | None = None
    min_conviction: int | None = None
    stop_atr_multiple: float | None = None
    min_reward_risk: float | None = None
    min_order_notional: float | None = None

    extra_json: str
    updated_at: str


class SettingsBundle(BaseModel):
    """The settings and the limits they imply, together.

    They travel together because the F6.8 form needs both at once: it shows the
    slider and, beside it, what that slider means in numbers.
    """

    profile_id: str
    settings: AgentSettings
    limits: DerivedLimits


class SettingsUpdate(BaseModel):
    """A patch over `agent_settings`.

    Only the fields present in the body are applied (`exclude_unset`), which
    makes it possible to tell "do not touch it" from "set it to NULL". The
    difference is not theoretical: on the hard limits, NULL means "derive it from
    the sliders again" (F6.5).
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
    screener_min_turnover: float | None = Field(default=None, ge=0)
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
    """What actually changed. Empty means the body changed nothing.

    The list is returned instead of a plain `ok` because `update_settings`
    ignores fields arriving with the value they already had: without this, the
    interface could not tell "saved" from "saved and something changed too".
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
    #: 'live' = the ingestor's quote; 'cycle' = the price the analyst saw on its
    #: last cycle; None = there is neither. It is labelled because lying about a
    #: price's freshness is worse than not giving it.
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
    #: Calls to the model and how many got no answer (F6.9). With
    #: `analyst_failures == analyst_calls` the cycle is 'failed' and analysed
    #: nothing; with a value in between it did analyse, but it is missing symbols.
    #: Without this pair, "0 decisions" cannot be read.
    analyst_calls: int = 0
    analyst_failures: int = 0


class CycleDetail(CycleRow):
    #: The settings it ran under (F6.3). None for cycles predating that task: it
    #: is missing information, not a zero, and the interface has to be able to say
    #: "we do not know which settings it ran with".
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
    #: Drop from the previous peak, in %. Negative or zero, never positive.
    #:
    #: It is computed on the server on purpose: it is the same definition
    #: `run.py report` uses, and having it in TypeScript too would be having it
    #: twice and condemning the two to disagree.
    drawdown_pct: float = 0.0


class CalibrationBucket(BaseModel):
    """One bar of the chart that decides the experiment.

    If `win_rate_pct` does not grow with `conviction_bucket`, the conviction the
    model declares informs nothing and we are trading on expensive noise.
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
    """The five chart series (F4.6), in a single trip.

    Together and not in five endpoints because they are one single screen: five
    requests would give five loading states and five ways of half-failing in
    order to read five aggregates of the same local file.
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
    #: Start of the bar according to the provider.
    as_of: str
    #: When we wrote it. The gap between the two is the datum's real lag, which
    #: is the open question of F2.1c in Europe.
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
    """Ingestor health.

    The averages are computed **over the ticks only**: a gap backfill downloads
    several days at once, so a single one of its rows would shift any latency
    average and this panel would start measuring something else (F2.10).
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
    #: Why we say it is healthy or not, in one sentence.
    message: str


# ----------------------------------------------------------------------
# Control de ciclos (F3.4)
# ----------------------------------------------------------------------

class CycleRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str | None = None
    dry_run: bool = False


class CycleControl(BaseModel):
    """State of the cycle launched from the interface.

    `enabled=False` means the controls are switched off: the API still serves,
    but with no button to fire anything (F3.8).
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
