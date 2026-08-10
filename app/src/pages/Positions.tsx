import { useState } from "react";

import { usePositions } from "@/api/hooks";
import type { PositionRow } from "@/api/types";
import { PageTitle } from "@/components/pieces";
import { PriceSource } from "@/components/PriceSource";
import { Section } from "@/components/Section";
import { TableHead, Row, Pagination, Table, Td, Th, Empty } from "@/components/Table";
import {
  signClass,
  money,
  signedMoney,
  quantity,
  dateTime,
  percent,
} from "@/lib/format";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

const LIMIT = 50;

/**
 * Open and closed positions (F4.7).
 *
 * They are **two tables and not one with a filter**, because the columns that
 * matter differ: on an open one you look at unrealised P&L and the distance to
 * the stop, and on a closed one at the exit price and the reason. Putting them
 * together would leave half the table full of dashes.
 *
 * @return The rendered screen with both tables.
 */
export function Positions() {
  const { profile, ref } = useActiveProfile();
  useTitle("Posiciones", profile?.name);
  const [offset, setOffset] = useState(0);

  const open = usePositions(ref, { status: "open", limit: 200 });
  const closed = usePositions(ref, {
    status: "closed",
    limit: LIMIT,
    offset,
  });

  const symbol = profile?.currency_symbol ?? "";

  return (
    <>
      <PageTitle>Posiciones</PageTitle>

      <Section title="Abiertas" query={open}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>Ninguna posición abierta ahora mismo.</Empty>
          ) : (
            <Table title="Posiciones abiertas">
              <TableHead>
                <Th>Símbolo</Th>
                <Th>Abierta</Th>
                <Th numeric>Cantidad</Th>
                <Th numeric>Entrada</Th>
                <Th numeric>Último</Th>
                <Th numeric>P&L</Th>
                <Th numeric>Stop</Th>
                <Th numeric>Objetivo</Th>
              </TableHead>
              <tbody>
                {page.items.map((row) => (
                  <OpenPositionTableRow key={row.id} row={row} symbol={symbol} />
                ))}
              </tbody>
            </Table>
          )
        }
      </Section>

      <Section title="Cerradas" query={closed}>
        {(page) => (
          <>
            {page.items.length === 0 ? (
              <Empty>
                Todavía no se ha cerrado ninguna posición. Solo se cierran al tocar el stop o
                el objetivo, o si el analista ve la tesis deteriorada: el horizonte en días no
                cierra nada por sí solo.
              </Empty>
            ) : (
              <Table title="Posiciones cerradas">
                <TableHead>
                  <Th>Símbolo</Th>
                  <Th>Cerrada</Th>
                  <Th numeric>Cantidad</Th>
                  <Th numeric>Entrada</Th>
                  <Th numeric>Salida</Th>
                  <Th numeric>P&L</Th>
                  <Th>Motivo</Th>
                </TableHead>
                <tbody>
                  {page.items.map((row) => (
                    <ClosedPositionTableRow key={row.id} row={row} symbol={symbol} />
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
 * One row of the open-positions table.
 *
 * @param props - Row props.
 * @param props.row - The position.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function OpenPositionTableRow({ row, symbol }: { row: PositionRow; symbol: string }) {
  return (
    <Row>
      <Td>
        <span className="font-medium">{row.symbol}</span>
        {row.thesis && (
          <p className="mt-0.5 max-w-md text-caption leading-snug text-text-muted">
            {row.thesis}
          </p>
        )}
      </Td>
      <Td className="whitespace-nowrap" title={row.opened_at}>
        {dateTime(row.opened_at)}
      </Td>
      <Td numeric>{quantity(row.qty)}</Td>
      <Td numeric>{money(row.entry_price, symbol)}</Td>
      <Td numeric>
        {money(row.last_price, symbol)}
        <PriceSource row={row} />
      </Td>
      <Td numeric className={signClass(row.unrealized_pnl)}>
        {signedMoney(row.unrealized_pnl, symbol)}
        <span className="ml-1 text-caption">
          {percent(row.unrealized_pnl_pct, { sign: true })}
        </span>
      </Td>
      <Td numeric>
        {money(row.stop_price, symbol)}
        {row.stop_distance_pct !== null && row.stop_distance_pct !== undefined && (
          <span className="ml-1 text-caption text-text-muted" title="Distancia al stop">
            {percent(row.stop_distance_pct)}
          </span>
        )}
      </Td>
      <Td numeric>{money(row.target_price, symbol)}</Td>
    </Row>
  );
}

/**
 * One row of the closed-positions table, which shows exit price and reason
 * instead of the stop.
 *
 * @param props - Row props.
 * @param props.row - The position.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function ClosedPositionTableRow({ row, symbol }: { row: PositionRow; symbol: string }) {
  return (
    <Row>
      <Td>
        <span className="font-medium">{row.symbol}</span>
      </Td>
      <Td className="whitespace-nowrap" title={row.closed_at ?? undefined}>
        {dateTime(row.closed_at)}
      </Td>
      <Td numeric>{quantity(row.qty)}</Td>
      <Td numeric>{money(row.entry_price, symbol)}</Td>
      <Td numeric>{money(row.exit_price, symbol)}</Td>
      <Td numeric className={signClass(row.realized_pnl)}>
        {signedMoney(row.realized_pnl, symbol)}
      </Td>
      <Td className="max-w-sm text-caption text-text-secondary">{row.exit_reason ?? "—"}</Td>
    </Row>
  );
}
