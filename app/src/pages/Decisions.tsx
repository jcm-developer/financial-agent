import { useState } from "react";

import { useDecisions } from "@/api/hooks";
import type { DecisionRow } from "@/api/types";
import { Input, Tag, PageTitle } from "@/components/pieces";
import { Select } from "@/components/Select";
import { Section } from "@/components/Section";
import { TableHead, Row, Pagination, Table, Td, Th, Empty } from "@/components/Table";
import { money, dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

const LIMIT = 50;

/**
 * What the analyst proposed and what the Risk Manager said (F4.7).
 *
 * This is the experiment's central screen: it is where you see whether the model
 * discriminates between opportunities or hands the same conviction to
 * everything. That is why the thesis and the risks are shown in the row itself
 * and not behind a click — text you have to go looking for does not get read,
 * and then the screen measures something else.
 *
 * @return The rendered screen.
 */
export function Decisions() {
  const { profile, ref } = useActiveProfile();
  useTitle("Decisiones", profile?.name);
  const [offset, setOffset] = useState(0);
  const [symbolFilter, setSymbolFilter] = useState("");
  const [action, setAction] = useState("");
  const [verdict, setVerdict] = useState("");

  const query = useDecisions(ref, {
    symbol: symbolFilter || undefined,
    action: action || undefined,
    verdict: verdict || undefined,
    limit: LIMIT,
    offset,
  });

  const symbol = profile?.currency_symbol ?? "";

  function changeFilter(apply: () => void) {
    // Changing a filter goes back to the first page: staying at the previous
    // offset gives an empty table with data behind it, and that reads as
    // "nothing matches the filter", which is not true.
    apply();
    setOffset(0);
  }

  return (
    <>
      <PageTitle>Decisiones</PageTitle>

      <div className="mb-5 flex flex-wrap items-end gap-3">
        <Input
          label="Símbolo"
          type="search"
          value={symbolFilter}
          placeholder="SAN.MC"
          onChange={(event) => changeFilter(() => setSymbolFilter(event.target.value))}
          className="w-32"
        />
        <Select
          label="Acción"
          value={action}
          options={[
            ["", "Todas"],
            ["buy", "Compra"],
            ["sell", "Venta"],
            ["hold", "Mantener"],
          ]}
          onChange={(next) => changeFilter(() => setAction(next))}
        />
        <Select
          label="Veredicto"
          value={verdict}
          options={[
            ["", "Todos"],
            ["approved", "Aprobadas"],
            ["rejected", "Rechazadas"],
          ]}
          onChange={(next) => changeFilter(() => setVerdict(next))}
        />
      </div>

      <Section query={query}>
        {(page) => (
          <>
            {page.items.length === 0 ? (
              <Empty>
                {symbolFilter || action || verdict
                  ? "Ninguna decisión cumple estos filtros."
                  : "El analista no ha registrado ninguna decisión todavía. Cada ciclo guarda una por candidato evaluado, incluidas las de mantener."}
              </Empty>
            ) : (
              <Table title="Decisiones del analista con el veredicto de riesgo">
                <TableHead>
                  <Th>Fecha</Th>
                  <Th>Símbolo</Th>
                  <Th>Acción</Th>
                  <Th numeric>Convicción</Th>
                  <Th>Tesis y riesgos</Th>
                  <Th>Veredicto</Th>
                </TableHead>
                <tbody>
                  {page.items.map((row) => (
                    <DecisionTableRow key={row.id} row={row} symbol={symbol} />
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
 * One row of the decisions table, thesis and risks included.
 *
 * @param props - Row props.
 * @param props.row - The decision and the risk manager's verdict on it.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function DecisionTableRow({ row, symbol }: { row: DecisionRow; symbol: string }) {
  return (
    <Row>
      <Td className="whitespace-nowrap" title={row.created_at}>
        {dateTime(row.created_at)}
      </Td>
      <Td>
        <span className="font-medium">{row.symbol}</span>
        {/* 'entry' or 'exit': the same action means different things depending
            on whether entering was being evaluated or an open position reviewed. */}
        <Tag
          tone="neutral"
          title={
            row.kind === "entry"
              ? "Se evaluaba entrar en el activo"
              : "Se revisaba una posición ya abierta"
          }
        >
          {row.kind}
        </Tag>
      </Td>
      <Td>
        <span
          className={cn(
            "font-medium",
            row.action === "buy" && "text-positive-ink",
            row.action === "sell" && "text-negative-ink",
            row.action === "hold" && "text-text-muted",
          )}
        >
          {row.action}
        </span>
      </Td>
      <Td numeric>{row.conviction}</Td>
      <Td className="max-w-lg">
        {row.thesis ? (
          <p className="text-caption leading-snug">{row.thesis}</p>
        ) : (
          <p className="text-caption text-text-muted">Sin tesis.</p>
        )}
        {row.risks && (
          <p className="mt-1 text-caption leading-snug text-text-muted">
            <span className="font-medium">Riesgos:</span> {row.risks}
          </p>
        )}
        <p className="mt-1 text-caption text-text-muted">
          {row.reference_price !== null && row.reference_price !== undefined && (
            <>ref. {money(row.reference_price, symbol)} · </>
          )}
          {row.horizon_days ? `${row.horizon_days} d · ` : ""}
          {row.llm_model ?? "modelo desconocido"}
        </p>
      </Td>
      <Td>
        {row.verdict ? (
          <>
            <span
              className={
                row.verdict === "approved"
                  ? "font-medium text-delta-good"
                  : "font-medium text-delta-bad"
              }
            >
              {row.verdict === "approved" ? "aprobada" : "rechazada"}
            </span>
            {row.rule && (
              <p className="mt-0.5 text-caption text-text-muted">{row.rule}</p>
            )}
            {row.risk_reason && (
              <p className="mt-0.5 max-w-xs text-caption leading-snug text-text-secondary">
                {row.risk_reason}
              </p>
            )}
            {row.approved_notional !== null && row.approved_notional !== undefined && (
              <p className="tabular mt-0.5 text-caption text-text-muted">
                {money(row.approved_notional, symbol)}
              </p>
            )}
          </>
        ) : (
          // A hold decision does not go through the Risk Manager: there is
          // nothing to size. Saying so stops it from looking like a gap.
          <span className="text-caption text-text-muted">
            {row.action === "hold" ? "no aplica" : "sin veredicto"}
          </span>
        )}
        {row.order_status && (
          <p className="mt-0.5 text-caption text-text-muted">
            orden: {row.order_status}
            {row.filled_avg_price !== null && row.filled_avg_price !== undefined
              ? ` a ${money(row.filled_avg_price, symbol)}`
              : ""}
          </p>
        )}
      </Td>
    </Row>
  );
}
