/**
 * Screen words for the enum values the API sends in English.
 *
 * The API keeps the schema's values (`completed`, `filled`, `buy`) because they
 * are data: filters, colours and tests compare against them. What the screen
 * prints is another matter, and printing the raw value left English words in a
 * Spanish interface. An unknown value falls through unchanged rather than
 * vanishing, so a status added to the schema later still shows up.
 */

const CYCLE_STATUS: Record<string, string> = {
  running: "en curso",
  completed: "completado",
  failed: "fallido",
  halted: "detenido",
};

const ORDER_STATUS: Record<string, string> = {
  submitted: "enviada",
  accepted: "aceptada",
  filled: "ejecutada",
  partially_filled: "ejecutada en parte",
  rejected: "rechazada",
  canceled: "cancelada",
  failed: "fallida",
  dry_run: "simulada",
};

const ACTION: Record<string, string> = {
  buy: "compra",
  sell: "venta",
  hold: "mantener",
};

const PROFILE_STATUS: Record<string, string> = {
  draft: "borrador",
  active: "activo",
  paused: "pausado",
  archived: "archivado",
};

/**
 * The Risk Manager's and the cycle's rule keys, as the screens say them. The
 * keys stay in SQL; the wording matches `src/risk.py` and `src/cycle.py`.
 */
const RULE: Record<string, string> = {
  risk_per_trade: "riesgo por operación",
  max_position_pct: "tamaño máximo por posición",
  max_total_exposure_pct: "exposición máxima",
  max_open_positions: "máximo de posiciones",
  insufficient_cash: "efectivo disponible",
  suggested_weight: "peso propuesto",
  conviction: "convicción",
  min_conviction: "convicción mínima",
  min_order_notional: "orden mínima",
  min_reward_risk: "beneficio/riesgo mínimo",
  min_target_sigma: "objetivo demasiado cercano",
  qty_below_one: "menos de una acción",
  already_open: "posición ya abierta",
  atr_unavailable: "sin ATR",
  stop_below_zero: "stop por debajo de cero",
  invalid_price: "precio no válido",
  no_equity: "sin capital",
  non_positive_risk: "riesgo no positivo",
  action_not_buy: "no es una compra",
  stop_loss_hit: "stop alcanzado",
  take_profit_hit: "objetivo alcanzado",
  llm_exit: "salida del analista",
  experiment_closed: "experimento cerrado",
  entry_cap: "sin plaza en el ciclo",
  add_to_loser: "promediar a la baja",
  weight_not_above_current: "peso por debajo del actual",
  weight_not_below_current: "peso por encima del actual",
  trim_too_small: "venta parcial demasiado pequeña",
  llm_trim: "venta parcial del analista",
  action_not_sell: "no es una venta",
};

function lookup(table: Record<string, string>, value: string | null | undefined): string {
  if (!value) return "";
  return table[value] ?? value;
}

export const cycleStatusLabel = (value: string | null | undefined) => lookup(CYCLE_STATUS, value);
export const orderStatusLabel = (value: string | null | undefined) => lookup(ORDER_STATUS, value);
export const actionLabel = (value: string | null | undefined) => lookup(ACTION, value);

/**
 * What a decision's action means, which depends on what was being judged.
 *
 * On a review of an open position `buy` is an add and a `sell` that names a
 * weight to keep is a partial sale; only a `sell` with no weight closes it all
 * (`src/risk.py`, `evaluate_trim`). Printing "compra" on a review would read as
 * a second position in the same name, which the schema does not allow.
 */
export function decisionActionLabel(
  action: string | null | undefined,
  kind: string | null | undefined,
  weightPct: number | null | undefined,
): string {
  if (kind === "exit" && action === "buy") return "ampliar";
  if (kind === "exit" && action === "sell" && weightPct != null) return "reducir";
  return actionLabel(action);
}
export const profileStatusLabel = (value: string | null | undefined) =>
  lookup(PROFILE_STATUS, value);
export const ruleLabel = (value: string | null | undefined) => lookup(RULE, value);
