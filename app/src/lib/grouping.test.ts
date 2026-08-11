import { describe, expect, it } from "vitest";

import type { CycleRow, DecisionRow, OrderRow } from "@/api/types";
import { dayKey, groupByDayAndCycle, groupCyclesByDay } from "@/lib/grouping";

/**
 * Tests of the grouping, which is the only part of the Decisions screen that can
 * be got wrong without anything looking wrong.
 *
 * A header over the correct-looking rows but with the wrong date, or a count
 * that adds up a cycle twice, produces a page that is entirely plausible. The
 * flat list it replaced had no such failure mode.
 *
 * The assertions reach into the groups with `?.` because `noUncheckedIndexedAccess`
 * is on: a missing group then fails the comparison it was standing in, which is
 * the same failure with a worse message and never a passing test.
 */

function cycle(id: string, startedAt: string, counts: Partial<CycleRow> = {}): CycleRow {
  return {
    id,
    started_at: startedAt,
    status: "ok",
    decisions: 0,
    approved: 0,
    rejected: 0,
    ...counts,
  };
}

function decision(id: string, cycleId: string, createdAt: string): DecisionRow {
  return {
    id,
    cycle_id: cycleId,
    created_at: createdAt,
    symbol: "SAN.MC",
    kind: "entry",
    action: "hold",
    conviction: 5,
  };
}

describe("dayKey", () => {
  it("agrupa por el día local y no por el de la marca UTC", () => {
    // The rows print in local time, so the group has to be the local day: with
    // Europe/Madrid in August (UTC+2) this mark is already the 12th on screen,
    // and filing it under the 11th would put a row under a header its own
    // timestamp contradicts.
    const local = new Date("2026-08-11T23:30:00Z");
    const month = `${local.getMonth() + 1}`.padStart(2, "0");
    const day = `${local.getDate()}`.padStart(2, "0");

    expect(dayKey("2026-08-11T23:30:00Z")).toBe(`${local.getFullYear()}-${month}-${day}`);
  });

  it("mantiene juntas las marcas que no se pueden interpretar", () => {
    // One group for the lot, rather than one group per row.
    expect(dayKey("2026-13-45T99:99:99Z")).toBe("2026-13-45");
    expect(dayKey("")).toBe("");
  });
});

describe("groupCyclesByDay", () => {
  it("suma los recuentos de cada jornada y conserva el orden de la API", () => {
    const days = groupCyclesByDay([
      cycle("c3", "2026-08-11T15:20:00Z", { decisions: 20, approved: 1, rejected: 3 }),
      cycle("c2", "2026-08-11T14:20:00Z", { decisions: 22, approved: 2, rejected: 1 }),
      cycle("c1", "2026-08-10T14:20:00Z", { decisions: 18, approved: 0, rejected: 4 }),
    ]);

    expect(days).toHaveLength(2);
    expect(days[0]?.cycles.map((row) => row.id)).toEqual(["c3", "c2"]);
    expect(days[0]?.decisions).toBe(42);
    expect(days[0]?.approved).toBe(3);
    expect(days[0]?.rejected).toBe(4);
    // The mark the header writes its date from is the day's first cycle.
    expect(days[0]?.at).toBe("2026-08-11T15:20:00Z");

    expect(days[1]?.cycles.map((row) => row.id)).toEqual(["c1"]);
    expect(days[1]?.decisions).toBe(18);
  });

  it("cuenta como cero un ciclo cuyos recuentos no vinieron", () => {
    // The generated types have these four optional, so `undefined` is a shape
    // the screen can really receive — and `undefined` inside a sum turns the
    // whole day into NaN, which renders as "NaN decisiones" without failing
    // anything.
    const days = groupCyclesByDay([
      { id: "c1", started_at: "2026-08-11T15:20:00Z", status: "ok" },
    ]);

    expect(days[0]?.decisions).toBe(0);
    expect(days[0]?.approved).toBe(0);
    expect(days[0]?.rejected).toBe(0);
  });

  it("devuelve la lista vacía sin grupos", () => {
    expect(groupCyclesByDay([])).toEqual([]);
  });
});

/** The three screens call it with their own timestamp column; this is theirs. */
function groupDecisions(rows: DecisionRow[]) {
  return groupByDayAndCycle(
    rows,
    (row) => row.created_at,
    (row) => row.cycle_id,
  );
}

describe("groupByDayAndCycle", () => {
  it("parte cada jornada en los ciclos de los que vinieron las filas", () => {
    const days = groupDecisions([
      decision("d4", "c2", "2026-08-11T15:20:10Z"),
      decision("d3", "c2", "2026-08-11T15:20:05Z"),
      decision("d2", "c1", "2026-08-11T14:20:05Z"),
      decision("d1", "c0", "2026-08-10T14:20:05Z"),
    ]);

    expect(days).toHaveLength(2);
    expect(days[0]?.rows).toBe(3);
    expect(days[0]?.cycles.map((group) => group.id)).toEqual(["c2", "c1"]);
    expect(days[0]?.cycles[0]?.rows.map((row) => row.id)).toEqual(["d4", "d3"]);
    // The header's time comes from the first surviving row of the cycle, not
    // from the cycle's own start, which this half of the screen never fetched.
    expect(days[0]?.cycles[0]?.at).toBe("2026-08-11T15:20:10Z");
    expect(days[1]?.rows).toBe(1);
  });

  it("no funde dos ciclos porque uno vuelva a aparecer más abajo", () => {
    // The API orders by `created_at desc`, so a cycle's rows arrive together;
    // this pins that the grouping does not depend on it, because a run of rows
    // interleaved by anything would otherwise open a second group with the same
    // id and the same header.
    const days = groupDecisions([
      decision("d1", "c1", "2026-08-11T15:20:10Z"),
      decision("d2", "c2", "2026-08-11T15:20:05Z"),
      decision("d3", "c1", "2026-08-11T15:20:01Z"),
    ]);

    expect(days[0]?.cycles.map((group) => group.id)).toEqual(["c1", "c2"]);
    expect(days[0]?.cycles[0]?.rows.map((row) => row.id)).toEqual(["d1", "d3"]);
    expect(days[0]?.rows).toBe(3);
  });

  it("junta bajo un solo grupo las filas que no traen ciclo", () => {
    // `OrderRow.cycle_id` is nullable, so an order written outside a cycle is a
    // real shape. Dropping those rows would lose them without a trace, and one
    // group per row would put a bare header over each of them.
    const order = (id: string, cycleId: string | null): OrderRow => ({
      id,
      cycle_id: cycleId,
      submitted_at: "2026-08-11T15:20:00Z",
      updated_at: "2026-08-11T15:20:00Z",
      symbol: "SAN.MC",
      side: "buy",
      qty: 10,
      order_type: "market",
      status: "filled",
    });

    const days = groupByDayAndCycle(
      [order("o1", null), order("o2", "c1"), order("o3", null)],
      (row) => row.submitted_at,
      (row) => row.cycle_id,
    );

    expect(days).toHaveLength(1);
    expect(days[0]?.cycles.map((group) => group.id)).toEqual(["", "c1"]);
    expect(days[0]?.cycles[0]?.rows.map((row) => row.id)).toEqual(["o1", "o3"]);
    expect(days[0]?.rows).toBe(3);
  });

  it("usa la columna de fecha que se le pasa y no una fija", () => {
    // Orders are stamped `submitted_at` and decisions `created_at`; reading the
    // wrong one would silently group everything under today.
    const days = groupByDayAndCycle(
      [{ when: "2026-08-11T15:20:00Z", cycle: "c1" }],
      (row) => row.when,
      (row) => row.cycle,
    );

    expect(days[0]?.key).toBe(dayKey("2026-08-11T15:20:00Z"));
  });

  it("devuelve la lista vacía sin grupos", () => {
    expect(groupDecisions([])).toEqual([]);
  });
});
