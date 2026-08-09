import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { EquityPoint } from "@/api/types";
import { COLORS, AXIS, Chart, ChartTooltip, SimpleTable } from "@/components/charts/base";
import { money, dateTime, percent } from "@/lib/format";

/**
 * Equity curve and drawdown, as **two charts and not one with two axes**.
 *
 * A dual axis —equity in euros on the left, drawdown in % on the right— lets the
 * chosen scale decide which of the two lines appears to dominate, and two people
 * read different things from the same drawing. Separated and sharing the time
 * axis below, the comparison is made by looking downwards, which is honest.
 */

interface Props {
  points: EquityPoint[];
  symbol: string;
  budget: number | null | undefined;
}

/**
 * The experiment's equity over time.
 *
 * @param props - Chart props.
 * @param props.points - Equity points, in chronological order.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @param props.budget - Assigned budget, which is the reference line the
 *     experiment is measured against. Null falls back to no reference.
 * @return The rendered chart.
 */
export function EquityCurve({ points, symbol, budget }: Props) {
  const data = points.map((p) => ({ ...p, label: dateTime(p.as_of) }));

  return (
    <Chart
      title="Curva de capital"
      explanation={
        budget
          ? `La referencia es el presupuesto asignado (${money(budget, symbol)}), no el primer punto: es contra lo que se mide el experimento.`
          : undefined
      }
      empty={
        data.length === 0
          ? "Todavía no hay curva: se dibuja un punto por ciclo ejecutado."
          : undefined
      }
      table={
        <SimpleTable
          columns={["Momento", "Capital", "Efectivo", "Posiciones", "Caída"]}
          rows={data.map((p) => [
            p.label,
            money(p.equity, symbol),
            money(p.cash, symbol),
            p.open_positions ?? 0,
            percent(p.drawdown_pct),
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.grid} vertical={false} />
          <XAxis dataKey="label" {...AXIS} minTickGap={40} />
          <YAxis {...AXIS} width={70} domain={["auto", "auto"]} />
          {budget ? (
            <ReferenceLine
              y={budget}
              stroke={COLORS.neutral}
              strokeDasharray="4 4"
              label={{ value: "inicial", position: "insideTopRight", fontSize: 10, fill: COLORS.faint }}
            />
          ) : null}
          <Tooltip
            content={<ChartTooltip format={(v) => money(v, symbol)} />}
            cursor={{ stroke: COLORS.axis }}
          />
          {/* 2 px and no dot per datum: with one point per cycle and ten
              sessions, marking them all turns the line into a necklace. */}
          <Line
            type="monotone"
            dataKey="equity"
            name="Capital"
            stroke={COLORS.series1}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </Chart>
  );
}

/**
 * The drop from the running peak, drawn below the equity curve and sharing its
 * time axis.
 *
 * @param props - Chart props.
 * @param props.points - Equity points, in chronological order.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered chart.
 */
export function Drawdown({ points, symbol }: Omit<Props, "budget">) {
  const data = points.map((p) => ({ ...p, label: dateTime(p.as_of) }));
  const worst = data.reduce((min, p) => Math.min(min, p.drawdown_pct ?? 0), 0);

  return (
    <Chart
      title="Caída desde máximos"
      explanation={
        data.length
          ? `Cuánto habría dolido en el peor momento. La peor hasta ahora: ${percent(worst)}.`
          : undefined
      }
      empty={data.length === 0 ? "Sin ciclos todavía." : undefined}
      table={
        <SimpleTable
          columns={["Momento", "Capital", "Caída"]}
          rows={data.map((p) => [
            p.label,
            money(p.equity, symbol),
            percent(p.drawdown_pct),
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.grid} vertical={false} />
          <XAxis dataKey="label" {...AXIS} minTickGap={40} />
          {/* The ceiling is pinned at 0: drawdown is never positive, and letting
              the axis fit itself would make "no drawdown" look like a valley. */}
          <YAxis {...AXIS} width={50} domain={["auto", 0]} unit="%" />
          <Tooltip
            content={<ChartTooltip format={(v) => percent(v)} />}
            cursor={{ stroke: COLORS.axis }}
          />
          <Area
            type="monotone"
            dataKey="drawdown_pct"
            name="Caída"
            stroke={COLORS.negative}
            strokeWidth={2}
            fill={COLORS.negative}
            fillOpacity={0.15}
          />
        </AreaChart>
      </ResponsiveContainer>
    </Chart>
  );
}
