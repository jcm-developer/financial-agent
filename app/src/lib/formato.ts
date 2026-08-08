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

export function dinero(valor: number | null | undefined, simbolo: string): string {
  if (valor === null || valor === undefined) return "—";
  return `${NUMERO.format(valor)} ${simbolo}`;
}

/** Con signo delante, que es lo que se quiere ver en un P&L. */
export function dineroConSigno(
  valor: number | null | undefined,
  simbolo: string,
): string {
  if (valor === null || valor === undefined) return "—";
  return `${valor >= 0 ? "+" : "−"}${NUMERO.format(Math.abs(valor))} ${simbolo}`;
}

export function porcentaje(
  valor: number | null | undefined,
  { signo = false }: { signo?: boolean } = {},
): string {
  if (valor === null || valor === undefined) return "—";
  const cuerpo = `${NUMERO.format(Math.abs(valor))}%`;
  if (!signo) return `${NUMERO.format(valor)}%`;
  return `${valor >= 0 ? "+" : "−"}${cuerpo}`;
}

export function entero(valor: number | null | undefined): string {
  if (valor === null || valor === undefined) return "—";
  return ENTERO.format(valor);
}

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

export function hora(iso: string | null | undefined): string {
  if (!iso) return "—";
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return iso;
  return fecha.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
}

/** Duración en segundos como «2 min 14 s», para los ciclos. */
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
 */
export function claseSigno(valor: number | null | undefined): string {
  if (valor === null || valor === undefined) return "text-text-muted";
  if (valor > 0) return "text-delta-good";
  if (valor < 0) return "text-delta-bad";
  return "text-text-secondary";
}
