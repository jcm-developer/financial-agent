import { useIngestStatus, useMarkets, useQuotes } from "@/api/hooks";
import { realAge, useQuotesReceivedAt } from "@/api/stream";
import type { MarketInfo, QuoteRow } from "@/api/types";
import { Card, BlockTitle, PageTitle } from "@/components/pieces";
import { Section } from "@/components/Section";
import { TableHead, Row, Table, Td, Th } from "@/components/Table";
import { useTitle } from "@/layout/useTitle";
import { percent, signClass } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * State of the ingestion and of the exchanges.
 *
 * It was the check page of stretches A and B, and **it stays as a real screen**
 * instead of being deleted: the old dashboard predates the ingestor, so the
 * health of the ingestion and the age of the prices are visible nowhere today.
 * And they are exactly the two numbers to watch during the first two weeks —
 * "every minute" only holds if the datum is a minute old (F2.1c).
 *
 * It depends on no profile: it is infrastructure, not experiment.
 *
 * @return The rendered screen.
 */
export function Diagnostics() {
  useTitle("Ingesta");
  const markets = useMarkets();
  const ingest = useIngestStatus();
  const quotes = useQuotes();
  // Only the arrival mark, to correct the age. The SSE connection is opened once
  // by the Layout: asking for it here would open a second one.
  const quotesReceivedAt = useQuotesReceivedAt();

  return (
    <>
      <PageTitle>Ingesta y mercados</PageTitle>

      <Section title="Salud del ingestor" query={ingest}>
        {(data) => (
          <Card>
            <p className="text-body-sm">
              <span
                className={cn(
                  "font-semibold",
                  data.healthy ? "text-delta-good" : "text-delta-bad",
                )}
              >
                {data.healthy ? "Sano" : "Con problemas"}
              </span>
              {" — "}
              {data.message}
            </p>
            <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-body-sm sm:grid-cols-4">
              <Item label="Símbolos seguidos" value={data.symbols_tracked ?? 0} />
              <Item
                label="Barras guardadas"
                value={(data.bars_stored ?? 0).toLocaleString("es-ES")}
              />
              <Item
                label="Latencia media"
                value={
                  data.avg_latency_ms === null || data.avg_latency_ms === undefined
                    ? "n/d"
                    : `${Math.round(data.avg_latency_ms)} ms`
                }
              />
              <Item label="Fallos seguidos" value={data.consecutive_failures ?? 0} />
            </dl>
          </Card>
        )}
      </Section>

      <Section title="Cotizaciones" query={quotes}>
        {(data: QuoteRow[]) =>
          data.length === 0 ? (
            <p className="text-body-sm text-text-muted">
              Todavía no hay cotizaciones. El ingestor las escribe en horario de mercado, y
              solo de los símbolos de los perfiles activos.
            </p>
          ) : (
            <Table title="Último precio conocido de cada símbolo, con su antigüedad">
              <TableHead>
                <Th>Símbolo</Th>
                <Th numeric>Precio</Th>
                <Th numeric>Variación</Th>
                <Th numeric>Antigüedad</Th>
              </TableHead>
              <tbody>
                {data.map((row) => (
                  <QuoteTableRow
                    key={row.symbol}
                    row={row}
                    receivedAt={quotesReceivedAt}
                  />
                ))}
              </tbody>
            </Table>
          )
        }
      </Section>

      <Section title="Bolsas" query={markets}>
        {(data: MarketInfo[]) => (
          <div className="grid gap-4 sm:grid-cols-2">
            {data.map((market) => (
              <MarketCard key={market.code} market={market} />
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

/**
 * A label and its value inside a definition list.
 *
 * @param props - Item props.
 * @param props.label - Label, in the interface language.
 * @param props.value - Value, already formatted.
 * @return The rendered pair.
 */
function Item({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-text-muted">{label}</dt>
      <dd className="tabular">{value}</dd>
    </div>
  );
}

/**
 * One market's card: session, calendar, universe and liquidity floor.
 *
 * @param props - Card props.
 * @param props.market - The market, whose own currency labels its figures.
 * @return The rendered card.
 */
function MarketCard({ market }: { market: MarketInfo }) {
  return (
    <Card as="article">
      <div className="flex items-baseline justify-between gap-3">
        <BlockTitle>{market.label}</BlockTitle>
        <span
          className={
            market.is_operating
              ? "text-caption font-semibold text-delta-good"
              : "text-caption text-text-muted"
          }
        >
          {market.is_operating ? "en ventana" : "fuera de ventana"}
        </span>
      </div>
      <p className="mt-1 text-body-sm text-text-secondary">{market.status_text}</p>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-body-sm">
        <dt className="text-text-muted">Sesión</dt>
        <dd className="tabular text-right">
          {market.session_open}–{market.session_close}
        </dd>
        {/* The operating window is not the session (FE.13): in Europe the work
            runs 09:15 to 17:45 over a session of 09:00 to 17:30. */}
        <dt className="text-text-muted">Ventana</dt>
        <dd className="tabular text-right">
          {market.operating_open}–{market.operating_close}
        </dd>
        <dt className="text-text-muted">Universo</dt>
        <dd className="tabular text-right">{market.universe_size} valores</dd>
        <dt className="text-text-muted">Liquidez mínima</dt>
        <dd className="tabular text-right">
          {market.min_turnover.toLocaleString("es-ES")} {market.currency}
        </dd>
      </dl>
    </Card>
  );
}

/**
 * One row of the quotes table, showing how old the price really is.
 *
 * @param props - Row props.
 * @param props.row - The quote.
 * @param props.receivedAt - When the batch arrived, which is added to the
 *     server's age so a stale price cannot pass as fresh.
 * @return The rendered row.
 */
function QuoteTableRow({
  row,
  receivedAt,
}: {
  row: QuoteRow;
  receivedAt: number | null;
}) {
  const age = realAge(row, receivedAt);

  return (
    <Row>
      <Td header>{row.symbol}</Td>
      <Td numeric>{row.price.toFixed(2)}</Td>
      {/* The sign and the colour go through `format`, which is where the rule
          lives, and not written out here: this cell had the `change >= 0` of its
          own and therefore the same bug, printing `+0,00%` in green for a
          negative zero. Two copies of a sign are two chances to get it wrong. */}
      <Td numeric className={signClass(row.change_pct)}>
        {percent(row.change_pct, { sign: true })}
      </Td>
      {/* Warned from 5 minutes on: that is the threshold where "live" stops
          being live with one-minute bars. */}
      <Td
        numeric
        className={
          age !== null && age > 300 ? "font-medium text-warning" : "text-text-muted"
        }
      >
        {age === null ? "n/d" : `${Math.round(age)} s`}
      </Td>
    </Row>
  );
}
