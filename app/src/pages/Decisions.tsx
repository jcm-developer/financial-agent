import { useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { ChevronRight } from "lucide-react";

import { useCycles, useDecisions } from "@/api/hooks";
import type { DecisionRow } from "@/api/types";
import { GroupedRows } from "@/components/GroupedRows";
import { Input, LINK_CLASSES, LinkButton, Loading, Tag, PageTitle } from "@/components/pieces";
import { Select } from "@/components/Select";
import { pickView, ViewTabs } from "@/components/ViewTabs";
import { Orders } from "@/pages/Orders";
import { Risk } from "@/pages/Risk";
import { Section, ErrorAlert } from "@/components/Section";
import {
  TableHead,
  Row,
  DetailRow,
  GroupRow,
  Pagination,
  Table,
  Td,
  Th,
  Empty,
} from "@/components/Table";
import { groupByDayAndCycle, groupCyclesByDay } from "@/lib/grouping";
import { money, dateTime, time, longDate, sentence } from "@/lib/format";
import { decisionActionLabel, orderStatusLabel, ruleLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

/** Cycles per page while browsing: three European sessions of eight. */
const CYCLE_LIMIT = 24;

/** Decisions per page once a filter has flattened the screen. */
const LIMIT = 50;

/**
 * Decisions fetched when a cycle is unfolded.
 *
 * A cycle produces one decision per candidate (`screener_top_n`, 20 by default)
 * plus one per open position reviewed, so 20–45 is the real range and 200 is
 * headroom, not a page size: the point of unfolding a cycle is to see the batch
 * whole, and a second page inside a fold would be a paginator nobody expects.
 */
const CYCLE_DECISIONS = 200;

/** Columns, so the group headers and the unfolded thesis span the table. */
const COLUMNS = 6;

const EMPTY_DECISIONS = "Todavía no hay decisiones: no ha corrido ningún ciclo.";
const EMPTY_CYCLE = "Este ciclo no registró ninguna decisión.";
const EMPTY_FILTERED = "Ninguna decisión cumple estos filtros.";

/** The three views of the screen, the default first. */
const VIEWS = [
  ["decisions", "Decisiones"],
  ["orders", "Órdenes"],
  ["risk", "Riesgo"],
] as const;

/** The document title of each view, so a tab left open says which one it is. */
const VIEW_TITLES: Record<string, string> = {
  decisions: "Decisiones",
  orders: "Órdenes",
  risk: "Riesgo",
};

/**
 * Decisiones, Órdenes and Riesgo, as three views of one screen.
 *
 * **They were three screens and they tell one story**: the analyst proposes, the
 * Risk Manager sizes or blocks, the broker fills. The decisions table already
 * carried the verdict and the order in two of its columns, so three entries in
 * the sidebar were three doors into the same room. What each view keeps is its
 * own table, because the columns that matter differ —a fill has a price and a
 * quantity, a risk event has a rule— and flattening them into one would leave
 * half of every row in dashes, which is the reason Positions kept two tables.
 *
 * The old addresses, `/orders` and `/risk`, redirect here with their view.
 *
 * **The tabs close the row of filters, on the right**, and not a row of their
 * own under the title. Each view has filters —symbol, action, verdict— and the
 * tabs are the same kind of thing, a choice of what the table below shows, so
 * they share the row and its bottom edge. It also gives the table back the 60
 * px a row of its own took. That is why the hub hands the tabs to the view
 * instead of drawing them itself.
 *
 * @return The rendered screen.
 */
export function Decisions() {
  const { profile } = useActiveProfile();
  const [params] = useSearchParams();
  const view = pickView(params.get("view"), VIEWS);
  useTitle(VIEW_TITLES[view] ?? "Decisiones", profile?.name);
  const tabs = (
    <ViewTabs
      label="Vistas de las decisiones"
      views={VIEWS}
      current={view}
      className="sm:ml-auto"
    />
  );

  return (
    <>
      <PageTitle>Decisiones</PageTitle>
      {view === "orders" ? (
        <Orders tabs={tabs} />
      ) : view === "risk" ? (
        <Risk tabs={tabs} />
      ) : (
        <DecisionsView tabs={tabs} />
      )}
    </>
  );
}

/**
 * What the analyst proposed and what the Risk Manager said (F4.7).
 *
 * This is the experiment's central screen: it is where you see whether the model
 * discriminates between opportunities or hands the same conviction to
 * everything.
 *
 * **It is a tree of session → cycle → decision, and that is the shape the data
 * always had.** Eight hourly cycles of twenty to forty-five decisions make a day
 * of 160–360 rows, so the flat list of fifty this screen used to be was never a
 * session: it was an arbitrary window across two or three of them, with nothing
 * marking where one batch of the analyst's work ended and the next began.
 *
 * **The prose folds away, which reverses what this screen used to argue.** The
 * old note here said the thesis had to be in the row because text you go looking
 * for does not get read. That was written before F10.1 dropped the density to
 * 16 px body and 48 px rows, and after it the thesis, the risks and the risk
 * manager's reason turned every row into 150–200 px: six rows filled the screen
 * and the columns you compare —conviction, verdict— were the ones pushed off it.
 * Positions settled this in F10.6 and closed positions in F10.7; this screen was
 * the third and last one still doing it the old way.
 *
 * **Filtering swaps the tree's source, and that is not a mode for its own sake.**
 * A cycle row knows how many decisions it holds but not whether any of them
 * mentions SAN.MC, so a tree driven by the cycle list under a filter would offer
 * eight cycles of which seven unfold to nothing. With a filter on, the rows are
 * fetched first and the groups read off them; the headers then count the page
 * rather than the cycle, and they drop the totals instead of printing one that
 * would be wrong at every page boundary.
 *
 * @param props - View props.
 * @param props.tabs - The screen's view tabs, placed at the end of the filters.
 * @return The rendered view.
 */
function DecisionsView({ tabs }: { tabs: ReactNode }) {
  const { profile, ref } = useActiveProfile();
  const [params] = useSearchParams();
  const [offset, setOffset] = useState(0);
  // `?symbol=` seeds the filter once: it is how a citation in the chat lands
  // here (F9.39). After that the field is the user's, not the URL's.
  const [symbolFilter, setSymbolFilter] = useState(params.get("symbol") ?? "");
  const [action, setAction] = useState("");
  const [verdict, setVerdict] = useState("");

  const symbol = profile?.currency_symbol ?? "";
  const filtering = Boolean(symbolFilter || action || verdict);

  function changeFilter(apply: () => void) {
    // Changing a filter goes back to the first page: staying at the previous
    // offset gives an empty table with data behind it, and that reads as
    // "nothing matches the filter", which is not true. It matters twice over now
    // that the two halves paginate different things —cycles here, decisions
    // there— so an offset carried across would be counted in the wrong unit.
    apply();
    setOffset(0);
  }

  return (
    <>
      <div className="mb-6 flex flex-wrap items-end gap-4">
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
        {tabs}
      </div>

      {filtering ? (
        <Filtered
          profile={ref}
          symbol={symbol}
          filters={{ symbol: symbolFilter || undefined, action: action || undefined, verdict: verdict || undefined }}
          offset={offset}
          onOffset={setOffset}
        />
      ) : (
        <BySession profile={ref} symbol={symbol} offset={offset} onOffset={setOffset} />
      )}
    </>
  );
}

/**
 * Which groups are unfolded, remembered by key.
 *
 * The default cannot be stored as state, because the data that decides it —which
 * day is the newest— arrives after the first render, and an effect writing the
 * default into state would fight the user's first click on a slow request. So
 * the state holds **only what has been toggled** and the default is passed in at
 * every call: `toggled[key] ?? byDefault`.
 *
 * @return The reader and the writer of the fold state.
 */
function useFolds() {
  const [toggled, setToggled] = useState<Record<string, boolean>>({});

  return {
    isOpen: (key: string, byDefault: boolean) => toggled[key] ?? byDefault,
    toggle: (key: string, byDefault: boolean) =>
      setToggled((state) => ({ ...state, [key]: !(state[key] ?? byDefault) })),
  };
}

/** The header row both halves of the screen share. */
function DecisionsHead() {
  return (
    <TableHead>
      <Th>Símbolo</Th>
      <Th>Acción</Th>
      <Th numeric>Convicción</Th>
      <Th>Veredicto</Th>
      <Th numeric>Nocional</Th>
      <Th>Orden</Th>
    </TableHead>
  );
}

/**
 * The browsing half: sessions, the cycles inside them, and the decisions of the
 * cycle you unfold.
 *
 * **The counts in the headers come from `/api/cycles` and are therefore the
 * counts in the database**, not the counts of what is on screen. That is the
 * reason this level is not built from the decisions themselves: a header that
 * added up only what had been loaded would say "3 decisiones" over a cycle that
 * ran forty-five, and would climb as you unfolded things.
 *
 * A cycle's decisions are fetched **when it is unfolded and not before**, by
 * mounting the component that queries them. Opening the screen therefore costs
 * one request for the cycle list plus one for the newest cycle, instead of the
 * three hundred rows a session would be.
 *
 * @param props - Browsing props.
 * @param props.profile - Profile name. Undefined leaves the queries disabled.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @param props.offset - Offset of the page of cycles on screen.
 * @param props.onOffset - Called with the new offset.
 * @return The rendered tree.
 */
function BySession({
  profile,
  symbol,
  offset,
  onOffset,
}: {
  profile: string | undefined;
  symbol: string;
  offset: number;
  onOffset: (next: number) => void;
}) {
  const query = useCycles(profile, { limit: CYCLE_LIMIT, offset });
  const { isOpen, toggle } = useFolds();

  return (
    <Section query={query}>
      {(page) => {
        const days = groupCyclesByDay(page.items);

        return (
          <>
            {days.length === 0 ? (
              // Worded for what was actually checked, which since the tree is
              // driven by the cycle list is "no cycles": a cycle that ran and
              // decided nothing does reach the tree, as a fold saying so.
              <Empty>{EMPTY_DECISIONS}</Empty>
            ) : (
              <Table title="Decisiones del analista, agrupadas por jornada y por ciclo">
                <DecisionsHead />
                {days.map((day, dayIndex) => {
                  // The newest session opens, and inside it the newest cycle:
                  // what the screen answers first is what the agent just did,
                  // and a screen of nothing but folded headers makes you click
                  // to find out whether it did anything at all.
                  const dayOpen = isOpen(day.key, dayIndex === 0);

                  return (
                    <tbody key={day.key}>
                      <GroupRow
                        columns={COLUMNS}
                        level="day"
                        open={dayOpen}
                        onToggle={() => toggle(day.key, dayIndex === 0)}
                        title={
                          dayOpen
                            ? "Plegar la jornada"
                            : `Ver los ${day.cycles.length} ciclos de esta jornada`
                        }
                      >
                        <span>{sentence(longDate(day.at))}</span>
                        {/* No `.tabular` on a count: it switches the whole
                            element to Fira Code, which is Verdana's rule for a
                            column of figures read downwards, not for a sentence
                            with numbers in it. */}
                        <span className="ml-auto text-caption font-normal text-text-secondary">
                          {day.cycles.length} {day.cycles.length === 1 ? "ciclo" : "ciclos"}
                          {" · "}
                          {day.decisions} {day.decisions === 1 ? "decisión" : "decisiones"}
                          {" · "}
                          {day.approved} aprob. · {day.rejected} rech.
                        </span>
                      </GroupRow>

                      {dayOpen &&
                        day.cycles.map((cycle, cycleIndex) => {
                          const cycleOpen = isOpen(
                            cycle.id,
                            dayIndex === 0 && cycleIndex === 0,
                          );

                          return (
                            <CycleGroup
                              key={cycle.id}
                              open={cycleOpen}
                              onToggle={() =>
                                toggle(cycle.id, dayIndex === 0 && cycleIndex === 0)
                              }
                              startedAt={cycle.started_at}
                              // `?? 0` and not a default in the props: the
                              // generated types have these optional, and an
                              // undefined reaching the header prints
                              // "undefined decisiones" without failing anything.
                              decisions={cycle.decisions ?? 0}
                              approved={cycle.approved ?? 0}
                              rejected={cycle.rejected ?? 0}
                              profile={profile}
                              cycleId={cycle.id}
                              symbol={symbol}
                            />
                          );
                        })}
                    </tbody>
                  );
                })}
              </Table>
            )}
            <Pagination
              total={page.total}
              limit={page.limit}
              offset={page.offset}
              onChange={onOffset}
            />
          </>
        );
      }}
    </Section>
  );
}

/**
 * One cycle's header and, when it is unfolded, its decisions.
 *
 * @param props - Cycle group props.
 * @param props.open - Whether the cycle is unfolded.
 * @param props.onToggle - Called when the header is pressed.
 * @param props.startedAt - When the cycle started, for the header's time.
 * @param props.decisions - Decisions the cycle registered, from the cycle row.
 * @param props.approved - Of those, how many the Risk Manager approved.
 * @param props.rejected - Of those, how many it rejected.
 * @param props.profile - Profile name, for the decisions query.
 * @param props.cycleId - The cycle whose decisions to fetch.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The header row, plus the cycle's rows when unfolded.
 */
function CycleGroup({
  open,
  onToggle,
  startedAt,
  decisions,
  approved,
  rejected,
  profile,
  cycleId,
  symbol,
}: {
  open: boolean;
  onToggle: () => void;
  startedAt: string;
  decisions: number;
  approved: number;
  rejected: number;
  profile: string | undefined;
  cycleId: string;
  symbol: string;
}) {
  return (
    <>
      <GroupRow
        columns={COLUMNS}
        level="cycle"
        open={open}
        onToggle={onToggle}
        title={open ? "Plegar el ciclo" : `Ver las decisiones del ciclo de las ${time(startedAt)}`}
      >
        <span className="tabular font-medium text-foreground">{time(startedAt)}</span>
        <span>
          {decisions} {decisions === 1 ? "decisión" : "decisiones"}
          {decisions > 0 && ` · ${approved} aprob. · ${rejected} rech.`}
        </span>
      </GroupRow>

      {open && <CycleDecisions profile={profile} cycleId={cycleId} symbol={symbol} />}
    </>
  );
}

/**
 * The decisions of one cycle, fetched only once the cycle has been unfolded.
 *
 * It is a component and not a call in the parent because that is what makes the
 * fetch lazy: a folded cycle does not mount it, so it does not ask. Unfolding a
 * cycle a second time paints from TanStack's cache without a request.
 *
 * @param props - Query props.
 * @param props.profile - Profile name. Undefined leaves the query disabled.
 * @param props.cycleId - The cycle whose decisions to fetch.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The cycle's rows, or the line that says why there are none.
 */
function CycleDecisions({
  profile,
  cycleId,
  symbol,
}: {
  profile: string | undefined;
  cycleId: string;
  symbol: string;
}) {
  const query = useDecisions(profile, { cycle_id: cycleId, limit: CYCLE_DECISIONS });

  if (query.isPending) {
    return (
      <DetailRow columns={COLUMNS}>
        <Loading className="pt-3.5" />
      </DetailRow>
    );
  }

  if (query.error) {
    return (
      <DetailRow columns={COLUMNS}>
        <div className="pt-3.5">
          <ErrorAlert error={query.error} />
        </div>
      </DetailRow>
    );
  }

  const items = query.data?.items ?? [];

  if (items.length === 0) {
    return (
      <DetailRow columns={COLUMNS}>
        {/* Said explicitly so an empty fold does not read as a loading failure. */}
        <p className="pt-3.5 text-caption text-text-secondary">{EMPTY_CYCLE}</p>
      </DetailRow>
    );
  }

  return <SplitHolds items={items} symbol={symbol} />;
}

/**
 * A cycle's decisions with what it acted on first and the holds folded under.
 *
 * **In a cycle of twenty, fifteen are «mantener»**, and they were interleaved in
 * the order the analyst answered, so the three buys worth reading were somewhere
 * among them. A hold is a decision too —it is the analyst saying no— so it is
 * not hidden, only moved under its own fold, one click away and counted.
 *
 * The order inside each half is the one the API gave, which is the order the
 * cycle wrote them in; nothing is re-ranked here.
 *
 * Only the browsing tree does this. Under a filter the user has already said
 * what they want to see, and folding part of it away would second-guess them.
 *
 * @param props - Split props.
 * @param props.items - The cycle's decisions, as the API returned them.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The acted-on rows, then the fold with the holds.
 */
function SplitHolds({ items, symbol }: { items: DecisionRow[]; symbol: string }) {
  const [open, setOpen] = useState(false);
  const acted = items.filter((row) => row.action !== "hold");
  const holds = items.filter((row) => row.action === "hold");

  return (
    <>
      {acted.map((row) => (
        <DecisionTableRow key={row.id} row={row} symbol={symbol} />
      ))}
      {holds.length > 0 && (
        <GroupRow
          columns={COLUMNS}
          level="inner"
          open={open}
          onToggle={() => setOpen((value) => !value)}
          title={open ? "Plegar las decisiones de mantener" : "Ver las decisiones de mantener"}
        >
          {holds.length} {holds.length === 1 ? "decisión" : "decisiones"} de mantener
        </GroupRow>
      )}
      {open &&
        holds.map((row) => <DecisionTableRow key={row.id} row={row} symbol={symbol} />)}
    </>
  );
}

/**
 * The filtering half: the matching rows, still grouped, but read off the rows.
 *
 * It is `<GroupedRows>` and therefore the same component Órdenes and Riesgo use,
 * which is the point: those two have no per-cycle endpoint either, so all three
 * count the page rather than the cycle and none of them prints a total that
 * would be wrong at a page boundary. What `Pagination` says is the number that
 * means something under a filter.
 *
 * @param props - Filtering props.
 * @param props.profile - Profile name. Undefined leaves the query disabled.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @param props.filters - Symbol, action and verdict as they travel to the API.
 * @param props.offset - Offset of the page of decisions on screen.
 * @param props.onOffset - Called with the new offset.
 * @return The rendered table.
 */
function Filtered({
  profile,
  symbol,
  filters,
  offset,
  onOffset,
}: {
  profile: string | undefined;
  symbol: string;
  filters: { symbol?: string; action?: string; verdict?: string };
  offset: number;
  onOffset: (next: number) => void;
}) {
  const query = useDecisions(profile, { ...filters, limit: LIMIT, offset });

  return (
    <Section query={query}>
      {(page) => (
        <>
          {page.items.length === 0 ? (
            <Empty>{EMPTY_FILTERED}</Empty>
          ) : (
            <Table title="Decisiones que cumplen los filtros, agrupadas por jornada y por ciclo">
              <DecisionsHead />
              <GroupedRows
                days={groupByDayAndCycle(
                  page.items,
                  (row) => row.created_at,
                  (row) => row.cycle_id,
                )}
                columns={COLUMNS}
                noun={["decisión", "decisiones"]}
                openAll
              >
                {(row) => <DecisionTableRow key={row.id} row={row} symbol={symbol} />}
              </GroupedRows>
            </Table>
          )}
          <Pagination
            total={page.total}
            limit={page.limit}
            offset={page.offset}
            onChange={onOffset}
          />
        </>
      )}
    </Section>
  );
}

/**
 * One decision: a single line, with the analyst's prose folded under it.
 *
 * The line holds what is compared down the column —action, conviction, verdict,
 * the size it was allowed and what the order did— and the fold holds what is
 * read once: the thesis, the risks and the Risk Manager's reason. It is the same
 * split F10.6 made in Positions, and for the same measurement: the three
 * paragraphs in the row turned 48 px into 150–200, so six decisions filled the
 * screen and the columns worth comparing were the ones pushed off it.
 *
 * The time is not a column any more: the cycle header above the row already
 * carries it, and the same clock repeated on forty rows is forty repetitions of
 * one fact. The full mark stays in the symbol's `title`.
 *
 * A decision with no prose gets no toggle, so nothing invites a click that would
 * unfold nothing.
 *
 * @param props - Row props.
 * @param props.row - The decision and the risk manager's verdict on it.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row, plus its detail row when unfolded.
 */
function DecisionTableRow({ row, symbol }: { row: DecisionRow; symbol: string }) {
  const { ref: profile } = useActiveProfile();
  const [open, setOpen] = useState(false);

  const thesis = row.thesis?.trim();
  const risks = row.risks?.trim();
  const reason = row.risk_reason?.trim();
  const news = row.news ?? [];
  const hasDetail = Boolean(thesis || risks || reason || news.length);

  return (
    <>
      <Row expanded={hasDetail && open}>
        <Td title={dateTime(row.created_at)}>
          {hasDetail ? (
            <LinkButton
              variant="subtle"
              className="inline-flex items-center gap-1 align-top font-medium"
              aria-expanded={open}
              title={open ? "Ocultar la tesis" : "Ver la tesis y los riesgos"}
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
          {/* The same action means different things on an entry and on the review
              of an open position, so the review is marked. Only the review: an
              entry is what nineteen rows in twenty are, and a chip repeated on
              every row stops being read — it is the exception that needs the
              label. */}
          {row.kind !== "entry" && (
            <Tag tone="neutral" title="Revisión de una posición abierta">
              revisión
            </Tag>
          )}
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
            {decisionActionLabel(row.action, row.kind, row.suggested_weight_pct)}
          </span>
        </Td>
        <Td numeric>{row.conviction}</Td>
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
                {row.verdict === "approved" ? "Aprobada" : "Rechazada"}
              </span>
              {row.rule && (
                <p className="mt-0.5 text-caption text-text-muted">{ruleLabel(row.rule)}</p>
              )}
            </>
          ) : (
            // A hold decision does not go through the Risk Manager: there is
            // nothing to size. A dash, like the two columns beside it, with the
            // reason in the `title`: «no aplica» written on fifteen rows was the
            // loudest thing in a column meant for verdicts.
            <span
              className="text-caption text-text-muted"
              title={row.action === "hold" ? "Mantener no pasa por el Risk Manager" : undefined}
            >
              {row.action === "hold" ? "—" : "Sin veredicto"}
            </span>
          )}
        </Td>
        <Td numeric>{money(row.approved_notional, symbol)}</Td>
        <Td>
          {row.order_status ? (
            <span className="text-caption text-text-secondary">
              {orderStatusLabel(row.order_status)}
              {row.filled_avg_price !== null && row.filled_avg_price !== undefined
                ? ` a ${money(row.filled_avg_price, symbol)}`
                : ""}
            </span>
          ) : (
            <span className="text-caption text-text-muted">—</span>
          )}
        </Td>
      </Row>

      {hasDetail && open && (
        <DetailRow columns={COLUMNS}>
          {thesis && <p className="text-caption leading-snug">{thesis}</p>}
          {risks && (
            <p className="mt-1 text-caption leading-snug text-text-secondary">
              <span className="font-medium">Riesgos:</span> {risks}
            </p>
          )}
          {reason && (
            <p className="mt-1 text-caption leading-snug text-text-secondary">
              <span className="font-medium">Veredicto:</span> {reason}
            </p>
          )}
          {/* The headlines the analyst cited, as its prompt showed them. The ref
              is kept because it is how the thesis refers to them. */}
          {news.length > 0 && (
            <div className="mt-1 text-caption leading-snug text-text-secondary">
              <span className="font-medium">Noticias citadas:</span>
              <ul className="mt-0.5 flex flex-col gap-0.5">
                {news.map((item) => (
                  <li key={item.ref}>
                    <span className="font-mono text-text-muted">[{item.ref}]</span>{" "}
                    {item.url ? (
                      <a
                        className={LINK_CLASSES}
                        href={item.url}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {item.title}
                      </a>
                    ) : (
                      item.title
                    )}
                    {item.source ? ` · ${item.source}` : ""}
                    {item.published_at ? ` · ${dateTime(item.published_at)}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="mt-1 text-caption text-text-muted">
            {dateTime(row.created_at)}
            {row.reference_price !== null && row.reference_price !== undefined
              ? ` · precio de referencia ${money(row.reference_price, symbol)}`
              : ""}
            {row.horizon_days ? ` · horizonte ${row.horizon_days} d` : ""}
            {` · ${row.llm_model ?? "modelo desconocido"}`}
          </p>
          {profile && (
            <p className="mt-2 text-caption">
              <Link to={askAboutDecision(profile, row)} className={LINK_CLASSES}>
                Preguntar al modelo por esta decisión
              </Link>
            </p>
          )}
        </DetailRow>
      )}
    </>
  );
}

/**
 * The link from a decision to a new conversation about it (F9.39), with the
 * question already drafted so it only has to be sent — or rewritten.
 *
 * @param profile - Profile name.
 * @param row - The decision.
 * @return The chat's address, carrying the decision, its symbol and the draft.
 */
function askAboutDecision(profile: string, row: DecisionRow): string {
  const day = longDate(row.created_at);
  const verb =
    row.action === "buy"
      ? row.kind === "entry" ? "propusiste comprar" : "propusiste ampliar"
      : row.action === "sell" ? "propusiste vender" : "decidiste mantenerte con";
  const question = `¿Por qué ${verb} ${row.symbol} el ${day}? ¿Lo harías igual hoy?`;
  const query = new URLSearchParams({ decision: row.id, symbol: row.symbol, q: question });
  return `/p/${encodeURIComponent(profile)}/chat?${query.toString()}`;
}
