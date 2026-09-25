import { useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";

import { useRiskEvents } from "@/api/hooks";
import type { RiskEventRow } from "@/api/types";
import { GroupedRows } from "@/components/GroupedRows";
import { Badge, LinkButton, PageTitle, SectionTitle } from "@/components/pieces";
import { Select } from "@/components/Select";
import { Section } from "@/components/Section";
import {
  TableHead,
  Row,
  DetailRow,
  Pagination,
  Table,
  Td,
  Th,
  Empty,
} from "@/components/Table";
import { groupByDayAndCycle } from "@/lib/grouping";
import { quantity, money, dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";
import { ruleLabel } from "@/lib/labels";

const LIMIT = 50;

/** Columns, so the group headers and the unfolded reason span the table. */
const COLUMNS = 4;

/**
 * Risk Manager events.
 *
 * The rejections are the evidence that the barrier works, and **which limit the
 * model hits most often is one of the experiment's questions**, not a detail of
 * a row. Hence the per-rule tally on top: if 80 % of the rejections come from
 * the same limit, either the model insists on something that does not fit or
 * that limit is set wrong, and both are worth knowing.
 *
 * The tally is computed **over the page being viewed** and says so, because the
 * API offers no per-rule aggregate: presenting it as the experiment's total
 * would be inventing a statistic.
 *
 * **The filter opens on «Todos» and not on the rejections.** Opening on a
 * filtered view is the screen lying about how much there is: it showed an empty
 * table on an experiment that had approved a dozen proposals, and the only way
 * to find that out was to notice the dropdown was not where it looked. What the
 * screen answers is what the Risk Manager did, and a rejection only means
 * something next to the approvals it sits among.
 *
 * **Grouped by session and cycle since F10.10**, like Decisiones and Órdenes.
 * It is the screen the grouping helps most: the Risk Manager writes one event
 * per proposal it sizes or blocks, so a single cycle contributes a dozen or more
 * and the flat list gave no way to see that a run of sixteen rejections was one
 * cycle hitting the same wall sixteen times rather than a pattern across the
 * week.
 *
 * @return The rendered screen, with the per-rule tally above the table.
 */
export function Risk() {
  const { profile, ref } = useActiveProfile();
  useTitle("Riesgo", profile?.name);
  const [offset, setOffset] = useState(0);
  const [verdict, setVerdict] = useState("");

  const query = useRiskEvents(ref, {
    verdict: verdict || undefined,
    limit: LIMIT,
    offset,
  });

  const symbol = profile?.currency_symbol ?? "";
  const byRule = useMemo(() => countByRule(query.data?.items ?? []), [query.data]);

  return (
    <>
      <PageTitle>Eventos de riesgo</PageTitle>

      <Select
        label="Veredicto"
        value={verdict}
        options={[
          ["", "Todos"],
          ["rejected", "Rechazados"],
          ["approved", "Aprobados"],
        ]}
        onChange={(next) => {
          setVerdict(next);
          setOffset(0);
        }}
        fieldClass="mb-8 w-fit"
      />

      {byRule.length > 0 && (
        <section className="mb-6">
          <SectionTitle className="mb-2">Por regla</SectionTitle>
          <ul className="flex flex-wrap gap-2">
            {byRule.map(([rule, times]) => (
              <li key={rule}>
                <Badge>
                  {ruleLabel(rule)} <span className="tabular font-semibold">{times}</span>
                </Badge>
              </li>
            ))}
          </ul>
          {/* Counted over the page, because the API has no per-rule aggregate:
              when the page is not the whole history, the line says so. */}
          {query.data && query.data.total > query.data.items.length && (
            <p className="mt-2 text-caption text-text-muted">
              Sobre los {query.data.items.length} eventos de esta página, de{" "}
              {query.data.total}.
            </p>
          )}
        </section>
      )}

      <Section query={query}>
        {(page) => (
          <>
            {page.items.length === 0 ? (
              <Empty>{emptyText(verdict)}</Empty>
            ) : (
              <Table title="Veredictos de riesgo, agrupados por jornada y por ciclo">
                <TableHead>
                  <Th>Símbolo</Th>
                  <Th>Veredicto</Th>
                  <Th>Regla</Th>
                  <Th numeric>Cantidad</Th>
                </TableHead>
                <GroupedRows
                  days={groupByDayAndCycle(
                    page.items,
                    (row) => row.created_at,
                    (row) => row.cycle_id,
                  )}
                  columns={COLUMNS}
                  noun={["evento", "eventos"]}
                  openAll={Boolean(verdict)}
                >
                  {(row) => <RiskEventTableRow key={row.id} row={row} symbol={symbol} />}
                </GroupedRows>
              </Table>
            )}
            <Pagination
              total={page.total}
              limit={page.limit}
              offset={page.offset}
              onChange={setOffset}
            />
          </>
        )}
      </Section>
    </>
  );
}

/**
 * The empty state, worded per verdict.
 *
 * An empty table means three different things here —nothing evaluated yet,
 * nothing blocked, nothing let through— and a single generic line would send
 * you to the database to find out which.
 *
 * @param verdict - The filter in force; the empty string is «Todos».
 * @return The wording for that case.
 */
function emptyText(verdict: string): string {
  if (verdict === "rejected") return "No hay ninguna propuesta rechazada.";
  if (verdict === "approved") return "No hay ninguna propuesta aprobada.";
  return "Todavía no hay veredictos de riesgo.";
}

/**
 * Tallies the events by rule.
 *
 * @param rows - Events on the page being viewed, not the whole experiment.
 * @return `[rule, count]` pairs, most frequent first. Events with no rule are
 *     counted under `sin regla` rather than dropped, so the total still adds up.
 */
function countByRule(rows: RiskEventRow[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const rule = row.rule ?? "sin regla";
    counts.set(rule, (counts.get(rule) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

/**
 * One row of the risk-events table, with the reason folded under it.
 *
 * **`reason` is prose and `Motivo` was a column of it**, held back by a
 * `max-w-md` that a `<table>` with the default `auto` layout ignores — the same
 * pair of mistakes F10.7 found in closed positions. The Risk Manager writes a
 * full sentence on every event, so *every* row was two or three lines tall and
 * the tally above the table, which is what the screen is for, sat over something
 * you had to scroll to compare.
 *
 * **What stays in the column is the rule**, as `<code>`, exactly as in closed
 * positions: a rule is an identifier and is compared down the column, a sentence
 * is not. Unlike a thesis, every event has a reason, so every row here has a
 * toggle — that is not an oversight, it is what the data is.
 *
 * @param props - Row props.
 * @param props.row - The event, with the rule it tripped.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row, plus its detail row when unfolded.
 */
function RiskEventTableRow({ row, symbol }: { row: RiskEventRow; symbol: string }) {
  const [open, setOpen] = useState(false);
  const reason = row.reason?.trim();

  // The kill switch belongs to no symbol: it belongs to the whole book.
  const name = row.symbol ?? "toda la cartera";

  return (
    <>
      <Row expanded={Boolean(reason) && open}>
        <Td title={dateTime(row.created_at)}>
          {reason ? (
            <LinkButton
              variant="subtle"
              className={cn(
                "inline-flex items-center gap-1",
                row.symbol ? "font-medium" : "text-text-muted",
              )}
              aria-expanded={open}
              title={open ? "Ocultar el motivo" : "Ver el motivo"}
              onClick={() => setOpen((value) => !value)}
            >
              <ChevronRight
                aria-hidden
                className={cn(
                  "size-3.5 shrink-0 transition-transform duration-150",
                  open && "rotate-90",
                )}
              />
              {name}
            </LinkButton>
          ) : (
            <span className={row.symbol ? "font-medium" : "text-text-muted"}>{name}</span>
          )}
        </Td>
        <Td>
          <span
            className={
              row.verdict === "approved"
                ? "font-medium text-delta-good"
                : "font-medium text-delta-bad"
            }
          >
            {row.verdict === "approved" ? "aprobado" : "rechazado"}
          </span>
        </Td>
        <Td>
          <span className="text-caption">{row.rule ? ruleLabel(row.rule) : "—"}</span>
        </Td>
        <Td numeric>
          {quantity(row.approved_qty)}
          {row.approved_notional !== null && row.approved_notional !== undefined && (
            <p className="text-caption text-text-muted">
              {money(row.approved_notional, symbol)}
            </p>
          )}
        </Td>
      </Row>

      {reason && open && (
        <DetailRow columns={COLUMNS}>
          <p className="text-caption leading-snug text-text-secondary">{reason}</p>
          <p className="mt-1 text-caption text-text-muted">{dateTime(row.created_at)}</p>
        </DetailRow>
      )}
    </>
  );
}
