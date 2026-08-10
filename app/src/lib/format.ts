/**
 * Number and date formatting.
 *
 * **The currency symbol is always passed in, never assumed.** That is FE.8: a
 * European budget written with `$` invites comparing it against another
 * experiment's as if they were the same unit, and the project converts currency
 * nowhere. Each profile carries its own (`currency_symbol`), derived from its
 * market, so here it is a required parameter and not a default.
 */

const NUMBER = new Intl.NumberFormat("es-ES", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const INTEGER = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });

/**
 * Formats a monetary amount followed by its currency symbol.
 *
 * @param value - Amount to format. Null or undefined renders as an em dash.
 * @param symbol - Currency symbol of the profile's market.
 * @return The formatted amount, or `—` when there is no value.
 */
export function money(value: number | null | undefined, symbol: string): string {
  if (value === null || value === undefined) return "—";
  return `${NUMBER.format(value)} ${symbol}`;
}

/**
 * Formats a monetary amount with an explicit leading sign.
 *
 * A leading sign is what you want to read in a P&L.
 *
 * @param value - Amount to format. Null or undefined renders as an em dash.
 * @param symbol - Currency symbol of the profile's market.
 * @return The signed amount, or `—` when there is no value.
 */
export function signedMoney(
  value: number | null | undefined,
  symbol: string,
): string {
  if (value === null || value === undefined) return "—";
  return `${value >= 0 ? "+" : "−"}${NUMBER.format(Math.abs(value))} ${symbol}`;
}

/**
 * Formats a percentage already expressed in points, so 3.5 renders as `3,50%`.
 *
 * @param value - Percentage points. Null or undefined renders as an em dash.
 * @param options - Formatting options.
 * @param options.sign - Whether to prefix an explicit `+` or `−`.
 * @return The formatted percentage, or `—` when there is no value.
 */
export function percent(
  value: number | null | undefined,
  { sign = false }: { sign?: boolean } = {},
): string {
  if (value === null || value === undefined) return "—";
  const body = `${NUMBER.format(Math.abs(value))}%`;
  if (!sign) return `${NUMBER.format(value)}%`;
  return `${value >= 0 ? "+" : "−"}${body}`;
}

/**
 * Formats a number with no decimal places.
 *
 * @param value - Number to format. Null or undefined renders as an em dash.
 * @return The rounded number, or `—` when there is no value.
 */
export function integer(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return INTEGER.format(value);
}

/**
 * Formats a share count, keeping decimals only when the position is fractional.
 *
 * @param value - Share count. Null or undefined renders as an em dash.
 * @return The formatted count, or `—` when there is no value.
 */
export function quantity(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  // Shares can be fractional, but showing "100,00" for a round 100 only adds
  // noise to a column that is read vertically.
  return Number.isInteger(value) ? INTEGER.format(value) : NUMBER.format(value);
}

/**
 * Date and time in local time. The API's marks are ISO in UTC.
 *
 * Local time is deliberate: whoever watches the experiment compares it against
 * their own morning, and "the 11:20 cycle" has to match the clock on the wall.
 * The original mark stays in the `title` so it is not lost.
 *
 * @param iso - ISO-8601 UTC timestamp as returned by the API.
 * @return Day, month, hour and minute in local time; the input unchanged when it
 *     is not a parsable date, or `—` when it is empty.
 */
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("es-ES", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Same as {@link dateTime} but dropping the date, for same-day columns.
 *
 * @param iso - ISO-8601 UTC timestamp as returned by the API.
 * @return Hour and minute in local time; the input unchanged when it is not a
 *     parsable date, or `—` when it is empty.
 */
export function time(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
}

/**
 * Formats an elapsed time as `2 min 14 s`.
 *
 * @param seconds - Elapsed seconds. Null or undefined renders as an em dash.
 * @return The formatted duration, or `—` when there is no value.
 */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.round(seconds);
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
}

/**
 * The colour class of a signed figure.
 *
 * Uses `delta-good`/`delta-bad` and not `positive`/`negative`, which is the
 * ink/mark split of the design system: these two are the **text** tier
 * (#16A34A / #DC2626), deep enough to be read as letters, while
 * `positive`/`negative` are the saturated fills a chart paints with.
 *
 * **The null case is the one that matters.** "No hay dato" and "cero" get
 * different colours, because a P&L of zero for want of a price is not a P&L of
 * zero.
 *
 * @param value - Signed value the colour describes.
 * @return The Tailwind text-colour class for that sign.
 */
export function signClass(value: number | null | undefined): string {
  if (value === null || value === undefined) return "text-text-muted";
  if (value > 0) return "text-delta-good";
  if (value < 0) return "text-delta-bad";
  return "text-text-secondary";
}
