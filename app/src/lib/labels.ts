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

function lookup(table: Record<string, string>, value: string | null | undefined): string {
  if (!value) return "";
  return table[value] ?? value;
}

export const cycleStatusLabel = (value: string | null | undefined) => lookup(CYCLE_STATUS, value);
export const orderStatusLabel = (value: string | null | undefined) => lookup(ORDER_STATUS, value);
export const actionLabel = (value: string | null | undefined) => lookup(ACTION, value);
export const profileStatusLabel = (value: string | null | undefined) =>
  lookup(PROFILE_STATUS, value);
