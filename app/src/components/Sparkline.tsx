/** The narrowest range the vertical scale may show, as a share of the level. */
const MIN_SPAN_RATIO = 0.04;

/**
 * A bare line of values, for the equity curve on Resumen.
 *
 * **Hand-drawn SVG and not Recharts**, because Recharts is what the analytics
 * screen is split out of the bundle for (App.tsx): pulling it into Resumen, the
 * screen every visit starts on, would load 380 KB of charting to draw one line.
 * This one has no axes, no tooltip and no interaction on purpose — it answers
 * "which way is it going", and the figures beside it and Analítica answer the
 * rest.
 *
 * The line is painted `series-1` and the reference is the budget as a dashed
 * `axis` line, so above or below the start is read against something and not
 * against the edge of the box. Colour does not carry polarity here: the signed
 * return is written right beside it.
 *
 * The drawing keeps a fixed 4:1 ratio and scales to the width it is given,
 * with the strokes held at 1.5 px by `vector-effect`: it fills the card without
 * a resize observer. Stretching it to an arbitrary box instead
 * (`preserveAspectRatio="none"`) was the first version, and it turned the dot
 * at the end into an ellipse.
 *
 * @param props - Sparkline props.
 * @param props.values - The series, oldest first.
 * @param props.baseline - The reference line, or null for none.
 * @param props.label - What the line is, for a screen reader: the SVG is an
 *     image and its figures are elsewhere on the page.
 * @param props.className - Classes for the SVG; its height follows its width.
 * @return The rendered SVG, or null when there is nothing to draw.
 */
export function Sparkline({
  values,
  baseline,
  label,
  className,
}: {
  values: number[];
  baseline?: number | null;
  label: string;
  className?: string;
}) {
  if (values.length === 0) return null;

  const all = baseline === null || baseline === undefined ? values : [...values, baseline];
  // **The scale never spans less than 4 % of the level**, centred on the data.
  // Fitted to the data alone, a first day at -0.08 % filled the whole height —
  // the budget at the top edge and the one mark at the bottom— and read as a
  // crash. Four per cent is roughly a quiet week of a stock portfolio: moves
  // smaller than that look small, and larger ones still get the full height.
  // It also covers the flat series, which would otherwise divide by zero.
  const low = Math.min(...all);
  const high = Math.max(...all);
  const minSpan = Math.abs((low + high) / 2) * MIN_SPAN_RATIO || 1;
  const middle = (low + high) / 2;
  const min = high - low >= minSpan ? low : middle - minSpan / 2;
  const max = high - low >= minSpan ? high : middle + minSpan / 2;
  const span = max - min;
  const width = 320;
  const height = 80;
  const pad = 6;

  const y = (value: number) =>
    pad + (1 - (value - min) / span) * (height - 2 * pad);
  const x = (index: number) =>
    values.length === 1 ? width / 2 : pad + (index / (values.length - 1)) * (width - 2 * pad);

  const points = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const last = values[values.length - 1]!;

  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
    >
      {baseline !== null && baseline !== undefined && (
        <line
          x1={0}
          x2={width}
          y1={y(baseline)}
          y2={y(baseline)}
          stroke="var(--color-axis)"
          strokeWidth={1}
          strokeDasharray="3 3"
          vectorEffect="non-scaling-stroke"
        />
      )}
      {values.length > 1 && (
        <polyline
          points={points}
          fill="none"
          stroke="var(--color-series-1)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      )}
      {/* The last mark as a dot: with one point it is the whole drawing, and
          with many it says where "now" is without an axis. */}
      <circle cx={x(values.length - 1)} cy={y(last)} r={3.5} fill="var(--color-series-1)" />
    </svg>
  );
}
