import { useState } from "react";
import { ChevronRight } from "lucide-react";

import { useOrders } from "@/api/hooks";
import type { OrderRow } from "@/api/types";
import { GroupedRows } from "@/components/GroupedRows";
import { Input, LinkButton, PageTitle } from "@/components/pieces";
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

const LIMIT = 50;

/** Columns, so the group headers and the unfolded reason span the table. */
const COLUMNS = 6;

/**
 * Orders sent, and also the ones that were NOT sent (F4.7).
 *
 * The unexecuted ones are the interesting half: an order in `canceled` or
 * `dry_run` means the agent decided to trade and could not —market closed, or
 * the simulator running dry— and without seeing them it looks as if the analyst
 * proposed nothing.
 *
 * **Grouped by session and cycle since F10.10**, like Decisiones and Riesgo: an
 * order is written by a cycle, and a flat list gave no way to see that four of
 * them came from one ten-minute burst. The groups are read off the page and not
 * from `/api/cycles`, because this endpoint takes no `cycle_id` and so there is
 * no per-cycle query to be lazy with — which is also why the headers count the
 * page and say so.
 *
 * @return The rendered screen.
 */
export function Orders() {
  const { profile, ref } = useActiveProfile();
  useTitle("Órdenes", profile?.name);
  const [offset, setOffset] = useState(0);
  const [symbolFilter, setSymbolFilter] = useState("");

  const query = useOrders(ref, {
    symbol: symbolFilter || undefined,
    limit: LIMIT,
    offset,
  });

  const symbol = profile?.currency_symbol ?? "";

  return (
    <>
      <PageTitle>Órdenes</PageTitle>

      <Input
        label="Símbolo"
        type="search"
        value={symbolFilter}
        placeholder="SAN.MC"
        onChange={(event) => {
          setSymbolFilter(event.target.value);
          setOffset(0);
        }}
        fieldClass="mb-5 w-fit"
        className="w-32"
      />

      <Section query={query}>
        {(page) => (
          <>
            {page.items.length === 0 ? (
              <Empty>
                {symbolFilter
                  ? `Ninguna orden de ${symbolFilter}.`
                  : "No se ha enviado ninguna orden todavía. Aquí aparecerán también las que el agente aprobó pero no pudo ejecutar."}
              </Empty>
            ) : (
              <Table title="Órdenes enviadas y no enviadas, agrupadas por jornada y por ciclo">
                <TableHead>
                  <Th>Símbolo</Th>
                  <Th>Lado</Th>
                  <Th numeric>Cantidad</Th>
                  <Th numeric>Ejecutada</Th>
                  <Th numeric>Precio</Th>
                  <Th>Estado</Th>
                </TableHead>
                <GroupedRows
                  days={groupByDayAndCycle(
                    page.items,
                    (row) => row.submitted_at,
                    (row) => row.cycle_id,
                  )}
                  columns={COLUMNS}
                  noun={["orden", "órdenes"]}
                  openAll={Boolean(symbolFilter)}
                >
                  {(row) => <OrderTableRow key={row.id} row={row} symbol={symbol} />}
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
 * The colour an order status deserves.
 *
 * `filled` is the normal case; the rest earns colour because it means something
 * happened.
 *
 * @param status - Order status as the broker recorded it.
 * @return The Tailwind text-colour class.
 */
function statusClass(status: string): string {
  if (status === "filled") return "text-delta-good";
  if (status === "failed") return "text-delta-bad";
  if (status === "canceled" || status === "dry_run") return "text-warning";
  return "text-text-secondary";
}

/**
 * One row of the orders table, with the reason it failed folded under it.
 *
 * **The reason was prose inside a cell and the `max-w-sm` was not holding it**,
 * which is the same pair of mistakes F10.7 found in closed positions: a
 * `<table>` with the default `auto` layout ignores a `max-width` on a `<td>`, so
 * the column took whatever the message asked for, and a single failed order
 * turned a 48 px row into a four-line one. Folded, the status column is back to
 * a word you can read down.
 *
 * **The date is not a column any more**: the cycle header above the row carries
 * the time and the session header the day. It stays in the fold and in the
 * symbol's `title`, because an order's `submitted_at` is not the cycle's start —
 * a cycle sends its orders minutes into its own run.
 *
 * An order with no error gets no toggle, so nothing invites a click that would
 * unfold nothing.
 *
 * @param props - Row props.
 * @param props.row - The order, sent or not.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row, plus its detail row when unfolded.
 */
function OrderTableRow({ row, symbol }: { row: OrderRow; symbol: string }) {
  const [open, setOpen] = useState(false);
  const error = row.error?.trim();

  return (
    <>
      <Row expanded={Boolean(error) && open}>
        <Td title={row.submitted_at}>
          {error ? (
            <LinkButton
              variant="subtle"
              className="inline-flex items-center gap-1 font-medium"
              aria-expanded={open}
              title={open ? "Ocultar el motivo" : "Ver por qué no se ejecutó"}
              onClick={() => setOpen((value) => !value)}
            >
              <ChevronRight
                aria-hidden
                className={cn(
                  "size-3.5 shrink-0 transition-transform duration-150",
                  open && "rotate-90",
                )}
              />
              {row.symbol}
            </LinkButton>
          ) : (
            <span className="font-medium">{row.symbol}</span>
          )}
        </Td>
        <Td>
          <span className={row.side === "buy" ? "text-positive-ink" : "text-negative-ink"}>
            {row.side === "buy" ? "compra" : "venta"}
          </span>
        </Td>
        <Td numeric>{quantity(row.qty)}</Td>
        <Td numeric>{quantity(row.filled_qty)}</Td>
        <Td numeric>{money(row.filled_avg_price, symbol)}</Td>
        <Td>
          <span className={cn("font-medium", statusClass(row.status))}>{row.status}</span>
        </Td>
      </Row>

      {error && open && (
        <DetailRow columns={COLUMNS}>
          <p className="text-caption leading-snug text-text-secondary">{error}</p>
          <p className="mt-1 text-caption text-text-muted">
            enviada {dateTime(row.submitted_at)}
          </p>
        </DetailRow>
      )}
    </>
  );
}
