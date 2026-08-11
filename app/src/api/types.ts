// Generado por tools/gen_api_types.py a partir del OpenAPI de la API.
// NO EDITAR A MANO: se regenera con  python tools/gen_api_types.py
//
// Los nombres salen de api/models.py. Si algo aqui no cuadra con lo que
// devuelve el servidor, el que manda es el servidor y este fichero esta
// desfasado.


export interface ActionResult {
  ok: boolean;
  message: string;
}

/**
 * One row of `agent_settings`, as it is read.
 *
 * ⚠️ **It is the read counterpart of `SettingsUpdate`, and it is not the same
 * shape**, which is why they are two models and not one with everything
 * optional:
 *
 *   * The **hard limits come back NULL** when they are derived from the
 *     sliders. That NULL is the datum (F6.5): it means "recompute it", and a
 *     model that filled it in with a number would erase the difference between
 *     a limit that was chosen and one that was inherited.
 *   * The **booleans come back as 0/1**, because SQLite has no boolean. They
 *     are declared `bool` so Pydantic converts them once, here, instead of
 *     every screen deciding whether `0` is false.
 *
 * It exists because until F6.8 this endpoint answered a plain `dict` and
 * therefore reached the frontend as `Record<string, unknown>` — exactly what
 * F4.11 said no longer happened anywhere, and what the F4 header forbids: a
 * change in the backend would not break the build, it would break the
 * 41-field form at runtime. A test compares these fields against the real
 * columns, the same way it does for `SettingsUpdate`.
 */
export interface AgentSettings {
  llm_provider: string;
  llm_model: string;
  llm_api_key?: string | null;
  llm_temperature: number;
  llm_timeout_seconds: number;
  llm_max_retries: number;
  analyst_persona?: string | null;
  risk_profile: number;
  diversification: number;
  horizon_days: number;
  market: "us" | "eu";
  universe_file?: string | null;
  screener_mode: string;
  screener_top_n: number;
  screener_min_turnover: number;
  screener_min_price: number;
  screener_max_volatility_pct: number;
  allow_shorts: boolean;
  excluded_sectors_json: string;
  cash_reserve_pct: number;
  benchmark: string;
  initial_budget: number;
  bar_interval: string;
  lookback_days: number;
  cycle_times: string;
  cycle_tz: string;
  sim_slippage_bps: number;
  sim_commission: number;
  dry_run: boolean;
  skip_when_market_closed: boolean;
  advanced_overrides: boolean;
  risk_per_trade_pct?: number | null;
  max_position_pct?: number | null;
  max_total_exposure_pct?: number | null;
  max_open_positions?: number | null;
  max_daily_loss_pct?: number | null;
  min_conviction?: number | null;
  stop_atr_multiple?: number | null;
  min_reward_risk?: number | null;
  min_order_notional?: number | null;
  extra_json: string;
  updated_at: string;
}

/**
 * The five chart series (F4.6), in a single trip.
 *
 * Together and not in five endpoints because they are one single screen: five
 * requests would give five loading states and five ways of half-failing in
 * order to read five aggregates of the same local file.
 */
export interface Analytics {
  equity_curve?: Array<EquityPoint>;
  calibration?: Array<CalibrationBucket>;
  rejections?: Array<RejectionCount>;
  by_symbol?: Array<SymbolPerformance>;
  conviction_histogram?: Array<ConvictionBucket>;
}

/**
 * One bar of the chart that decides the experiment.
 *
 * If `win_rate_pct` does not grow with `conviction_bucket`, the conviction the
 * model declares informs nothing and we are trading on expensive noise.
 */
export interface CalibrationBucket {
  conviction_bucket: number;
  trades: number;
  avg_pnl?: number | null;
  win_rate_pct?: number | null;
}

export interface ConvictionBucket {
  bucket: number;
  buys?: number;
  holds?: number;
  sells?: number;
  total?: number;
}

/**
 * State of the cycle launched from the interface.
 *
 * `enabled=False` means the controls are switched off: the API still serves,
 * but with no button to fire anything (F3.8).
 */
export interface CycleControl {
  enabled: boolean;
  running: boolean;
  external?: boolean;
  stop_requested?: boolean;
  profile?: string | null;
  dry_run?: boolean;
  stage: string;
  started_at?: string | null;
  finished_at?: string | null;
  elapsed_seconds?: number | null;
  returncode?: number | null;
  lines?: Array<string>;
}

export interface CycleDetail {
  id: string;
  started_at: string;
  finished_at?: string | null;
  status: string;
  equity_start?: number | null;
  equity_end?: number | null;
  equity_delta?: number | null;
  market_open?: boolean | null;
  llm_model?: string | null;
  error?: string | null;
  decisions?: number;
  approved?: number;
  rejected?: number;
  orders?: number;
  symbols_scanned?: Array<string>;
  analyst_calls?: number;
  analyst_failures?: number;
  settings?: Record<string, unknown> | null;
}

export interface CycleRow {
  id: string;
  started_at: string;
  finished_at?: string | null;
  status: string;
  equity_start?: number | null;
  equity_end?: number | null;
  equity_delta?: number | null;
  market_open?: boolean | null;
  llm_model?: string | null;
  error?: string | null;
  decisions?: number;
  approved?: number;
  rejected?: number;
  orders?: number;
  symbols_scanned?: Array<string>;
  analyst_calls?: number;
  analyst_failures?: number;
}

export interface CycleRunRequest {
  profile?: string | null;
  dry_run?: boolean;
}

export interface DecisionRow {
  id: string;
  cycle_id: string;
  created_at: string;
  symbol: string;
  kind: string;
  action: string;
  conviction: number;
  thesis?: string | null;
  risks?: string | null;
  horizon_days?: number | null;
  reference_price?: number | null;
  suggested_stop?: number | null;
  suggested_target?: number | null;
  suggested_weight_pct?: number | null;
  llm_model?: string | null;
  latency_ms?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  verdict?: string | null;
  rule?: string | null;
  risk_reason?: string | null;
  approved_qty?: number | null;
  approved_notional?: number | null;
  order_status?: string | null;
  filled_avg_price?: number | null;
}

/**
 * The nine effective limits and where each one comes from.
 *
 * `derived_fields` is what the interface paints in grey: the limits that come
 * from the sliders and not from a number written by hand (F6.8).
 */
export interface DerivedLimits {
  risk_per_trade_pct: number;
  max_position_pct: number;
  max_total_exposure_pct: number;
  max_open_positions: number;
  max_daily_loss_pct: number;
  min_conviction: number;
  stop_atr_multiple: number;
  min_reward_risk: number;
  min_order_notional: number;
  sector_cap?: number | null;
  derived_fields: Array<string>;
  summary: string;
}

export interface EquityPoint {
  as_of: string;
  equity: number;
  cash?: number | null;
  positions_value?: number | null;
  open_positions?: number;
  day_pnl_pct?: number | null;
  drawdown_pct?: number;
}

export interface HTTPValidationError {
  detail?: Array<ValidationError>;
}

export interface IngestRun {
  id: number;
  started_at: string;
  finished_at?: string | null;
  kind: string;
  symbols_requested: number;
  symbols_ok: number;
  symbols_failed: number;
  latency_ms?: number | null;
  rate_limited: boolean;
  error?: string | null;
}

/**
 * Ingestor health.
 *
 * The averages are computed **over the ticks only**: a gap backfill downloads
 * several days at once, so a single one of its rows would shift any latency
 * average and this panel would start measuring something else (F2.10).
 */
export interface IngestStatus {
  healthy: boolean;
  last_tick_at?: string | null;
  seconds_since_last_tick?: number | null;
  consecutive_failures?: number;
  rate_limited_recently?: boolean;
  avg_latency_ms?: number | null;
  symbols_tracked?: number;
  symbols_by_market?: Record<string, number>;
  bars_stored?: number;
  quotes_stored?: number;
  last_backfill_at?: string | null;
  recent?: Array<IngestRun>;
  message: string;
}

/**
 * One exchange from the registry in `src/market_calendar.py`.
 *
 * The interface needs it for the profile creation form (F5.3) and so as not to
 * write '$' in a European profile. None of this is inferred in the frontend:
 * the currency, the hours and the liquidity floor are properties of the market
 * and live in a single place.
 */
export interface MarketInfo {
  code: string;
  label: string;
  timezone: string;
  currency: string;
  currency_symbol: string;
  benchmark: string;
  universe_file: string;
  universe_size: number;
  min_turnover: number;
  session_open: string;
  session_close: string;
  operating_open: string;
  operating_close: string;
  session_minutes: number;
  operating_minutes: number;
  is_trading_day: boolean;
  is_session_open: boolean;
  is_operating: boolean;
  status_text: string;
}

export interface OrderRow {
  id: string;
  cycle_id?: string | null;
  decision_id?: string | null;
  submitted_at: string;
  updated_at: string;
  symbol: string;
  side: string;
  qty: number;
  order_type: string;
  status: string;
  filled_qty?: number | null;
  filled_avg_price?: number | null;
  stop_price?: number | null;
  target_price?: number | null;
  broker_order_id?: string | null;
  error?: string | null;
}

export interface Page_CycleRow {
  items: Array<CycleRow>;
  total: number;
  limit: number;
  offset: number;
}

export interface Page_DecisionRow {
  items: Array<DecisionRow>;
  total: number;
  limit: number;
  offset: number;
}

export interface Page_OrderRow {
  items: Array<OrderRow>;
  total: number;
  limit: number;
  offset: number;
}

export interface Page_PositionRow {
  items: Array<PositionRow>;
  total: number;
  limit: number;
  offset: number;
}

export interface Page_RiskEventRow {
  items: Array<RiskEventRow>;
  total: number;
  limit: number;
  offset: number;
}

export interface Page_SettingsHistoryRow {
  items: Array<SettingsHistoryRow>;
  total: number;
  limit: number;
  offset: number;
}

export interface PositionRow {
  id: string;
  symbol: string;
  status: string;
  qty: number;
  entry_price: number;
  stop_price?: number | null;
  target_price?: number | null;
  thesis?: string | null;
  horizon_days?: number | null;
  opened_at: string;
  closed_at?: string | null;
  exit_price?: number | null;
  realized_pnl?: number | null;
  exit_reason?: string | null;
  last_price?: number | null;
  last_price_as_of?: string | null;
  price_source?: "live" | "cycle" | null;
  market_value?: number | null;
  unrealized_pnl?: number | null;
  unrealized_pnl_pct?: number | null;
  stop_distance_pct?: number | null;
  entry_commission?: number | null;
}

export interface ProfileCreate {
  name: string;
  market?: "us" | "eu";
  description?: string;
  budget?: number;
  watch?: number;
}

export interface ProfileDetail {
  id: string;
  name: string;
  description?: string | null;
  status: "draft" | "active" | "paused" | "archived";
  created_at: string;
  updated_at: string;
  portfolio_id?: string | null;
  market: string;
  currency: string;
  currency_symbol: string;
  llm_provider: string;
  llm_model: string;
  llm_api_key_masked: string;
  universe_file?: string | null;
  screener_mode?: string;
  watched_symbols?: number;
  risk_summary: string;
  metrics: ProfileMetrics;
  settings: Record<string, unknown>;
  limits: DerivedLimits;
  universe: Array<string>;
  market_info: MarketInfo;
}

export interface ProfileDuplicate {
  name: string;
  description?: string;
}

/**
 * The figures on the profile card (F5.2).
 */
export interface ProfileMetrics {
  equity?: number | null;
  cash?: number | null;
  initial_budget?: number | null;
  total_return_pct?: number | null;
  day_pnl_pct?: number | null;
  equity_as_of?: string | null;
  open_positions?: number;
  closed_trades?: number;
  win_rate_pct?: number | null;
  realized_pnl?: number | null;
  cycles?: number;
  decisions?: number;
  last_cycle_at?: string | null;
  last_cycle_status?: string | null;
}

export interface ProfilePatch {
  name?: string | null;
  description?: string | null;
  status?: "draft" | "active" | "paused" | "archived" | null;
}

export interface ProfileSummary {
  id: string;
  name: string;
  description?: string | null;
  status: "draft" | "active" | "paused" | "archived";
  created_at: string;
  updated_at: string;
  portfolio_id?: string | null;
  market: string;
  currency: string;
  currency_symbol: string;
  llm_provider: string;
  llm_model: string;
  llm_api_key_masked: string;
  universe_file?: string | null;
  screener_mode?: string;
  watched_symbols?: number;
  risk_summary: string;
  metrics: ProfileMetrics;
}

export interface QuoteRow {
  symbol: string;
  price: number;
  prev_close?: number | null;
  change_pct?: number | null;
  volume?: number | null;
  as_of: string;
  updated_at: string;
  age_seconds?: number | null;
}

export interface RejectionCount {
  rule: string;
  rejections: number;
  last_seen?: string | null;
}

export interface RiskEventRow {
  id: string;
  cycle_id: string;
  decision_id?: string | null;
  created_at: string;
  symbol?: string | null;
  verdict: string;
  rule?: string | null;
  reason: string;
  approved_qty?: number | null;
  approved_notional?: number | null;
  stop_price?: number | null;
  target_price?: number | null;
}

/**
 * What actually changed. Empty means the body changed nothing.
 *
 * The list is returned instead of a plain `ok` because `update_settings`
 * ignores fields arriving with the value they already had: without this, the
 * interface could not tell "saved" from "saved and something changed too".
 */
export interface SettingsApplied {
  applied: Array<string>;
  limits: DerivedLimits;
}

/**
 * The settings and the limits they imply, together.
 *
 * They travel together because the F6.8 form needs both at once: it shows the
 * slider and, beside it, what that slider means in numbers.
 */
export interface SettingsBundle {
  profile_id: string;
  settings: AgentSettings;
  limits: DerivedLimits;
}

export interface SettingsHistoryRow {
  id: number;
  field: string;
  old_value?: string | null;
  new_value?: string | null;
  source?: string | null;
  changed_at: string;
}

/**
 * A patch over `agent_settings`.
 *
 * Only the fields present in the body are applied (`exclude_unset`), which
 * makes it possible to tell "do not touch it" from "set it to NULL". The
 * difference is not theoretical: on the hard limits, NULL means "derive it from
 * the sliders again" (F6.5).
 */
export interface SettingsUpdate {
  llm_provider?: "nvidia" | "openai" | "anthropic" | null;
  llm_model?: string | null;
  llm_api_key?: string | null;
  llm_temperature?: number | null;
  llm_timeout_seconds?: number | null;
  llm_max_retries?: number | null;
  analyst_persona?: string | null;
  risk_profile?: number | null;
  diversification?: number | null;
  horizon_days?: number | null;
  market?: "us" | "eu" | null;
  universe_file?: string | null;
  screener_mode?: "score" | "random" | null;
  screener_top_n?: number | null;
  screener_min_turnover?: number | null;
  screener_min_price?: number | null;
  screener_max_volatility_pct?: number | null;
  allow_shorts?: boolean | null;
  excluded_sectors_json?: string | null;
  cash_reserve_pct?: number | null;
  benchmark?: string | null;
  initial_budget?: number | null;
  bar_interval?: "1m" | "1h" | "1d" | null;
  lookback_days?: number | null;
  cycle_times?: string | null;
  cycle_tz?: string | null;
  sim_slippage_bps?: number | null;
  sim_commission?: number | null;
  dry_run?: boolean | null;
  skip_when_market_closed?: boolean | null;
  advanced_overrides?: boolean | null;
  risk_per_trade_pct?: number | null;
  max_position_pct?: number | null;
  max_total_exposure_pct?: number | null;
  max_open_positions?: number | null;
  max_daily_loss_pct?: number | null;
  min_conviction?: number | null;
  stop_atr_multiple?: number | null;
  min_reward_risk?: number | null;
  min_order_notional?: number | null;
  extra_json?: string | null;
}

export interface SymbolPerformance {
  symbol: string;
  trades: number;
  wins: number;
  win_rate_pct?: number | null;
  total_pnl?: number | null;
  avg_pnl?: number | null;
  avg_holding_days?: number | null;
}

export interface UniverseUpdate {
  symbols: Array<string>;
}

export interface ValidationError {
  loc: Array<number | string>;
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

/** Operaciones de la API: 'METODO /ruta' -> tipo de la respuesta. */
export interface ApiOperations {
  /** Analytics */
  "GET /api/analytics": Analytics;
  /** Cycles */
  "GET /api/cycles": Page_CycleRow;
  /** Close Experiment */
  "POST /api/cycles/close-experiment": CycleControl;
  /** Control Status */
  "GET /api/cycles/control/status": CycleControl;
  /** Run Cycle */
  "POST /api/cycles/run": CycleControl;
  /** Stop Cycle */
  "POST /api/cycles/stop": ActionResult;
  /** Cycle Detail */
  "GET /api/cycles/{cycle_id}": CycleDetail;
  /** Decisions */
  "GET /api/decisions": Page_DecisionRow;
  /** Ingest Status */
  "GET /api/ingest-status": IngestStatus;
  /** Markets */
  "GET /api/markets": Array<MarketInfo>;
  /** Market */
  "GET /api/markets/{code}": MarketInfo;
  /** Orders */
  "GET /api/orders": Page_OrderRow;
  /** Positions */
  "GET /api/positions": Page_PositionRow;
  /** List Profiles */
  "GET /api/profiles": Array<ProfileSummary>;
  /** Create Profile */
  "POST /api/profiles": ProfileDetail;
  /** Limits Preview */
  "GET /api/profiles/limits-preview": DerivedLimits;
  /** Delete Profile */
  "DELETE /api/profiles/{profile_ref}": ActionResult;
  /** Get Profile */
  "GET /api/profiles/{profile_ref}": ProfileDetail;
  /** Patch Profile */
  "PATCH /api/profiles/{profile_ref}": ProfileDetail;
  /** Duplicate */
  "POST /api/profiles/{profile_ref}/duplicate": ProfileDetail;
  /** Get Limits */
  "GET /api/profiles/{profile_ref}/limits": DerivedLimits;
  /** Get Settings */
  "GET /api/profiles/{profile_ref}/settings": SettingsBundle;
  /** Patch Settings */
  "PATCH /api/profiles/{profile_ref}/settings": SettingsApplied;
  /** Get Settings History */
  "GET /api/profiles/{profile_ref}/settings/history": Page_SettingsHistoryRow;
  /** Put Universe */
  "PUT /api/profiles/{profile_ref}/universe": ActionResult;
  /** Quotes */
  "GET /api/quotes": Array<QuoteRow>;
  /** Risk Events */
  "GET /api/risk-events": Page_RiskEventRow;
  /** Stream */
  "GET /api/stream": unknown;
}
