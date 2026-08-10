import { useState } from "react";

import { useOrders } from "@/api/hooks";
import type { OrderRow } from "@/api/types";
import { Input, PageTitle } from "@/components/pieces";
import { Section } from "@/components/Section";
import { TableHead, Row, Pagination, Table, Td, Th, Empty } from "@/components/Table";
import { quantity, money, dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

const LIMIT = 50;

/**
 * Orders sent, and also the ones that were NOT sent (F4.7).
 *
 * The unexecuted ones are the interesting half: an order in `canceled` or
 * `dry_run` means the agent decided to trade and could not —market closed, or
 * the simulator running dry— and without seeing them it looks as if the analyst
 * proposed nothing. The reason column carries the `error` the cycle left.
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
              <Table title="Órdenes enviadas y no enviadas">
                <TableHead>
                  <Th>Enviada</Th>
                  <Th>Símbolo</Th>
                  <Th>Lado</Th>
                  <Th numeric>Cantidad</Th>
                  <Th numeric>Ejecutada</Th>
                  <Th numeric>Precio</Th>
                  <Th>Estado</Th>
                </TableHead>
                <tbody>
                  {page.items.map((row) => (
                    <OrderTableRow key={row.id} row={row} symbol={symbol} />
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
 * One row of the orders table.
 *
 * @param props - Row props.
 * @param props.row - The order, sent or not.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function OrderTableRow({ row, symbol }: { row: OrderRow; symbol: string }) {
  return (
    <Row>
      <Td className="whitespace-nowrap" title={row.submitted_at}>
        {dateTime(row.submitted_at)}
      </Td>
      <Td>
        <span className="font-medium">{row.symbol}</span>
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
        {row.error && (
          <p className="mt-0.5 max-w-sm text-caption leading-snug text-text-secondary">
            {row.error}
          </p>
        )}
      </Td>
    </Row>
  );
}
