import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { RejectionCount, SymbolPerformance } from "@/api/types";
import { COLORS, AXIS, Chart, ChartTooltip, SimpleTable } from "@/components/charts/base";
import { money, integer, dateTime, percent } from "@/lib/format";
import { ruleLabel } from "@/lib/labels";

/**
 * Realised P&L per asset.
 *
 * Colour encodes **polarity**, not identity: blue for what gained, red for what
 * lost. Hence there is no legend —there are no series to tell apart— and there
 * is a line at zero, which is where the meaning sits.
 *
 * @param props - Chart props.
 * @param props.rows - Per-symbol performance. Rows with no realised P&L are
 *     dropped rather than drawn as zero, which would claim they broke even.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered chart.
 */
export function PnlBySymbol({
  rows,
  symbol,
}: {
  rows: SymbolPerformance[];
  symbol: string;
}) {
  const data = rows.filter((r) => r.total_pnl !== null && r.total_pnl !== undefined);

  return (
    <Chart
      title="P&L realizado por activo"
      empty={
        data.length === 0
          ? "Aún no hay operaciones cerradas."
          : undefined
      }
      table={
        <SimpleTable
          columns={["Activo", "Operaciones", "Aciertos", "P&L total", "Días medios"]}
          rows={data.map((r) => [
            r.symbol,
            r.trades,
            percent(r.win_rate_pct),
            money(r.total_pnl, symbol),
            r.avg_holding_days ?? "—",
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.grid} vertical={false} />
          <XAxis dataKey="symbol" {...AXIS} />
          <YAxis {...AXIS} width={70} />
          <ReferenceLine y={0} stroke={COLORS.axis} />
          <Tooltip
            content={<ChartTooltip format={(v) => money(v, symbol)} />}
            cursor={{ fill: COLORS.cursor }}
          />
          <Bar dataKey="total_pnl" name="P&L" radius={[4, 4, 0, 0]}>
            {data.map((r) => (
              <Cell
                key={r.symbol}
                fill={(r.total_pnl ?? 0) >= 0 ? COLORS.positive : COLORS.negative}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Chart>
  );
}

/**
 * Which limit the model keeps hitting.
 *
 * Horizontal bars because the labels are rule names («tamaño máximo por posición»,
 * «convicción mínima») and vertically they would overlap or have to be rotated,
 * which is worse. A single magnitude, so a single tone.
 *
 * @param props - Chart props.
 * @param props.rows - Rejection counts per rule, sorted here so the caller does
 *     not have to.
 * @return The rendered chart.
 */
export function RejectionsByRule({ rows }: { rows: RejectionCount[] }) {
  const data = [...rows]
    .sort((a, b) => b.rejections - a.rejections)
    .map((r) => ({ ...r, label: ruleLabel(r.rule) }));

  return (
    <Chart
      title="Rechazos por regla"
      empty={
        data.length === 0
          ? "No hay ninguna propuesta rechazada."
          : undefined
      }
      table={
        <SimpleTable
          columns={["Regla", "Rechazos", "Último"]}
          rows={data.map((r) => [r.label, r.rejections, dateTime(r.last_seen)])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 8, right: 16, bottom: 0, left: 8 }}
        >
          <CartesianGrid stroke={COLORS.grid} horizontal={false} />
          <XAxis type="number" {...AXIS} allowDecimals={false} />
          <YAxis type="category" dataKey="label" {...AXIS} width={170} />
          <Tooltip
            content={<ChartTooltip format={(v) => integer(v)} />}
            cursor={{ fill: COLORS.cursor }}
          />
          <Bar
            dataKey="rejections"
            name="Rechazos"
            fill={COLORS.series2}
            radius={[0, 4, 4, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </Chart>
  );
}
