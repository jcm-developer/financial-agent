import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { AXIS, COLORS, Chart, ChartTooltip, SimpleTable } from "@/components/charts/base";
import { percent } from "@/lib/format";

/**
 * Two or more experiments' equity, on the same axis (F5.6).
 *
 * ⚠️ **The series is the return in %, never the money, and that is not a
 * presentation choice.** The project converts currency nowhere (D8), so drawing
 * a European experiment's equity and an American one's on the same axis would
 * put euros and dollars on one scale — which is exactly the mistake FE.8 exists
 * to prevent, made bigger by being drawn. And even within one currency, two
 * experiments with different budgets are only comparable once indexed: 200 € on
 * 10.000 and 200 € on 1.000 are not the same result.
 *
 * ⚠️ **At most two curves share an axis, and that came out of measuring.** The
 * validated palette has two categorical hues; green is reserved for `delta-good`
 * and amber for `warning`, so a third would have to come out of what is left, and
 * what is left collides with the blue already in use: purple against
 * `--series-1` gives ΔE 3.7–5.8 under deuteranopia, against a target of 8 and a
 * hard normal-vision floor of 15. Cycling hues or inventing a third anyway is
 * how a chart ends up saying two experiments are different when the reader
 * cannot tell them apart. So beyond two, the comparison becomes **small
 * multiples**: one chart per experiment, one series each, sharing a y domain so
 * they are still read against each other.
 */
interface Series {
  name: string;
  /** Return in %, one point per equity snapshot, already indexed. */
  points: { as_of: string; return_pct: number }[];
}

interface Props {
  series: Series[];
}

/** The two hues, in fixed order. Never cycled: entity 1 is always series-1. */
const HUES = [COLORS.series1, COLORS.series2] as const;

/**
 * The comparison chart: overlaid for two, small multiples beyond that.
 *
 * @param props - Chart props.
 * @param props.series - One entry per experiment, its points already indexed.
 * @return The rendered chart or grid of charts.
 */
export function Comparison({ series }: Props) {
  if (series.length === 0) {
    return (
      <Chart title="Rentabilidad comparada" table={null} empty="Elige al menos un experimento.">
        {null}
      </Chart>
    );
  }

  if (series.length <= 2) return <Overlaid series={series} />;
  return <SmallMultiples series={series} />;
}

/**
 * Builds the rows the table view shows, shared by both layouts.
 *
 * @param series - The experiments being compared.
 * @return Header texts and rows, already formatted.
 */
function tableOf(series: Series[]): { columns: string[]; rows: (string | number)[][] } {
  const dates = [...new Set(series.flatMap((s) => s.points.map((p) => p.as_of)))].sort();
  return {
    columns: ["Fecha", ...series.map((s) => s.name)],
    rows: dates.map((date) => [
      date.slice(0, 10),
      ...series.map((s) => {
        const point = s.points.find((p) => p.as_of === date);
        return point ? percent(point.return_pct, { sign: true }) : "—";
      }),
    ]),
  };
}

/**
 * Up to two experiments on one axis.
 *
 * @param props - Chart props.
 * @param props.series - One or two experiments.
 * @return The rendered chart.
 */
function Overlaid({ series }: Props) {
  // One row per date, one key per experiment: Recharts wants the series side by
  // side, and the snapshots of two experiments do not have to land on the same
  // instants.
  const dates = [...new Set(series.flatMap((s) => s.points.map((p) => p.as_of)))].sort();
  const data = dates.map((date) => {
    const row: Record<string, string | number | null> = { as_of: date.slice(5, 10) };
    for (const s of series) {
      row[s.name] = s.points.find((p) => p.as_of === date)?.return_pct ?? null;
    }
    return row;
  });

  return (
    <Chart
      title="Rentabilidad comparada"
      explanation={
        <>
          En % sobre el presupuesto asignado, no en dinero: el proyecto no convierte divisa y
          dos presupuestos distintos no se comparan en euros.{" "}
          {series.length === 2 && "Cada experimento conserva su color al filtrar."}
        </>
      }
      table={<SimpleTable {...tableOf(series)} />}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
          <CartesianGrid stroke={COLORS.grid} strokeDasharray="2 4" vertical={false} />
          <XAxis dataKey="as_of" {...AXIS} />
          <YAxis {...AXIS} tickFormatter={(v: number) => `${v}%`} />
          <Tooltip
            cursor={{ stroke: COLORS.axis }}
            content={<ChartTooltip format={(v) => percent(v, { sign: true })} />}
          />
          {series.map((s, index) => (
            <Line
              key={s.name}
              type="monotone"
              dataKey={s.name}
              name={s.name}
              // Fixed order, never cycled: with two slots and at most two
              // series, the index is the slot.
              stroke={HUES[index]}
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Chart>
  );
}

/**
 * Three or more experiments, one chart each.
 *
 * They share the y domain so the curves are still read against each other: with
 * each chart auto-scaling, a 0.4 % wobble and a 12 % run would draw the same
 * shape, which is the one thing a comparison must not do.
 *
 * @param props - Chart props.
 * @param props.series - Three or more experiments.
 * @return The rendered grid of charts.
 */
function SmallMultiples({ series }: Props) {
  const all = series.flatMap((s) => s.points.map((p) => p.return_pct));
  const low = Math.min(0, ...all);
  const high = Math.max(0, ...all);
  const pad = Math.max(1, (high - low) * 0.1);
  const domain: [number, number] = [low - pad, high + pad];

  return (
    <Chart
      title={`Rentabilidad comparada · ${series.length} experimentos`}
      height="h-96"
      explanation={
        <>
          En % sobre el presupuesto asignado. Con más de dos experimentos se dibuja uno por
          gráfica y no todos encima: la paleta validada tiene dos tonos categóricos —el verde
          está reservado a las variaciones y el ámbar a los avisos— y un tercero se confunde
          con el azul en daltonismo (ΔE 3,7 en deuteranopía, contra un mínimo de 8). Todas
          comparten escala vertical.
        </>
      }
      table={<SimpleTable {...tableOf(series)} />}
    >
      <div className="grid h-full grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {series.map((s) => (
          <div key={s.name} className="flex min-h-0 flex-col">
            <p className="truncate text-caption font-medium" title={s.name}>
              {s.name}
            </p>
            <div className="min-h-0 flex-1">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={s.points.map((p) => ({ ...p, as_of: p.as_of.slice(5, 10) }))}
                  margin={{ top: 4, right: 4, bottom: 0, left: -20 }}
                >
                  <CartesianGrid stroke={COLORS.grid} strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="as_of" {...AXIS} hide />
                  <YAxis {...AXIS} domain={domain} tickFormatter={(v: number) => `${Math.round(v)}%`} />
                  <Tooltip
                    cursor={{ stroke: COLORS.axis }}
                    content={<ChartTooltip format={(v) => percent(v, { sign: true })} />}
                  />
                  <Line
                    type="monotone"
                    dataKey="return_pct"
                    name={s.name}
                    stroke={COLORS.series1}
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ))}
      </div>
    </Chart>
  );
}
