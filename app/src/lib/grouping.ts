import type { CycleRow } from "@/api/types";

/**
 * Folding a history table into the two levels the data already has.
 *
 * Decisions, orders and risk events are all written by a cycle, and every cycle
 * belongs to a session, so all three screens were flat lists of fifty rows with
 * nothing saying where one batch of the agent's work ended and the next began.
 * With eight hourly cycles a session, that list was never a day: it was an
 * arbitrary window across two or three of them.
 *
 * It lives here and not in the screens for the reason `portfolio.ts` does: it is
 * the only part of them that can be tested without mounting React, and it is the
 * part where an off-by-one is invisible — a header with the wrong date over the
 * right rows produces a page that is entirely plausible.
 *
 * **The day is the local day**, and that is the whole subtlety of this file.
 * The marks are ISO in UTC and the rows are printed in local time (`dateTime`,
 * `time`), so grouping by the first ten characters of the mark would file a
 * 23:30 row under a day the row itself says it does not belong to. In August in
 * Madrid that is two hours of every day landing under the previous day's header.
 */

/**
 * The local calendar day of an ISO mark, as `YYYY-MM-DD`.
 *
 * Built from the date's own parts rather than formatted, because every locale
 * that prints a day also reorders it, and this value is a key: it is compared,
 * never read.
 *
 * @param iso - ISO-8601 UTC timestamp as returned by the API.
 * @return The local day. An unparsable mark falls back to its first ten
 *     characters, which keeps the rows of that mark together instead of
 *     scattering one group per row.
 */
export function dayKey(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** One session's cycles, with the day's totals already added up. */
export interface CycleDay {
  /** Local `YYYY-MM-DD`. It is what the fold state is remembered by. */
  key: string;
  /** Mark of the day's first cycle, for the header to write the date from. */
  at: string;
  cycles: CycleRow[];
  decisions: number;
  approved: number;
  rejected: number;
}

/**
 * Groups a page of cycles into the days they ran on.
 *
 * The counts come from the cycle rows and are therefore **the counts in the
 * database**, not the counts of what the screen has loaded: the header can say
 * "30 decisiones" over a cycle nobody has unfolded yet. That is the reason the
 * Decisions tree is driven by `/api/cycles` — a header that counted only what
 * was on screen would go up as you unfolded things.
 *
 * Input order is preserved, so the API's `started_at desc` survives into the
 * groups and into the cycles inside each of them.
 *
 * @param cycles - A page of cycles, newest first.
 * @return One group per local day, in the order the days first appear.
 */
export function groupCyclesByDay(cycles: CycleRow[]): CycleDay[] {
  const days = new Map<string, CycleDay>();

  for (const cycle of cycles) {
    const key = dayKey(cycle.started_at);
    let day = days.get(key);
    if (!day) {
      day = {
        key,
        at: cycle.started_at,
        cycles: [],
        decisions: 0,
        approved: 0,
        rejected: 0,
      };
      days.set(key, day);
    }
    day.cycles.push(cycle);
    day.decisions += cycle.decisions ?? 0;
    day.approved += cycle.approved ?? 0;
    day.rejected += cycle.rejected ?? 0;
  }

  return [...days.values()];
}

/** The rows of one cycle that landed on the page. */
export interface CycleGroup<T> {
  /** The cycle's id, or the empty string for rows that carry none. */
  id: string;
  /** Mark of the cycle's first row on this page, for the header's time. */
  at: string;
  rows: T[];
}

/** One session's worth of rows, split by the cycle that wrote them. */
export interface DayGroup<T> {
  key: string;
  at: string;
  cycles: CycleGroup<T>[];
  rows: number;
}

/**
 * Groups a page of rows into days and, inside each, into cycles.
 *
 * This is the shape used when the tree has to be read **off the rows**, which is
 * every case except the Decisions screen browsing unfiltered: `/api/orders` and
 * `/api/risk-events` take no `cycle_id`, so there is no per-cycle query to be
 * lazy with, and under a filter a cycle row cannot say whether any of its rows
 * survived the filter anyway.
 *
 * The consequence is deliberate and the screens state it: these counts describe
 * **the page**, not the cycle. A cycle straddling a page boundary is announced
 * with the rows on this side of it, which is why the headers built from this
 * carry no "de 30" the way the Decisions tree does.
 *
 * @template T - The row type, which this function never looks inside.
 * @param rows - A page of rows, newest first.
 * @param at - Reads the row's timestamp. The column differs per table
 *     (`created_at`, `submitted_at`), so it is asked for rather than guessed.
 * @param cycleOf - Reads the row's cycle. Null and undefined are grouped
 *     together under the empty string: an order the broker wrote outside a cycle
 *     is a real case, and dropping those rows would lose them silently.
 * @return One group per local day, each holding its cycles in first-seen order.
 */
export function groupByDayAndCycle<T>(
  rows: T[],
  at: (row: T) => string,
  cycleOf: (row: T) => string | null | undefined,
): DayGroup<T>[] {
  const days = new Map<string, DayGroup<T>>();
  const cycles = new Map<string, CycleGroup<T>>();

  for (const row of rows) {
    const mark = at(row);
    const key = dayKey(mark);
    let day = days.get(key);
    if (!day) {
      day = { key, at: mark, cycles: [], rows: 0 };
      days.set(key, day);
    }

    // Keyed by day and cycle together: the same cycle cannot span two days, but
    // keying by the cycle alone would silently rely on that being true forever.
    const id = cycleOf(row) ?? "";
    const cycleKey = `${key}/${id}`;
    let cycle = cycles.get(cycleKey);
    if (!cycle) {
      cycle = { id, at: mark, rows: [] };
      cycles.set(cycleKey, cycle);
      day.cycles.push(cycle);
    }

    cycle.rows.push(row);
    day.rows += 1;
  }

  return [...days.values()];
}
