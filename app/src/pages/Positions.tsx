import { useState } from "react";
import { ChevronRight } from "lucide-react";

import { usePositions } from "@/api/hooks";
import type { PositionRow } from "@/api/types";
import { Figure, LinkButton, PageTitle } from "@/components/pieces";
import { PriceSource } from "@/components/PriceSource";
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
import {
  signClass,
  money,
  signedMoney,
  quantity,
  dateTime,
  percent,
} from "@/lib/format";
import { summarizeOpen, type OpenSummary } from "@/lib/portfolio";
import { cn } from "@/lib/utils";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

const LIMIT = 50;

/** Columns of the open table, so the unfolded thesis spans all of them. */
const OPEN_COLUMNS = 8;

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
            <>
              <Totals summary={summarizeOpen(page.items)} symbol={symbol} />
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
            </>
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
 * The four figures of the open book, over the table they add up.
 *
 * **They are computed from the rows on screen and not read from `metrics`.** The
 * reasoning is in `summarizeOpen`, and it is worth repeating because it is the
 * trap this screen invites: `/api/profiles` also carries a portfolio value, but
 * marked at the price of the last cycle's bar, while every row underneath is
 * marked at the ingestor's minute price. A card showing 9989,63 € over a table
 * of positions adding up to 9985,13 € would read as a broken sum. Here the
 * cards and the column below them are, by construction, the same arithmetic.
 *
 * **What is deliberately not here is a "cambio del día".** The reference this
 * was modelled on has one, and this project cannot honestly produce it: the row
 * carries no previous close, so the only daily figure available is
 * `metrics.day_pnl_pct`, which is the other clock. Half a card on the live price
 * and half on the cycle price is the FE.8 mistake in a different unit.
 *
 * @param props - Totals props.
 * @param props.summary - The totals, already computed by `summarizeOpen`.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row of cards.
 */
function Totals({ summary, symbol }: { summary: OpenSummary; symbol: string }) {
  const { withoutPrice, withoutStop } = summary;

  return (
    <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Figure label="Invertido" value={money(summary.invested, symbol)}>
        <span className="text-text-muted">
          coste de entrada de {summary.count === 1 ? "1 posición" : `${summary.count} posiciones`},
          sin comisiones
        </span>
      </Figure>

      <Figure label="Valor de mercado" value={money(summary.marketValue, symbol)}>
        <span className={withoutPrice ? "font-medium text-delta-bad" : "text-text-muted"}>
          {withoutPrice
            ? `${withoutPrice} sin precio, fuera del total`
            : "al último precio del ingestor"}
        </span>
      </Figure>

      <Figure
        label="P&L latente"
        value={signedMoney(summary.unrealizedPnl, symbol)}
        className={signClass(summary.unrealizedPnl)}
      >
        <span className="text-text-muted">
          {percent(summary.unrealizedPnlPct, { sign: true })} sobre lo invertido
        </span>
      </Figure>

      {/* This one is not painted by sign, and the asymmetry is on purpose: the
          other three say what is, this one says what would happen. Painted red
          it would be red on every healthy portfolio —a stop below the entry is
          the normal case— and a colour that is always on stops meaning
          anything. */}
      <Figure
        label="Si saltan los stops"
        value={signedMoney(summary.stopOutcome, symbol)}
        title="Lo que se realizaría si todas las posiciones salieran hoy por su stop, sin contar la comisión de salida"
      >
        <span className={withoutStop ? "font-medium text-warning" : "text-text-muted"}>
          {withoutStop
            ? `${withoutStop} sin stop, fuera del total`
            : "contra el precio de entrada"}
        </span>
      </Figure>
    </div>
  );
}

/**
 * One row of the open-positions table, with the thesis folded away.
 *
 * The thesis used to sit under the symbol and it was the wrong place: it is
 * four to six lines of prose in the narrowest column of the table, so a single
 * position turned a 48 px row into a 200 px one and pushed the figures —the
 * P&L, the distance to the stop— down out of the first screenful. What the
 * screen is for is comparing positions, and prose in a column cannot be
 * compared.
 *
 * Folded, the table is back to one line per position and the thesis is one
 * click away at full width. **It is `aria-expanded` on the symbol and not a
 * tooltip** because a tooltip cannot be read at leisure, cannot be selected and
 * does not exist on a touch screen — and this is a paragraph, not a note.
 *
 * A position with no thesis gets no toggle: it is plain text, so nothing
 * invites a click that would unfold nothing.
 *
 * @param props - Row props.
 * @param props.row - The position.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row, plus its detail row when unfolded.
 */
function OpenPositionTableRow({ row, symbol }: { row: PositionRow; symbol: string }) {
  const [open, setOpen] = useState(false);
  const thesis = row.thesis?.trim();

  return (
    <>
      <Row expanded={Boolean(thesis) && open}>
        <Td>
          {thesis ? (
            <LinkButton
              variant="subtle"
              className="inline-flex items-center gap-1 font-medium"
              aria-expanded={open}
              title={open ? "Ocultar la tesis" : "Ver la tesis"}
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

      {thesis && open && (
        <DetailRow columns={OPEN_COLUMNS}>
          {/* No `max-w-prose`: the thesis takes the width of the table it is
              unfolding inside. The measure of 65 characters is the typographic
              rule for a page of running text, and this is not one — it is four
              lines that are read once, right under the numbers they explain, and
              a column half the width of the screen made it look like a second
              table with the rest of the row missing. */}
          <p className="text-caption leading-snug text-text-secondary">{thesis}</p>
        </DetailRow>
      )}
    </>
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
