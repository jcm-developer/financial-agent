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
 * @return The rendered screen, with the per-rule tally above the table.
 */
export function Risk() {
  const { profile, ref } = useActiveProfile();
  useTitle("Riesgo", profile?.name);
  const [offset, setOffset] = useState(0);
  const [verdict, setVerdict] = useState("rejected");

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
          ["rejected", "Rechazados"],
          ["approved", "Aprobados"],
          ["", "Todos"],
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
          </p>
        </section>
      )}

      <Section query={query}>
        {(page) => (
          <>
            {page.items.length === 0 ? (
              <Empty>
                {verdict === "rejected"
                  ? "El Risk Manager no ha rechazado nada todavía. Con pocas propuestas es lo esperable; si sigue así con muchas, conviene comprobar que los límites están donde se cree."
                  : "No hay eventos con este veredicto."}
              </Empty>
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
