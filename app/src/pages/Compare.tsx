import { lazy, Suspense, useState } from "react";
import { useQueries } from "@tanstack/react-query";

import { api } from "@/api/client";
import { useProfiles } from "@/api/hooks";
import { keys } from "@/api/keys";
import type { Analytics, ProfileSummary } from "@/api/types";
import { Alert, Card, Loading, PageTitle, SectionTitle } from "@/components/pieces";
import { ProfileStatus } from "@/components/ProfileStatus";
import { Section } from "@/components/Section";
import { TableHead, Row, Table, Td, Th } from "@/components/Table";
import { dateTime, money, percent, signClass, signedMoney } from "@/lib/format";
import { useTitle } from "@/layout/useTitle";

/**
 * The comparison charts are bundled apart, like Analytics: they are the only
 * other screen that pulls Recharts in.
 */
const Comparison = lazy(() =>
  import("@/components/charts/Comparison").then((m) => ({ default: m.Comparison })),
);

/**
 * Comparing experiments (F5.6).
 *
 * **This is what makes the project an experiment and not a bot**, and the shape
 * follows from that: the interesting comparison is one profile against its own
 * duplicate with a single parameter changed (F5.4), so the screen has to put the
 * curves and the figures of several experiments beside each other without
 * making them look more alike than they are.
 *
 * ⚠️ **The chart is in %, the table is in each one's own currency.** Those are
 * two different answers to the same problem: the project converts currency
 * nowhere (D8), so a shared axis can only carry an indexed figure, while a table
 * has a column per experiment and each cell can wear its own symbol. Putting
 * euros and dollars on one axis is FE.8's mistake drawn to scale.
 *
 * ⚠️ **Two experiments at once is a real limit today** (F6.10): `cycle_times`
 * lives in the schema and nobody reads it, so the scheduler runs one set of
 * hours for every active profile. Comparing histories that already exist works
 * fine; running two experiments on different schedules does not yet.
 *
 * @return The rendered screen.
 */
export function Compare() {
  useTitle("Comparar");
  const profiles = useProfiles(true);
  const [chosen, setChosen] = useState<string[]>([]);

  return (
    <>
      <PageTitle>Comparar experimentos</PageTitle>

      <Section query={profiles}>
        {(all: ProfileSummary[]) =>
          all.length < 2 ? (
            <Card padding="p-6" dashed>
              <p className="text-[13px] text-text-secondary">
                Hace falta más de un experimento para comparar. El gesto normal es duplicar
                uno y cambiarle un solo parámetro: así las dos curvas se diferencian en eso y
                no en cinco cosas a la vez.
              </p>
            </Card>
          ) : (
            <Picked all={all} chosen={chosen} onChange={setChosen} />
          )
        }
      </Section>
    </>
  );
}

/**
 * The chooser plus everything that hangs off the choice.
 *
 * @param props - Props.
 * @param props.all - Every profile, archived included.
 * @param props.chosen - Names currently selected.
 * @param props.onChange - Replaces the selection.
 * @return The rendered chooser, chart and table.
 */
function Picked({
  all,
  chosen,
  onChange,
}: {
  all: ProfileSummary[];
  chosen: string[];
  onChange: (next: string[]) => void;
}) {
  const selected = all.filter((p) => chosen.includes(p.name));

  // One request per experiment, in parallel and cached by TanStack. It is the
  // decision in F4's header: several typed endpoints beat one untyped bundle,
  // and here it also means picking a fourth experiment does not refetch the
  // three already on screen.
  const analytics = useQueries({
    queries: selected.map((profile) => ({
      queryKey: keys.analytics(profile.name),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        api.get<Analytics>("/api/analytics", { profile: profile.name }, signal),
    })),
  });

  const loading = analytics.some((q) => q.isPending);
  const failed = analytics.find((q) => q.error)?.error;

  const series = selected
    .map((profile, index) => ({
      name: profile.name,
      points: indexed(analytics[index]?.data, profile.metrics.initial_budget),
    }))
    .filter((s) => s.points.length > 0);

  return (
    <>
      <fieldset className="mb-6">
        <legend className="sr-only">Experimentos a comparar</legend>
        <SectionTitle className="mb-3">Qué comparar</SectionTitle>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {all.map((profile) => (
            <label key={profile.id} className="flex items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={chosen.includes(profile.name)}
                onChange={(e) =>
                  onChange(
                    e.target.checked
                      ? [...chosen, profile.name]
                      : chosen.filter((n) => n !== profile.name),
                  )
                }
              />
              {profile.name}
              <span className="text-text-muted">{profile.currency}</span>
              <ProfileStatus status={profile.status} />
            </label>
          ))}
        </div>
      </fieldset>

      {chosen.length === 0 ? (
        <Card padding="p-6" dashed>
          <p className="text-[13px] text-text-secondary">
            Elige dos experimentos para ver sus curvas una encima de otra. Con más de dos se
            dibuja uno por gráfica, compartiendo escala.
          </p>
        </Card>
      ) : (
        <>
          {failed && <Alert className="mb-4">{failed.message}</Alert>}
          {loading && <Loading text="Cargando las curvas…" />}

          {!loading && !failed && (
            <div className="mb-8">
              <Suspense fallback={<Loading text="Cargando gráficas…" />}>
                <Comparison series={series} />
              </Suspense>
            </div>
          )}

          <SectionTitle className="mb-3">Métricas lado a lado</SectionTitle>
          <SideBySide profiles={selected} />
        </>
      )}
    </>
  );
}

/**
 * Turns an equity curve into a return curve against the assigned budget.
 *
 * Indexing is what makes two experiments comparable at all: 200 on a budget of
 * 10.000 and 200 on a budget of 1.000 are the same money and not the same
 * result, and if the budgets are in different currencies they are not even the
 * same money.
 *
 * @param data - The analytics of one experiment, if it has landed.
 * @param budget - Its assigned budget.
 * @return One point per snapshot, as a percentage return.
 */
function indexed(
  data: Analytics | undefined,
  budget: number | null | undefined,
): { as_of: string; return_pct: number }[] {
  if (!data?.equity_curve?.length || !budget) return [];
  return data.equity_curve.map((point) => ({
    as_of: point.as_of,
    return_pct: (point.equity / budget - 1) * 100,
  }));
}

/**
 * The metrics table, one column per experiment.
 *
 * **One column each and not one row each**: the rows are the metrics, so two
 * experiments' capital sit side by side and get read against each other, which
 * is the whole point. With experiments as rows, comparing a figure means
 * scanning down a column of mixed units.
 *
 * @param props - Table props.
 * @param props.profiles - The chosen experiments.
 * @return The rendered table.
 */
function SideBySide({ profiles }: { profiles: ProfileSummary[] }) {
  const rows: {
    label: string;
    cell: (p: ProfileSummary) => { text: string; className?: string; title?: string };
  }[] = [
    {
      label: "Mercado",
      cell: (p) => ({ text: `${p.market.toUpperCase()} · ${p.currency}` }),
    },
    { label: "Estado", cell: (p) => ({ text: p.status }) },
    {
      label: "Selección de candidatos",
      // The row that decides how the comparison is read: the control is meant
      // to do worse, and without this line its numbers look like a failed
      // experiment instead of the baseline (R7).
      cell: (p) => ({
        text: p.screener_mode === "random" ? "aleatoria (control)" : "por puntuación",
        title:
          p.screener_mode === "random"
            ? "Grupo de control: los candidatos no están elegidos. Es contra lo que se mide si el criterio del modelo aporta algo."
            : undefined,
      }),
    },
    { label: "Modelo", cell: (p) => ({ text: `${p.llm_provider}/${p.llm_model}` }) },
    { label: "Criterio de riesgo", cell: (p) => ({ text: p.risk_summary, title: p.risk_summary }) },
    {
      label: "Presupuesto",
      // Each cell wears its own symbol. That is FE.8, and on this screen it is
      // not a nicety: two columns side by side is exactly where a euro figure
      // gets read as dollars.
      cell: (p) => ({ text: money(p.metrics.initial_budget, p.currency_symbol) }),
    },
    { label: "Capital", cell: (p) => ({ text: money(p.metrics.equity, p.currency_symbol) }) },
    {
      label: "Rentabilidad",
      cell: (p) => ({
        text: percent(p.metrics.total_return_pct, { sign: true }),
        className: signClass(p.metrics.total_return_pct),
      }),
    },
    {
      label: "P&L realizado",
      cell: (p) => ({
        text: signedMoney(p.metrics.realized_pnl, p.currency_symbol),
        className: signClass(p.metrics.realized_pnl),
      }),
    },
    { label: "Posiciones abiertas", cell: (p) => ({ text: String(p.metrics.open_positions ?? 0) }) },
    {
      label: "Operaciones cerradas",
      cell: (p) => ({
        text: String(p.metrics.closed_trades ?? 0),
        title:
          (p.metrics.closed_trades ?? 0) < 30
            ? "Menos de 30: todavía no hay con qué leer la calibración."
            : undefined,
      }),
    },
    {
      label: "Aciertos",
      cell: (p) => ({
        text: `${percent(p.metrics.win_rate_pct)} (${p.metrics.closed_trades ?? 0})`,
        title: "El porcentaje va con el número de operaciones: sin él no significa nada.",
      }),
    },
    { label: "Ciclos", cell: (p) => ({ text: String(p.metrics.cycles ?? 0) }) },
    { label: "Último ciclo", cell: (p) => ({ text: dateTime(p.metrics.last_cycle_at) }) },
  ];

  return (
    <Table title="Métricas de los experimentos comparados">
      <TableHead>
        <Th>Métrica</Th>
        {profiles.map((p) => (
          <Th key={p.id} numeric>
            {p.name}
          </Th>
        ))}
      </TableHead>
      <tbody>
        {rows.map((row) => (
          <Row key={row.label}>
            {/* The metric names the row: without `scope="row"` a screen reader
                reads "10.240,00 €" without saying which figure it is, and the
                whole table is one figure per row. */}
            <Td header className="text-text-muted">
              {row.label}
            </Td>
            {profiles.map((p) => {
              const cell = row.cell(p);
              return (
                <Td key={p.id} numeric className={cell.className} title={cell.title}>
                  {cell.text}
                </Td>
              );
            })}
          </Row>
        ))}
      </tbody>
    </Table>
  );
}
