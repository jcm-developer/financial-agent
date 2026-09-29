import { useState } from "react";
import { Link } from "react-router";
import { ChevronRight } from "lucide-react";

import { useAnalytics, useCycles, usePositions } from "@/api/hooks";
import type { CycleRow, PositionRow, ProfileMetrics, ProfileSummary } from "@/api/types";
import { CycleStatus } from "@/components/CycleStatus";
import {
  Alert,
  Card,
  LINK_CLASSES,
  LinkButton,
  Loading,
  PageTitle,
  Stat,
} from "@/components/pieces";
import { PriceSource } from "@/components/PriceSource";
import { Section } from "@/components/Section";
import { Sparkline } from "@/components/Sparkline";
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
  time,
  percent,
} from "@/lib/format";
import { cycleStatusLabel } from "@/lib/labels";
import {
  exitRuleLabel,
  splitExitReason,
  summarizeOpen,
  type OpenSummary,
} from "@/lib/portfolio";
import { cn } from "@/lib/utils";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

/** Closed positions per page. */
const CLOSED_LIMIT = 50;

/** Columns of the open table, so the unfolded thesis spans all of them. */
const OPEN_COLUMNS = 8;

/** Same, for the closed table and its unfolded exit reason. */
const CLOSED_COLUMNS = 7;

/**
 * The experiment's summary, and since 2026-09-28 its positions too.
 *
 * **Resumen and Posiciones were two screens with the same half.** Both opened
 * on a row of portfolio cards and both carried the table of open positions —
 * the summary's with fewer columns—, so the sidebar had two entries for one
 * question and the two tables could be seen disagreeing on which columns a
 * position has. Now there is one table, the complete one, and Posiciones is a
 * redirect here.
 *
 * **The top is one card and not eight.** The eight figures had the same weight,
 * and on a young experiment half of them said «—» or repeated each other —the
 * total return and the day's P&L are the same number on the first day. What the
 * screen answers first is how the experiment is doing, so that is the large
 * figure, with the return, the day and the curve beside it; the counts drop to a
 * line underneath.
 *
 * **Two clocks, kept apart by section.** The overview is marked at the last
 * cycle's bar (`metrics`), and the positions section at the ingestor's live
 * price. Both are right, and each says which it is: the overview under the
 * capital, the positions section in its own line of totals. F9.8.2 is why this
 * is spelled out on screen and not only here.
 *
 * @return The rendered screen.
 */
export function Summary() {
  const { profile, ref, loading, error } = useActiveProfile();
  useTitle("Resumen", profile?.name);
  const [closedOffset, setClosedOffset] = useState(0);
  const open = usePositions(ref, { status: "open", limit: 200 });
  const closed = usePositions(ref, { status: "closed", limit: CLOSED_LIMIT, offset: closedOffset });
  const cycles = useCycles(ref, { limit: 5 });

  if (loading) return <Loading />;
  if (error) return <Alert>{error.message}</Alert>;
  if (!profile) return null;

  const symbol = profile.currency_symbol;

  return (
    <>
      <PageTitle aside={<RiskHeadline summary={profile.risk_summary} />}>
        {profile.name}
      </PageTitle>

      <Overview profile={profile} />

      <Section title="Posiciones abiertas" query={open}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>No hay posiciones abiertas en este experimento.</Empty>
          ) : (
            <>
              <Totals summary={summarizeOpen(page.items)} metrics={profile.metrics} symbol={symbol} />
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

      <Section title="Últimos ciclos" query={cycles}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>Todavía no ha corrido ningún ciclo.</Empty>
          ) : (
            <Table title="Últimos ciclos ejecutados">
              <TableHead>
                <Th>Inicio</Th>
                <Th>Estado</Th>
                <Th numeric>Decisiones</Th>
                <Th numeric>Órdenes</Th>
                <Th numeric>Δ capital</Th>
              </TableHead>
              <tbody>
                {page.items.map((cycle) => (
                  <CycleTableRow
                    key={cycle.id}
                    cycle={cycle}
                    profile={profile.name}
                    symbol={symbol}
                  />
                ))}
              </tbody>
            </Table>
          )
        }
      </Section>

      <Section title="Posiciones cerradas" query={closed}>
        {(page) => (
          <>
            {page.items.length === 0 ? (
              <Empty>Aún no hay posiciones cerradas.</Empty>
            ) : (
              <Table title="Posiciones cerradas">
                <TableHead>
                  <Th>Símbolo</Th>
                  <Th>Cerrada</Th>
                  <Th numeric>Cantidad</Th>
                  <Th numeric>Entrada</Th>
                  <Th numeric>Salida</Th>
                  <Th numeric>P&L</Th>
                  <Th>Regla</Th>
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
              onChange={setClosedOffset}
            />
          </>
        )}
      </Section>
    </>
  );
}

/**
 * The head of the risk summary beside the title, with the rest in its `title`.
 *
 * `risk_summary` is a whole sentence of limits, which Ajustes shows in full;
 * beside a page title it was a paragraph competing with the name. The part
 * before the colon (`Riesgo 5/10 a 10 días`) is what identifies the setting,
 * and a summary with no colon is shown whole rather than cut somewhere invented.
 *
 * @param props - Headline props.
 * @param props.summary - The profile's `risk_summary`.
 * @return The rendered headline.
 */
function RiskHeadline({ summary }: { summary: string }) {
  const cut = summary.indexOf(":");
  if (cut < 0) return <>{summary}</>;
  return <span title={summary}>{summary.slice(0, cut)}</span>;
}

/**
 * How the experiment is doing: the capital, the return, the day and the curve,
 * with the counts on a line underneath.
 *
 * Capital, return and day are the last row of `equity_snapshots`, marked at the
 * last cycle's bar. The time of that valuation stays under the capital: it reads
 * `equity_as_of` and not `last_cycle_at`, because a running cycle has started
 * but not yet written its snapshot.
 *
 * Before the first cycle there is no snapshot, and Capital shows the budget
 * rather than a dash: until something is traded, that is what the capital is.
 *
 * The curve is the same `/api/analytics` series Analítica draws, so the two
 * cannot disagree; the query is shared through the cache, and opening Analítica
 * after Resumen costs no request.
 *
 * @param props - Overview props.
 * @param props.profile - The profile, whose `metrics` already carry the figures
 *     computed, so this screen and the profile card cannot disagree.
 * @return The rendered card.
 */
function Overview({ profile }: { profile: ProfileSummary }) {
  const m = profile.metrics;
  const symbol = profile.currency_symbol;
  const analytics = useAnalytics(profile.name);
  const curve = (analytics.data?.equity_curve ?? []).map((point) => point.equity);
  const marked = m.equity_as_of;
  const valuedAt = marked ? `Valorado el ${dateTime(marked)}` : undefined;
  const hasEquity = m.equity !== null && m.equity !== undefined;
  const winRateMissing = m.win_rate_pct === null || m.win_rate_pct === undefined;

  return (
    <Card as="section" aria-label="Estado del experimento" padding="p-0" className="mb-8">
      <div className="grid gap-6 p-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] lg:items-center">
        <div>
          <p className="text-caption text-text-muted">Capital</p>
          <p className="tabular mt-1 text-h1" title={valuedAt}>
            {money(hasEquity ? m.equity : m.initial_budget, symbol)}
          </p>
          <p className="mt-1 text-caption text-text-muted">
            {hasEquity
              ? `De ${money(m.initial_budget, symbol)} inicial${marked ? ` · a precio del ciclo de las ${time(marked)}` : ""}`
              : "Presupuesto inicial"}
          </p>

          <dl className="mt-5 grid grid-cols-2 gap-4">
            <Stat
              label="Rentabilidad total"
              value={percent(m.total_return_pct, { sign: true })}
              valueClass={cn("text-h3", signClass(m.total_return_pct))}
              title={valuedAt}
            />
            <Stat
              label="P&L del día"
              value={percent(m.day_pnl_pct, { sign: true })}
              valueClass={cn("text-h3", signClass(m.day_pnl_pct))}
              title={valuedAt}
            />
          </dl>
        </div>

        <div>
          <div className="mb-2 flex items-baseline justify-between gap-3">
            <p className="text-caption text-text-muted">
              Curva de capital
            </p>
            <Link
              to={`/p/${encodeURIComponent(profile.name)}/analytics`}
              className={cn(LINK_CLASSES, "text-caption whitespace-nowrap")}
            >
              Ver analítica
            </Link>
          </div>
          {curve.length > 0 ? (
            <Sparkline
              values={curve}
              baseline={m.initial_budget}
              label={`Curva de capital: ${curve.length} marcas, la última en ${money(curve[curve.length - 1], symbol)}`}
              className="h-auto w-full"
            />
          ) : (
            <p className="flex aspect-[4/1] items-center justify-center rounded-md border border-dashed border-border text-caption text-text-muted">
              {analytics.isPending
                ? "Cargando la curva…"
                : "La curva empieza con el primer ciclo."}
            </p>
          )}
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-4 border-t border-border px-6 py-4 sm:grid-cols-3 lg:grid-cols-5">
        <Stat label="Posiciones abiertas" value={String(m.open_positions ?? 0)} />
        <Stat label="Operaciones cerradas" value={String(m.closed_trades ?? 0)} />
        <Stat
          label="Aciertos"
          value={percent(m.win_rate_pct)}
          valueClass={winRateMissing ? "text-text-muted" : undefined}
          title={winRateMissing ? "Sin operaciones cerradas todavía" : undefined}
        />
        <Stat
          label="P&L realizado"
          value={signedMoney(m.realized_pnl, symbol)}
          valueClass={signClass(m.realized_pnl)}
        />
        {m.last_cycle_at ? (
          <Stat label="Último ciclo" value={dateTime(m.last_cycle_at)}>
            {m.last_cycle_status && (
              <span
                className={
                  m.last_cycle_status === "failed" ? "font-semibold text-delta-bad" : undefined
                }
              >
                {cycleStatusLabel(m.last_cycle_status)}
              </span>
            )}
          </Stat>
        ) : (
          <Stat label="Último ciclo" value="Ninguno" valueClass="text-text-muted" />
        )}
      </dl>
    </Card>
  );
}

/**
 * The open book's totals, over its table, at the live price.
 *
 * **The set reconciles, and that is what it is for** (F4.17): capital inicial +
 * P&L latente + P&L realizado = valor de la cartera. The first and the last term
 * are in the overview above —the initial capital under the big figure, the
 * realised P&L on its line of counts— and the two that depend on the live price
 * are here, so a figure that looks wrong can still be traced instead of merely
 * doubted.
 *
 * **Two clocks, and here they compose rather than clash.** `cash` comes from the
 * broker's ledger and does not depend on any price —it only moves on a fill— so
 * pairing it with the live market value gives the portfolio's value **at the live
 * price**. That is a different number from the capital above, which is the same
 * sum marked at the last cycle's bar, and it is the fresher of the two; the line
 * says so rather than leaving two capitals side by side to be compared.
 *
 * **What is deliberately not here is a "cambio del día".** The row carries no
 * previous close, so the only daily figure available is `metrics.day_pnl_pct`,
 * which is marked at the cycle price. Half a figure on one clock and half on the
 * other is the FE.8 mistake in a different unit.
 *
 * It is a line of three and not three cards: under the overview, a second row
 * of cards of the same weight would be the eight-card top back again.
 *
 * @param props - Totals props.
 * @param props.summary - The open book's totals, from `summarizeOpen`.
 * @param props.metrics - The profile's figures, for the cash the table cannot
 *     know about.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered line of totals.
 */
function Totals({
  summary,
  metrics,
  symbol,
}: {
  summary: OpenSummary;
  metrics: ProfileMetrics;
  symbol: string;
}) {
  const { withoutPrice, withoutStop, withoutCommission } = summary;
  const cash = metrics.cash;
  // Null and not zero when either half is missing: a portfolio value with the
  // cash silently left out is the same 9985 € against 9989 € confusion in a
  // bigger unit.
  const portfolioValue =
    cash === null || cash === undefined || summary.marketValue === null
      ? null
      : Math.round((cash + summary.marketValue) * 100) / 100;

  return (
    <Card padding="px-6 py-4" className="mb-3">
      <p className="mb-3 text-caption text-text-muted">A precio en vivo</p>
      <dl className="grid gap-4 sm:grid-cols-3">
        <Stat label="Valor de la cartera" value={money(portfolioValue, symbol)}>
          <span className={withoutPrice ? "font-medium text-delta-bad" : undefined}>
            {withoutPrice
              ? `${withoutPrice} sin precio, fuera del total`
              : `${money(summary.marketValue, symbol)} en posiciones y ${money(cash, symbol)} en efectivo`}
          </span>
        </Stat>

        <Stat
          label="P&L latente"
          value={signedMoney(summary.unrealizedPnl, symbol)}
          valueClass={signClass(summary.unrealizedPnl)}
        >
          <span className={withoutCommission ? "font-medium text-warning" : undefined}>
            {withoutCommission
              ? `${withoutCommission} sin comisión conocida`
              : `${percent(summary.unrealizedPnlPct, { sign: true })} · ${money(summary.commissions, symbol)} de comisiones`}
          </span>
        </Stat>

        {/* This one is not painted by sign, and the asymmetry is on purpose: the
            other two say what is, this one says what would happen. Painted red
            it would be red on every healthy portfolio —a stop below the entry is
            the normal case— and a colour that is always on stops meaning
            anything. */}
        <Stat
          label="Si saltan los stops"
          value={signedMoney(summary.stopOutcome, symbol)}
          title="Resultado si todas las posiciones salieran ahora por su stop, sin la comisión de salida"
        >
          {withoutStop > 0 && (
            <span className="font-medium text-warning">{withoutStop} sin stop, fuera del total</span>
          )}
        </Stat>
      </dl>
    </Card>
  );
}

/**
 * One row of the open-positions table, with the thesis folded away.
 *
 * The thesis used to sit under the symbol and it was the wrong place: it is
 * four to six lines of prose in the narrowest column of the table, so a single
 * position turned a 48 px row into a 200 px one and pushed the figures —the
 * P&L, the distance to the stop— down out of the first screenful. What the
 * table is for is comparing positions, and prose in a column cannot be
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
              className="inline-flex items-center gap-1 align-top font-medium"
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
        <Td className="whitespace-nowrap">{dateTime(row.opened_at)}</Td>
        <Td numeric>{quantity(row.qty)}</Td>
        <Td numeric>{money(row.entry_price, symbol)}</Td>
        {/* The timestamp lives in the `title` because a live price carries no
            tag: the freshness still has to be reachable, and the cell is where it
            belongs. */}
        <Td numeric title={row.last_price_as_of ? dateTime(row.last_price_as_of) : undefined}>
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
 * One row of the closed-positions table, which shows exit price and rule
 * instead of the stop.
 *
 * **The reason folds away, for the same reason the thesis does** (F10.6): an
 * `llm_exit` carries the analyst's whole paragraph, so one closed position was
 * turning a 48 px row into a seven-line one and pushing the figures —the exit
 * price, the realised P&L— off the first screenful.
 *
 * The `max-w-sm` that used to sit on the cell was not holding it back either:
 * a `<table>` with the default `auto` layout ignores a max-width on a `<td>`,
 * so the column took whatever the paragraph asked for.
 *
 * What stays in the column is the rule, and it is called **Regla** and not
 * «Motivo» to match the Riesgo view, where the same identifier already has that
 * header. A rule is a label and reads down the column; the paragraph is prose
 * and does not.
 *
 * @param props - Row props.
 * @param props.row - The position.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row, plus its detail row when unfolded.
 */
function ClosedPositionTableRow({ row, symbol }: { row: PositionRow; symbol: string }) {
  const [open, setOpen] = useState(false);
  const { rule, detail } = splitExitReason(row.exit_reason);

  return (
    <>
      <Row expanded={Boolean(detail) && open}>
        <Td>
          {detail ? (
            <LinkButton
              variant="subtle"
              className="inline-flex items-center gap-1 align-top font-medium"
              aria-expanded={open}
              title={open ? "Ocultar el motivo del cierre" : "Ver el motivo del cierre"}
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
        <Td className="whitespace-nowrap">{dateTime(row.closed_at)}</Td>
        <Td numeric>{quantity(row.qty)}</Td>
        <Td numeric>{money(row.entry_price, symbol)}</Td>
        <Td numeric>{money(row.exit_price, symbol)}</Td>
        <Td numeric className={signClass(row.realized_pnl)}>
          {signedMoney(row.realized_pnl, symbol)}
        </Td>
        <Td>
          {rule ? exitRuleLabel(rule) : <span className="text-text-muted">—</span>}
        </Td>
      </Row>

      {detail && open && (
        <DetailRow columns={CLOSED_COLUMNS}>
          <p className="text-caption leading-snug text-text-secondary">{detail}</p>
        </DetailRow>
      )}
    </>
  );
}

/**
 * One row of the recent-cycles table.
 *
 * @param props - Row props.
 * @param props.cycle - The cycle.
 * @param props.profile - Profile name, needed to link to the cycles screen.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function CycleTableRow({
  cycle,
  profile,
  symbol,
}: {
  cycle: CycleRow;
  profile: string;
  symbol: string;
}) {
  return (
    <Row>
      <Td>
        <Link
          className={LINK_CLASSES}
          to={`/p/${encodeURIComponent(profile)}/cycles?cycle=${encodeURIComponent(cycle.id)}`}
        >
          {dateTime(cycle.started_at)}
        </Link>
      </Td>
      <Td>
        <CycleStatus cycle={cycle} />
      </Td>
      <Td numeric>{cycle.decisions ?? 0}</Td>
      <Td numeric>{cycle.orders ?? 0}</Td>
      <Td numeric className={signClass(cycle.equity_delta)}>
        {signedMoney(cycle.equity_delta, symbol)}
      </Td>
    </Row>
  );
}
