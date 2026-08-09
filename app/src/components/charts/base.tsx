import { useState, type ReactNode } from "react";

import { LinkButton, Card, BlockTitle } from "@/components/pieces";

/**
 * What every chart shares.
 *
 * **Colours are passed as `var(--series-1)` and not as hexadecimal.** SVG
 * presentation attributes accept CSS variables, so the theme switch repaints the
 * charts on its own: without that, the colours would have to be read with
 * `getComputedStyle` and redrawn by hand on every change.
 *
 * The palette is the one inherited from the old dashboard and **it is
 * validated**, not assumed: both series pass the six checks —lightness band,
 * chroma, separation under colour blindness, normal-vision floor and contrast—
 * in light and in dark. The blue/red pair separates with ΔE 21.6 under
 * protanopia, well above the minimum of 8.
 */

export const COLORS = {
  series1: "var(--color-series-1)",
  series2: "var(--color-series-2)",
  positive: "var(--color-positive)",
  negative: "var(--color-negative)",
  neutral: "var(--color-text-muted)",
  grid: "var(--color-grid)",
  axis: "var(--color-axis)",
  /** The labels Recharts paints as SVG and not as page text. */
  faint: "var(--color-text-muted)",
  /** The highlight of the bar or point under the pointer. */
  cursor: "var(--color-surface-sunken)",
} as const;

/** Discreet axes: the grid is a reference, not the protagonist. */
export const AXIS = {
  stroke: "var(--color-axis)",
  fontSize: 11,
  tickLine: false,
  axisLine: false,
} as const;

/**
 * A chart's frame, with a **table view**.
 *
 * The table is not an extra: it is what keeps the data available when colour is
 * not enough —colour blindness, printing, a screen reader— and it is also how a
 * specific figure gets checked, since in a chart it is estimated and in a table
 * it is read. The old dashboard had it and losing it would have been a loss.
 *
 * @param props - Chart frame props.
 * @param props.title - Chart heading.
 * @param props.explanation - What the chart answers, when the title cannot say it.
 * @param props.empty - Empty-state wording. When given, nothing is drawn.
 * @param props.table - The same data as a table, for the alternative view.
 * @param props.children - The chart itself.
 * @return The rendered card, showing the chart or the table.
 */
export function Chart({
  title,
  explanation,
  empty,
  table,
  children,
}: {
  title: string;
  explanation?: ReactNode;
  /** Empty-state text. When present, nothing is drawn. */
  empty?: string;
  table: ReactNode;
  children: ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);

  return (
    <Card as="section">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <BlockTitle>{title}</BlockTitle>
        {!empty && (
          <LinkButton
            onClick={() => setShowTable((v) => !v)}
            aria-pressed={showTable}
            className="text-xs"
          >
            {showTable ? "Ver gráfica" : "Ver tabla"}
          </LinkButton>
        )}
      </div>
      {explanation && (
        <p className="mb-3 text-xs leading-snug text-text-muted">{explanation}</p>
      )}

      {empty ? (
        <p className="py-6 text-[13px] text-text-muted">{empty}</p>
      ) : showTable ? (
        <div className="overflow-x-auto">{table}</div>
      ) : (
        <div className="h-56 w-full">{children}</div>
      )}
    </Card>
  );
}

/**
 * The charts' tooltip.
 *
 * A tooltip of our own: the Recharts one brings its own colours and does not
 * know the palette. It is `ChartTooltip` and not `Tooltip` because every chart
 * imports Recharts' `Tooltip` in the same breath to mount this one into it.
 *
 * @param props - Tooltip props, passed by Recharts.
 * @param props.active - Whether the pointer is over a point.
 * @param props.payload - The series values at that point.
 * @param props.label - The x-axis value.
 * @param props.format - Formats a numeric value, given its series name. Without
 *     it the value is printed as it comes.
 * @return The rendered tooltip, or null when there is nothing under the pointer.
 */
export function ChartTooltip({
  active,
  payload,
  label,
  format,
}: {
  active?: boolean;
  payload?: { name?: string; value?: number | string; color?: string }[];
  label?: string | number;
  format?: (value: number, name: string) => string;
}) {
  if (!active || !payload?.length) return null;

  return (
    <Card padding="px-2.5 py-1.5" className="rounded-md text-xs">
      {label !== undefined && (
        <p className="mb-0.5 font-medium text-foreground">{label}</p>
      )}
      {payload.map((entry, index) => (
        <p key={index} className="tabular flex items-center gap-1.5 text-text-secondary">
          <span
            aria-hidden
            className="inline-block size-2 rounded-full"
            style={{ background: entry.color }}
          />
          {entry.name && <span>{entry.name}:</span>}
          <span className="font-medium text-foreground">
            {typeof entry.value === "number" && format
              ? format(entry.value, entry.name ?? "")
              : entry.value}
          </span>
        </p>
      ))}
    </Card>
  );
}

/**
 * Minimal table for each chart's alternative view.
 *
 * @param props - Table props.
 * @param props.columns - Header texts. The first column is left-aligned and the
 *     rest right-aligned, since they carry the figures.
 * @param props.rows - Rows, already formatted.
 * @return The rendered table.
 */
export function SimpleTable({
  columns,
  rows,
}: {
  columns: string[];
  rows: (string | number)[][];
}) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-border text-left text-text-muted">
          {columns.map((c, i) => (
            <th key={c} scope="col" className={i === 0 ? "py-1 pr-3" : "py-1 pr-3 text-right"}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} className="border-b border-border last:border-0">
            {row.map((cell, j) => (
              <td
                key={j}
                className={j === 0 ? "py-1 pr-3" : "tabular py-1 pr-3 text-right"}
              >
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
