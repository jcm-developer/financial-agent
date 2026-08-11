import { useState, type ReactNode } from "react";

import { GroupRow } from "@/components/Table";
import type { DayGroup } from "@/lib/grouping";
import { longDate, time, sentence } from "@/lib/format";

/**
 * A page of history rows, folded into session → cycle → row.
 *
 * It exists because three screens —Órdenes, Riesgo and Decisiones under a
 * filter— render exactly this structure, and the only things that differ are the
 * row itself and the noun the counts are written with. Copying it would be the
 * `pieces.tsx` mistake in table form: the fold state, the default of which group
 * opens, and the wording of the headers would drift screen by screen, and the
 * drift is only visible if you look at two of them side by side.
 *
 * **The counts here describe the page and not the cycle**, and the headers say
 * nothing that pretends otherwise. These three tables have no per-cycle
 * endpoint to ask —`/api/orders` and `/api/risk-events` take no `cycle_id`— so
 * what the screen has is what came back; a cycle straddling a page boundary
 * would otherwise be announced with a total contradicted by the rows under it.
 * The number that means something is the one `Pagination` already gives.
 *
 * The Decisions screen browsing unfiltered does **not** use this: there the tree
 * is driven by `/api/cycles`, whose counts are the database's and whose rows are
 * fetched per cycle as they are unfolded. That is a different component because
 * it is a different guarantee, not because it looks different.
 *
 * @template T - The row type, which this component never looks inside.
 * @param props - Grouped rows props.
 * @param props.days - The page already grouped, from `groupByDayAndCycle`.
 * @param props.columns - Columns of the table, so the headers span all of them.
 * @param props.noun - Singular and plural of what is being counted, in the
 *     interface language: `["orden", "órdenes"]`.
 * @param props.openAll - Opens every group instead of only the newest session.
 *     True when a filter is on: filtering is already the act of asking to see
 *     the matches, and folding them away again would be answering a question
 *     with a closed door.
 * @param props.children - Renders one row. It must return `<tr>` elements.
 * @return One `<tbody>` per session.
 */
export function GroupedRows<T>({
  days,
  columns,
  noun,
  openAll = false,
  children,
}: {
  days: DayGroup<T>[];
  columns: number;
  noun: [singular: string, plural: string];
  openAll?: boolean;
  children: (row: T) => ReactNode;
}) {
  // The default cannot be held as state: the data that decides it —which day is
  // the newest— arrives after the first render, and an effect writing the
  // default in would fight the user's first click on a slow request. So the
  // state holds only what has been toggled, and the default is passed in at
  // every read.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const isOpen = (key: string, byDefault: boolean) => toggled[key] ?? byDefault;
  const toggle = (key: string, byDefault: boolean) =>
    setToggled((state) => ({ ...state, [key]: !(state[key] ?? byDefault) }));

  function count(rows: number) {
    return `${rows} ${rows === 1 ? noun[0] : noun[1]}`;
  }

  return (
    <>
      {days.map((day, dayIndex) => {
        // The newest session opens. A screen of nothing but folded headers makes
        // you click to find out whether the agent did anything at all.
        const dayByDefault = openAll || dayIndex === 0;
        const dayOpen = isOpen(day.key, dayByDefault);

        return (
          <tbody key={day.key}>
            <GroupRow
              columns={columns}
              level="day"
              open={dayOpen}
              onToggle={() => toggle(day.key, dayByDefault)}
              title={dayOpen ? "Plegar la jornada" : "Ver la jornada"}
            >
              <span>{sentence(longDate(day.at))}</span>
              {/* No `.tabular` on a count: it switches the whole element to
                  Fira Code, which is Verdana's rule for a column of figures read
                  downwards, not for a sentence with numbers in it. */}
              <span className="ml-auto text-caption font-normal text-text-secondary">
                {count(day.rows)} en esta página
              </span>
            </GroupRow>

            {dayOpen &&
              day.cycles.map((cycle) => {
                const cycleKey = `${day.key}/${cycle.id}`;
                const cycleOpen = isOpen(cycleKey, true);

                return (
                  <GroupedCycle
                    key={cycleKey}
                    // An order the broker wrote outside a cycle is a real case,
                    // so it gets a header saying so rather than a blank time.
                    // The note is prose and stays out of Fira Code, which is for
                    // the figures — here, the clock.
                    label={
                      cycle.id ? (
                        <span className="tabular font-medium text-foreground">
                          {time(cycle.at)}
                        </span>
                      ) : (
                        <span className="font-medium text-foreground">sin ciclo</span>
                      )
                    }
                    count={count(cycle.rows.length)}
                    open={cycleOpen}
                    onToggle={() => toggle(cycleKey, true)}
                    columns={columns}
                    title={cycleTitle(cycle.id, cycle.at, cycleOpen)}
                  >
                    {cycle.rows.map((row) => children(row))}
                  </GroupedCycle>
                );
              })}
          </tbody>
        );
      })}
    </>
  );
}

/**
 * The whole sentence a cycle header carries, since the chevron says nothing.
 *
 * @param id - The cycle's id, empty for rows that carry none.
 * @param at - Mark of the cycle's first row on this page.
 * @param open - Whether the cycle is currently unfolded.
 * @return The wording for that case.
 */
function cycleTitle(id: string, at: string, open: boolean): string {
  if (!id) return "Filas que no escribió ningún ciclo";
  return open ? "Plegar el ciclo" : `Ver el ciclo de las ${time(at)}`;
}

/**
 * One cycle's header and its rows.
 *
 * @param props - Cycle props.
 * @param props.label - The cycle's time, or the note that there is no cycle.
 * @param props.count - How many rows of it are on this page, already worded.
 * @param props.open - Whether the cycle is unfolded.
 * @param props.onToggle - Called when the header is pressed.
 * @param props.columns - Columns of the table, so the header spans all of them.
 * @param props.title - The whole sentence, since the chevron alone says nothing.
 * @param props.children - The cycle's rows.
 * @return The header row, plus the rows when unfolded.
 */
function GroupedCycle({
  label,
  count,
  open,
  onToggle,
  columns,
  title,
  children,
}: {
  label: ReactNode;
  count: string;
  open: boolean;
  onToggle: () => void;
  columns: number;
  title: string;
  children: ReactNode;
}) {
  return (
    <>
      <GroupRow
        columns={columns}
        level="cycle"
        open={open}
        onToggle={onToggle}
        title={title}
      >
        {label}
        <span>{count}</span>
      </GroupRow>

      {open && children}
    </>
  );
}
