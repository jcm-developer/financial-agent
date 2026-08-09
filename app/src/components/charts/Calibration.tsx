import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { CalibrationBucket, ConvictionBucket } from "@/api/types";
import { COLORS, AXIS, Chart, ChartTooltip, SimpleTable } from "@/components/charts/base";
import { money, integer, percent } from "@/lib/format";

/** Below this, a hit rate means nothing. */
const MIN_SAMPLE = 5;

/**
 * **The chart that decides the experiment.**
 *
 * Real hit rate grouped by the conviction the model declared on entry. If the
 * bars do not rise from left to right, conviction informs nothing and we are
 * trading on expensive noise — which is exactly the question this project exists
 * to answer.
 *
 * **Each bar carries its trade count on top**, and the ones that do not reach
 * five are dimmed. Without that the chart lies at its most dangerous moment: a
 * bucket with a single winning trade draws a 100 % bar identical to one from a
 * bucket with thirty, and it is right at the start —when there are few— that the
 * urge to draw conclusions is strongest.
 *
 * @param props - Chart props.
 * @param props.buckets - Hit rate per declared-conviction bucket.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered chart, with the buckets below the minimum sample dimmed.
 */
export function Calibration({
  buckets,
  symbol,
}: {
  buckets: CalibrationBucket[];
  symbol: string;
}) {
  const data = buckets.map((b) => ({
    ...b,
    label: `${b.conviction_bucket}–${b.conviction_bucket + 9}`,
    reliable: b.trades >= MIN_SAMPLE,
  }));
  const few = data.some((d) => !d.reliable);

  return (
    <Chart
      title="Calibración de la convicción"
      explanation={
        <>
          Acierto real según la convicción declarada al entrar. Si no sube de izquierda a
          derecha, la convicción del modelo no informa de nada.
          {few && " Los tramos atenuados tienen menos de cinco operaciones: no concluyas de ellos."}
        </>
      }
      empty={
        data.length === 0
          ? "Hace falta al menos una operación cerrada que venga de una decisión de entrada. Con un ciclo al día, esto tarda semanas en decir algo."
          : undefined
      }
      table={
        <SimpleTable
          columns={["Convicción", "Operaciones", "Aciertos", "P&L medio"]}
          rows={data.map((d) => [
            d.label,
            d.trades,
            percent(d.win_rate_pct),
            money(d.avg_pnl, symbol),
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 18, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.grid} vertical={false} />
          <XAxis dataKey="label" {...AXIS} />
          <YAxis {...AXIS} width={45} domain={[0, 100]} unit="%" />
          {/* 50 % is the reference that gives the height its meaning: below it,
              the bucket is right less often than a coin. */}
          <ReferenceLine
            y={50}
            stroke={COLORS.neutral}
            strokeDasharray="4 4"
            label={{ value: "azar", position: "insideTopRight", fontSize: 10, fill: COLORS.faint }}
          />
          <Tooltip
            content={<ChartTooltip format={(v) => percent(v)} />}
            cursor={{ fill: COLORS.cursor }}
          />
          <Bar dataKey="win_rate_pct" name="Aciertos" radius={[4, 4, 0, 0]}>
            {data.map((d) => (
              <Cell
                key={d.conviction_bucket}
                fill={COLORS.series1}
                fillOpacity={d.reliable ? 1 : 0.35}
              />
            ))}
            <LabelList
              dataKey="trades"
              position="top"
              fontSize={10}
              fill={COLORS.faint}
              formatter={(value) => (value === undefined ? "" : `n=${value}`)}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Chart>
  );
}

/**
 * Spread of the declared conviction, by proposed action.
 *
 * If it concentrates in a single bucket, the model is not discriminating between
 * opportunities: it declares the same for everything and its conviction is not a
 * signal, it is a habit.
 *
 * The colours are a **diverging** scale, not a categorical one: buy and sell are
 * the two poles and hold is the neutral point, so grey is what it gets.
 * Validated as diverging and not as a categorical palette — a categorical one
 * would demand chroma in all three, and here the grey in the middle is correct.
 *
 * @param props - Chart props.
 * @param props.buckets - Proposal counts per conviction bucket and action.
 * @return The rendered chart.
 */
export function ConvictionHistogram({ buckets }: { buckets: ConvictionBucket[] }) {
  const data = buckets.map((b) => ({
    ...b,
    label: `${b.bucket}–${b.bucket + 9}`,
  }));

  return (
    <Chart
      title="Convicción declarada"
      explanation="Cuántas decisiones cayeron en cada tramo. Si se concentra en uno solo, el modelo no está discriminando entre oportunidades."
      empty={data.length === 0 ? "El analista no ha registrado decisiones todavía." : undefined}
      table={
        <SimpleTable
          columns={["Convicción", "Compras", "Mantener", "Ventas", "Total"]}
          rows={data.map((d) => [
            d.label,
            d.buys ?? 0,
            d.holds ?? 0,
            d.sells ?? 0,
            d.total ?? 0,
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORS.grid} vertical={false} />
          <XAxis dataKey="label" {...AXIS} />
          <YAxis {...AXIS} width={40} allowDecimals={false} />
          <Tooltip
            content={<ChartTooltip format={(v) => integer(v)} />}
            cursor={{ fill: COLORS.cursor }}
          />
          {/* Stacked with a 2 px gap between segments: without the separation,
              two adjacent segments of the same height read as one. */}
          <Bar dataKey="buys" name="Compras" stackId="a" fill={COLORS.positive} />
          <Bar dataKey="holds" name="Mantener" stackId="a" fill={COLORS.neutral} />
          <Bar
            dataKey="sells"
            name="Ventas"
            stackId="a"
            fill={COLORS.negative}
            radius={[4, 4, 0, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </Chart>
  );
}
