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
      explanation="Solo operaciones cerradas. Las abiertas no cuentan hasta que se cierran."
      empty={
        data.length === 0
          ? "Ninguna posición cerrada todavía, así que no hay nada realizado que repartir."
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
 * Horizontal bars because the labels are rule names (`max_position_pct`,
 * `min_conviction`) and vertically they would overlap or have to be rotated,
 * which is worse. A single magnitude, so a single tone.
 *
 * @param props - Chart props.
 * @param props.rows - Rejection counts per rule, sorted here so the caller does
 *     not have to.
 * @return The rendered chart.
 */
export function RejectionsByRule({ rows }: { rows: RejectionCount[] }) {
  const data = [...rows].sort((a, b) => b.rejections - a.rejections);

  return (
    <Chart
      title="Rechazos del Risk Manager"
      explanation="Si casi todos son de la misma regla, o el modelo insiste en algo que no cabe o ese límite está mal puesto."
      empty={
        data.length === 0
          ? "El Risk Manager no ha rechazado nada. Con pocas propuestas es lo esperable."
          : undefined
      }
      table={
        <SimpleTable
          columns={["Regla", "Rechazos", "Último"]}
          rows={data.map((r) => [r.rule, r.rejections, dateTime(r.last_seen)])}
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
          <YAxis type="category" dataKey="rule" {...AXIS} width={140} />
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
