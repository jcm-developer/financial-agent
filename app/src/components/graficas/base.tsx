import { useState, type ReactNode } from "react";

import { BotonEnlace, Tarjeta, TituloBloque } from "@/components/piezas";

/**
 * Lo compartido por todas las gráficas.
 *
 * **Los colores se pasan como `var(--series-1)` y no como hexadecimal.** Los
 * atributos de presentación de SVG aceptan variables CSS, así que el interruptor
 * de tema repinta las gráficas solo: sin eso habría que leer los colores con
 * `getComputedStyle` y volver a dibujar a mano en cada cambio.
 *
 * La paleta es la heredada del dashboard viejo y **está validada**, no supuesta:
 * las dos series pasan las seis comprobaciones —banda de luminosidad, croma,
 * separación bajo daltonismo, suelo de visión normal y contraste— en claro y en
 * oscuro. El par azul/rojo separa con ΔE 21,6 en protanopía, muy por encima del
 * mínimo de 8.
 */

export const COLORES = {
  serie1: "var(--color-series-1)",
  serie2: "var(--color-series-2)",
  positivo: "var(--color-positive)",
  negativo: "var(--color-negative)",
  neutro: "var(--color-text-muted)",
  rejilla: "var(--color-grid)",
  eje: "var(--color-axis)",
  /** Las etiquetas que Recharts pinta como SVG y no como texto de la página. */
  tenue: "var(--color-text-muted)",
  /** El resalte de la barra o el punto sobre el que está el ratón. */
  cursor: "var(--color-surface-sunken)",
} as const;

/** Ejes discretos: la rejilla es referencia, no protagonista. */
export const EJE = {
  stroke: "var(--color-axis)",
  fontSize: 11,
  tickLine: false,
  axisLine: false,
} as const;

/**
 * Marco de una gráfica, con **vista de tabla**.
 *
 * La tabla no es un extra: es lo que hace que el dato siga estando disponible
 * cuando el color no basta —daltonismo, impresión, lector de pantalla— y además
 * es como se comprueba una cifra concreta, que en una gráfica se estima y en una
 * tabla se lee. El dashboard viejo ya lo tenía y habría sido una pérdida.
 *
 * @param props - Chart frame props.
 * @param props.titulo - Chart heading.
 * @param props.explicacion - What the chart answers, when the title cannot say it.
 * @param props.vacia - Empty-state wording. When given, nothing is drawn.
 * @param props.tabla - The same data as a table, for the alternative view.
 * @param props.children - The chart itself.
 * @return The rendered card, showing the chart or the table.
 */
export function Grafica({
  titulo,
  explicacion,
  vacia,
  tabla,
  children,
}: {
  titulo: string;
  explicacion?: ReactNode;
  /** Texto del estado vacío. Si viene, no se dibuja nada. */
  vacia?: string;
  tabla: ReactNode;
  children: ReactNode;
}) {
  const [verTabla, setVerTabla] = useState(false);

  return (
    <Tarjeta etiqueta="section">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <TituloBloque>{titulo}</TituloBloque>
        {!vacia && (
          <BotonEnlace
            onClick={() => setVerTabla((v) => !v)}
            aria-pressed={verTabla}
            className="text-xs"
          >
            {verTabla ? "Ver gráfica" : "Ver tabla"}
          </BotonEnlace>
        )}
      </div>
      {explicacion && (
        <p className="mb-3 text-xs leading-snug text-text-muted">{explicacion}</p>
      )}

      {vacia ? (
        <p className="py-6 text-[13px] text-text-muted">{vacia}</p>
      ) : verTabla ? (
        <div className="overflow-x-auto">{tabla}</div>
      ) : (
        <div className="h-56 w-full">{children}</div>
      )}
    </Tarjeta>
  );
}

/**
 * The charts' tooltip.
 *
 * Tooltip propio: el de Recharts trae sus colores y no conoce la paleta.
 *
 * @param props - Tooltip props, passed by Recharts.
 * @param props.active - Whether the pointer is over a point.
 * @param props.payload - The series values at that point.
 * @param props.label - The x-axis value.
 * @param props.formato - Formats a numeric value, given its series name. Without
 *     it the value is printed as it comes.
 * @return The rendered tooltip, or null when there is nothing under the pointer.
 */
export function Globo({
  active,
  payload,
  label,
  formato,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number | string; color?: string }[];
  label?: string | number;
  formato?: (valor: number, nombre: string) => string;
}) {
  if (!active || !payload?.length) return null;

  return (
    <Tarjeta relleno="px-2.5 py-1.5" className="rounded-md text-xs">
      {label !== undefined && (
        <p className="mb-0.5 font-medium text-foreground">{label}</p>
      )}
      {payload.map((entrada, indice) => (
        <p key={indice} className="tabular flex items-center gap-1.5 text-text-secondary">
          <span
            aria-hidden
            className="inline-block size-2 rounded-full"
            style={{ background: entrada.color }}
          />
          {entrada.name && <span>{entrada.name}:</span>}
          <span className="font-medium text-foreground">
            {typeof entrada.value === "number" && formato
              ? formato(entrada.value, entrada.name ?? "")
              : entrada.value}
          </span>
        </p>
      ))}
    </Tarjeta>
  );
}

/**
 * Minimal table for each chart's alternative view.
 *
 * @param props - Table props.
 * @param props.columnas - Header texts. The first column is left-aligned and
 *     the rest right-aligned, since they carry the figures.
 * @param props.filas - Rows, already formatted.
 * @return The rendered table.
 */
export function TablaSimple({
  columnas,
  filas,
}: {
  columnas: string[];
  filas: (string | number)[][];
}) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-left text-text-muted">
          {columnas.map((c, i) => (
            <th key={c} scope="col" className={i === 0 ? "py-1 pr-3" : "py-1 pr-3 text-right"}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {filas.map((fila, i) => (
          <tr key={i} className="border-b border-border last:border-0">
            {fila.map((celda, j) => (
              <td
                key={j}
                className={j === 0 ? "py-1 pr-3" : "tabular py-1 pr-3 text-right"}
              >
                {celda}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
