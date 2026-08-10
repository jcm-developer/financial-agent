import { useState, type ReactNode } from "react";

import { LinkButton, Card, BlockTitle } from "@/components/pieces";
import { cn } from "@/lib/utils";

/**
 * What every chart shares.
 *
 * **Colours are passed as `var(--color-…)` and never as hexadecimal.** SVG
 * presentation attributes accept CSS variables, so a chart is repainted by
 * editing `index.css` and nothing else: without that, the colours would have to
 * be read with `getComputedStyle` and redrawn by hand.
 *
 * The palette is Verdana's. Two things follow from it that are worth knowing
 * before adding a chart:
 *
 * - **Polarity is the success/error pair** (#22C55E / #EF4444) — the mark tier,
 *   which is what a fill needs. ⚠️ It is a green/red pair, so under protanopia
 *   and deuteranopia the two poles are close to each other: the zero line, the
 *   sign already written into every figure and the table view underneath are
 *   what carry the reading when the hue does not.
 * - **Identity is series-1 then series-2** (navy, then the info sky), assigned
 *   in that fixed order and never cycled. Sage is missing from that list on
 *   purpose: Verdana reserves it for interactive elements and positive states,
 *   so a chart series painted sage would read as a link.
 *
 * Colour codes polarity **or** identity in a given chart, never both.
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
  height = "h-56",
  table,
  children,
}: {
  title: string;
  explanation?: ReactNode;
  /** Empty-state text. When present, nothing is drawn. */
  empty?: string;
  /**
   * Height utility of the plotting area.
   *
   * A prop and not something overridden from `className`, for the same reason
   * `Card`'s padding is: `h-56` and `h-96` are the same utility group, so which
   * one wins would be decided by the stylesheet's order. The only caller that
   * changes it is the comparison's small multiples, which stack several charts
   * inside one frame.
   */
  height?: string;
  table: ReactNode;
  children: ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);

  return (
    <Card as="section">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-3">
        <BlockTitle>{title}</BlockTitle>
        {!empty && (
          <LinkButton
            onClick={() => setShowTable((v) => !v)}
            aria-pressed={showTable}
            className="text-caption"
          >
            {showTable ? "Ver gráfica" : "Ver tabla"}
          </LinkButton>
        )}
      </div>
      {explanation && (
        <p className="mb-4 text-caption font-normal text-text-secondary">{explanation}</p>
      )}

      {empty ? (
        <p className="py-8 text-body-sm text-text-secondary">{empty}</p>
      ) : showTable ? (
        <div className="overflow-x-auto">{table}</div>
      ) : (
        <div className={cn(height, "w-full")}>{children}</div>
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
    <Card elevated padding="px-3 py-2" className="text-caption">
      {label !== undefined && (
        <p className="mb-1 font-medium text-foreground">{label}</p>
      )}
      {payload.map((entry, index) => (
        <p key={index} className="tabular flex items-center gap-2 text-text-secondary">
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
    <table className="w-full text-caption">
      <thead>
        <tr className="border-b border-border text-left tracking-[0.5px] text-text-secondary uppercase">
          {columns.map((c, i) => (
            <th key={c} scope="col" className={i === 0 ? "py-2 pr-4" : "py-2 pr-4 text-right"}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} className="border-b border-surface-sunken last:border-0">
            {row.map((cell, j) => (
              <td
                key={j}
                className={
                  j === 0 ? "py-2 pr-4 font-normal" : "tabular py-2 pr-4 text-right font-normal"
                }
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
