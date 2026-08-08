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
 * Las cinco series de las graficas (F4.6), en un solo viaje.
 *
 * Juntas y no en cinco endpoints porque son una sola pantalla: cinco peticiones
 * darian cinco estados de carga y cinco formas de fallar a medias para leer
 * cinco agregados del mismo fichero local.
 */
export interface Analytics {
  equity_curve?: Array<EquityPoint>;
  calibration?: Array<CalibrationBucket>;
  rejections?: Array<RejectionCount>;
  by_symbol?: Array<SymbolPerformance>;
  conviction_histogram?: Array<ConvictionBucket>;
}

/**
 * Una barra del grafico que decide el experimento.
 *
 * Si el `win_rate_pct` no crece con el `conviction_bucket`, la conviccion que
 * declara el modelo no informa de nada y se esta operando con ruido caro.
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
 * Estado del ciclo lanzado desde la interfaz.
 *
 * `enabled=False` significa que los controles estan apagados: la API sirve
 * igual, pero sin boton de disparar nada (F3.8).
 */
export interface CycleControl {
  enabled: boolean;
  running: boolean;
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
 * Los nueve limites efectivos y de donde sale cada uno.
 *
 * `derived_fields` es lo que la interfaz pinta en gris: los limites que salen
 * de los deslizadores y no de un numero escrito a mano (F6.8).
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
 * Salud del ingestor.
 *
 * Las medias se calculan **solo sobre los ticks**: un relleno de huecos
 * descarga varios dias de golpe, asi que una sola de sus filas desplazaria
 * cualquier media de latencia y este panel pasaria a medir otra cosa (F2.10).
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
 * Una bolsa del registro de `src/market_calendar.py`.
 *
 * La interfaz la necesita para el alta de perfil (F5.3) y para no escribir '$'
 * en un perfil europeo. Nada de esto se deduce en el frontend: la divisa, el
 * horario y el suelo de liquidez son propiedades del mercado y viven en un
 * solo sitio.
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
 * Las cifras de la tarjeta de perfil (F5.2).
 */
export interface ProfileMetrics {
  equity?: number | null;
  initial_budget?: number | null;
  total_return_pct?: number | null;
  day_pnl_pct?: number | null;
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
 * Que cambio de verdad. Vacio significa que el cuerpo no cambiaba nada.
 *
 * Se devuelve la lista y no un simple `ok` porque `update_settings` ignora los
 * campos que llegan con el valor que ya tenian: sin esto, la interfaz no podria
 * distinguir "guardado" de "guardado y ademas cambio algo".
 */
export interface SettingsApplied {
  applied: Array<string>;
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
 * Un parche sobre `agent_settings`.
 *
 * Solo se aplican los campos presentes en el cuerpo (`exclude_unset`), lo que
 * permite distinguir "no lo toques" de "ponlo a NULL". La diferencia no es
 * teorica: en los limites duros, NULL significa "vuelve a derivarlo de los
 * deslizadores" (F6.5).
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
  screener_min_dollar_volume?: number | null;
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
  "GET /api/profiles/{profile_ref}/settings": Record<string, unknown>;
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
