import { useMemo, useState } from "react";

import { useRiskEvents } from "@/api/hooks";
import type { RiskEventRow } from "@/api/types";
import { Badge, PageTitle, SectionTitle } from "@/components/pieces";
import { Select } from "@/components/Select";
import { Section } from "@/components/Section";
import { TableHead, Row, Pagination, Table, Td, Th, Empty } from "@/components/Table";
import { quantity, money, dateTime } from "@/lib/format";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

const LIMIT = 50;

/**
 * Risk Manager events (F4.7).
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
                  {rule} <span className="tabular font-semibold">{times}</span>
                </Badge>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-caption text-text-muted">
            Contado sobre las {query.data?.items.length ?? 0} filas de esta página, no
            sobre el histórico completo.
            {verdict === "" &&
              " Con «Todos», la regla de un aprobado dice qué límite fijó el tamaño, no qué lo bloqueó."}
          </p>
        </section>
      )}

      <Section query={query}>
        {(page) => (
          <>
            {page.items.length === 0 ? (
              <Empty>{emptyText(verdict)}</Empty>
            ) : (
              <Table title="Veredictos del Risk Manager">
                <TableHead>
                  <Th>Fecha</Th>
                  <Th>Símbolo</Th>
                  <Th>Veredicto</Th>
                  <Th>Regla</Th>
                  <Th>Motivo</Th>
                  <Th numeric>Cantidad</Th>
                </TableHead>
                <tbody>
                  {page.items.map((row) => (
                    <RiskEventTableRow key={row.id} row={row} symbol={symbol} />
                  ))}
                </tbody>
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
 * An empty table means three different things here, and telling them apart is
 * most of what the screen is for: the Risk Manager has not evaluated anything
 * yet, it has blocked nothing, or it has let nothing through. A single generic
 * line would send you to the database to find out which of the three it is —
 * and the third one is the only one worth worrying about.
 *
 * @param verdict - The filter in force; the empty string is «Todos».
 * @return The wording for that case.
 */
function emptyText(verdict: string): string {
  if (verdict === "rejected") {
    return (
      "El Risk Manager no ha rechazado nada todavía. Con pocas propuestas es lo " +
      "esperable; si sigue así con muchas, conviene comprobar que los límites están " +
      "donde se cree."
    );
  }
  if (verdict === "approved") {
    return (
      "Ninguna propuesta ha pasado el Risk Manager todavía. Si en «Todos» hay " +
      "rechazos, su regla dice qué límite está frenando al modelo."
    );
  }
  return (
    "El Risk Manager no ha emitido ningún veredicto todavía: no ha llegado a " +
    "evaluar ninguna propuesta. Los primeros aparecen en cuanto un ciclo proponga " +
    "una operación."
  );
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
 * One row of the risk-events table.
 *
 * @param props - Row props.
 * @param props.row - The event, with the rule it tripped.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function RiskEventTableRow({ row, symbol }: { row: RiskEventRow; symbol: string }) {
  return (
    <Row>
      <Td className="whitespace-nowrap" title={row.created_at}>
        {dateTime(row.created_at)}
      </Td>
      <Td>
        {/* The kill switch belongs to no symbol: it belongs to the whole book. */}
        {row.symbol ? (
          <span className="font-medium">{row.symbol}</span>
        ) : (
          <span className="text-text-muted">toda la cartera</span>
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
        <code className="text-caption">{row.rule ?? "—"}</code>
      </Td>
      <Td className="max-w-md text-caption leading-snug">{row.reason}</Td>
      <Td numeric>
        {quantity(row.approved_qty)}
        {row.approved_notional !== null && row.approved_notional !== undefined && (
          <p className="text-caption text-text-muted">
            {money(row.approved_notional, symbol)}
          </p>
        )}
      </Td>
    </Row>
  );
}
