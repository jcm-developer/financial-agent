/**
 * Formato de cifras y fechas.
 *
 * **El símbolo de la divisa se pasa siempre, nunca se asume.** Es FE.8: un
 * presupuesto europeo escrito con `$` invita a compararlo con el de otro
 * experimento como si fuera la misma unidad, y el proyecto no convierte divisa en
 * ningún sitio. Cada perfil trae el suyo (`currency_symbol`), que sale de su
 * mercado, así que aquí es un parámetro obligatorio y no un valor por defecto.
 */

const NUMERO = new Intl.NumberFormat("es-ES", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const ENTERO = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });

/**
 * Formats a monetary amount followed by its currency symbol.
 *
 * @param valor - Amount to format. Null or undefined renders as an em dash.
 * @param simbolo - Currency symbol of the profile's market.
 * @return The formatted amount, or `—` when there is no value.
 */
export function dinero(valor: number | null | undefined, simbolo: string): string {
  if (valor === null || valor === undefined) return "—";
  return `${NUMERO.format(valor)} ${simbolo}`;
}

/**
 * Formats a monetary amount with an explicit leading sign.
 *
 * Con signo delante, que es lo que se quiere ver en un P&L.
 *
 * @param valor - Amount to format. Null or undefined renders as an em dash.
 * @param simbolo - Currency symbol of the profile's market.
 * @return The signed amount, or `—` when there is no value.
 */
export function dineroConSigno(
  valor: number | null | undefined,
  simbolo: string,
): string {
  if (valor === null || valor === undefined) return "—";
  return `${valor >= 0 ? "+" : "−"}${NUMERO.format(Math.abs(valor))} ${simbolo}`;
}

/**
 * Formats a percentage already expressed in points, so 3.5 renders as `3,50%`.
 *
 * @param valor - Percentage points. Null or undefined renders as an em dash.
 * @param options - Formatting options.
 * @param options.signo - Whether to prefix an explicit `+` or `−`.
 * @return The formatted percentage, or `—` when there is no value.
 */
export function porcentaje(
  valor: number | null | undefined,
  { signo = false }: { signo?: boolean } = {},
): string {
  if (valor === null || valor === undefined) return "—";
  const cuerpo = `${NUMERO.format(Math.abs(valor))}%`;
  if (!signo) return `${NUMERO.format(valor)}%`;
  return `${valor >= 0 ? "+" : "−"}${cuerpo}`;
}

/**
 * Formats a number with no decimal places.
 *
 * @param valor - Number to format. Null or undefined renders as an em dash.
 * @return The rounded number, or `—` when there is no value.
 */
export function entero(valor: number | null | undefined): string {
  if (valor === null || valor === undefined) return "—";
  return ENTERO.format(valor);
}

/**
 * Formats a share count, keeping decimals only when the position is fractional.
 *
 * @param valor - Share count. Null or undefined renders as an em dash.
 * @return The formatted count, or `—` when there is no value.
 */
export function cantidad(valor: number | null | undefined): string {
  if (valor === null || valor === undefined) return "—";
  // Las acciones pueden ser fraccionarias, pero enseñar "100,00" cuando son 100
  // enteras solo añade ruido a una columna que se lee en vertical.
  return Number.isInteger(valor) ? ENTERO.format(valor) : NUMERO.format(valor);
}

/**
 * Fecha y hora en local. Las marcas de la API son ISO en UTC.
 *
 * Se enseña en hora local a propósito: quien mira el experimento lo compara con
 * su propia mañana, y «el ciclo de las 11:20» tiene que coincidir con la hora del
 * reloj de la pared. La marca original queda en el `title` para que no se pierda.
 *
 * @param iso - ISO-8601 UTC timestamp as returned by the API.
 * @return Day, month, hour and minute in local time; the input unchanged when it
 *     is not a parsable date, or `—` when it is empty.
 */
export function fechaHora(iso: string | null | undefined): string {
  if (!iso) return "—";
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return iso;
  return fecha.toLocaleString("es-ES", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Same as {@link fechaHora} but dropping the date, for same-day columns.
 *
 * @param iso - ISO-8601 UTC timestamp as returned by the API.
 * @return Hour and minute in local time; the input unchanged when it is not a
 *     parsable date, or `—` when it is empty.
 */
export function hora(iso: string | null | undefined): string {
  if (!iso) return "—";
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return iso;
  return fecha.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
}

/**
 * Formats an elapsed time as `2 min 14 s`.
 *
 * Duración en segundos como «2 min 14 s», para los ciclos.
 *
 * @param segundos - Elapsed seconds. Null or undefined renders as an em dash.
 * @return The formatted duration, or `—` when there is no value.
 */
export function duracion(segundos: number | null | undefined): string {
  if (segundos === null || segundos === undefined) return "—";
  const total = Math.round(segundos);
  if (total < 60) return `${total} s`;
  const minutos = Math.floor(total / 60);
  const resto = total % 60;
  return resto ? `${minutos} min ${resto} s` : `${minutos} min`;
}

/**
 * La clase de color de una cifra con signo.
 *
 * Usa `delta-good`/`delta-bad` y no `positive`/`negative`: son pares distintos a
 * propósito (ver `index.css`). El texto de una variación puede usar verde porque
 * no compite con ninguna serie de una gráfica.
 *
 * @param valor - Signed value the colour describes.
 * @return The Tailwind text-colour class for that sign.
 */
export function claseSigno(valor: number | null | undefined): string {
  if (valor === null || valor === undefined) return "text-text-muted";
  if (valor > 0) return "text-delta-good";
  if (valor < 0) return "text-delta-bad";
  return "text-text-secondary";
}
