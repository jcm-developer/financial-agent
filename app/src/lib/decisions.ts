import type { CycleRow, DecisionRow } from "@/api/types";

/**
 * Folding the decision history into the two levels it already has.
 *
 * The screen used to be one flat list of fifty rows with nothing saying where
 * one batch ended and the next began, and that is not how the data is shaped:
 * **a decision belongs to a cycle and a cycle belongs to a session**. With eight
 * hourly cycles and twenty to forty-five decisions each, a day is 160–360 rows,
 * so the flat list was never one day, it was an arbitrary window across two or
 * three.
 *
 * It lives here and not in the screen for the reason `portfolio.ts` does: it is
 * the only part of the screen that can be tested without mounting React, and it
 * is the part where an off-by-one would be invisible on a page that still looks
 * plausible.
 *
 * **The day is the local day**, and that is the whole subtlety of this file.
 * `created_at` is ISO in UTC and the rows are printed in local time
 * (`dateTime`, `time`), so grouping by the first ten characters of the mark
 * would file a 23:30 decision under a day the row itself says it does not
 * belong to. In August in Madrid that is two hours of every day landing under
 * the wrong header.
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
 * "23 decisiones" over a cycle nobody has unfolded yet. That is the reason this
 * level is driven by `/api/cycles` and not by the decisions themselves — a
 * header that counted only what was on screen would go up as you scrolled.
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

/** The rows of one cycle that survived the filter. */
export interface DecisionCycle {
  id: string;
  /** Mark of the cycle's first surviving row, for the header's time. */
  at: string;
  rows: DecisionRow[];
}

/** One session's worth of filtered rows, split by the cycle they came from. */
export interface DecisionDay {
  key: string;
  at: string;
  cycles: DecisionCycle[];
  rows: number;
}

/**
 * Groups a page of decisions into days and, inside each, into cycles.
 *
 * This is the other half of the screen: **with a filter on, the tree cannot be
 * driven by the cycle list**, because a cycle row cannot say whether any of its
 * decisions mentions SAN.MC, and a tree built from it would offer eight
 * unfoldable cycles of which seven are empty. So when filtering, the rows come
 * first and the groups are read off them.
 *
 * The consequence is deliberate and the screen states it: these counts describe
 * **the page**, not the cycle. A cycle straddling a page boundary is announced
 * with the rows on this side of it, which is why the headers here carry no
 * "de 23" the way the browsing tree does.
 *
 * @param rows - A page of decisions, newest first.
 * @return One group per local day, each holding the cycles in first-seen order.
 */
export function groupDecisionsByDay(rows: DecisionRow[]): DecisionDay[] {
  const days = new Map<string, DecisionDay>();
  const cycles = new Map<string, DecisionCycle>();

  for (const row of rows) {
    const key = dayKey(row.created_at);
    let day = days.get(key);
    if (!day) {
      day = { key, at: row.created_at, cycles: [], rows: 0 };
      days.set(key, day);
    }

    // Keyed by day and cycle together: the same cycle cannot span two days, but
    // keying by `cycle_id` alone would silently rely on that being true forever.
    const cycleKey = `${key}/${row.cycle_id}`;
    let cycle = cycles.get(cycleKey);
    if (!cycle) {
      cycle = { id: row.cycle_id, at: row.created_at, rows: [] };
      cycles.set(cycleKey, cycle);
      day.cycles.push(cycle);
    }

    cycle.rows.push(row);
    day.rows += 1;
  }

  return [...days.values()];
}
