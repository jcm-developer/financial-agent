import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { keys } from "@/api/keys";
import { realAge, applyEvent, mergeCycle, mergeIngest } from "@/api/stream";
import type { CycleControl, IngestStatus } from "@/api/types";

/**
 * The splicing of the stream's events, which is the part that cannot be checked
 * by eye.
 *
 * The three cases that matter come from the real shape of the F3.5 events: the
 * `ingest` one sends a subset of the fields, the `cycle` one sends the whole
 * buffer the first time and only the new lines afterwards, and in between a
 * chunk can go missing.
 */

const HEALTH: IngestStatus = {
  healthy: true,
  message: "Al dia.",
  last_tick_at: "2026-08-08T15:30:00+00:00",
  seconds_since_last_tick: 12,
  consecutive_failures: 0,
  rate_limited_recently: false,
  avg_latency_ms: 850,
  symbols_tracked: 89,
  symbols_by_market: { eu: 89 },
  bars_stored: 45_400,
  quotes_stored: 89,
  last_backfill_at: null,
  recent: [],
};

describe("mergeIngest", () => {
  it("keeps the fields the event does not send", () => {
    // The event brings 5 of the 14 fields. Replacing instead of merging would
    // leave the health panel half-blank exactly when the ingestor changes its
    // verdict.
    const merged = mergeIngest(HEALTH, {
      healthy: false,
      message: "3 fallos seguidos.",
      last_tick_at: "2026-08-08T15:32:00+00:00",
      seconds_since_last_tick: 180,
      consecutive_failures: 3,
    });

    expect(merged?.healthy).toBe(false);
    expect(merged?.consecutive_failures).toBe(3);
    // What the event does not carry is still there.
    expect(merged?.avg_latency_ms).toBe(850);
    expect(merged?.symbols_tracked).toBe(89);
    expect(merged?.bars_stored).toBe(45_400);
  });

  it("invents nothing when there was no cache", () => {
    // Filling the other nine fields with zeros would assert things we do not
    // know: "0 bars stored" would read as an ingestor with no data.
    expect(
      mergeIngest(undefined, {
        healthy: true,
        message: "Al dia.",
        last_tick_at: null,
        seconds_since_last_tick: null,
        consecutive_failures: 0,
      }),
    ).toBeUndefined();
  });
});

describe("mergeCycle", () => {
  const base: CycleControl = { enabled: true, running: true, stage: "analizando" };

  it("the first event replaces: it carries the whole buffer", () => {
    const { state, hasGap } = mergeCycle(undefined, {
      ...base,
      lines: ["uno", "dos"],
    });

    expect(state.lines).toEqual(["uno", "dos"]);
    expect(hasGap).toBe(false);
  });

  it("increments are spliced at `from`", () => {
    const previous: CycleControl = { ...base, lines: ["uno", "dos"] };
    const { state, hasGap } = mergeCycle(previous, {
      ...base,
      lines: ["tres"],
      from: 2,
    });

    expect(state.lines).toEqual(["uno", "dos", "tres"]);
    expect(hasGap).toBe(false);
  });

  it("a `from` that points backwards rewrites instead of duplicating", () => {
    // It can happen after a reconnection: the server resends something we had.
    const previous: CycleControl = { ...base, lines: ["uno", "dos", "tres"] };
    const { state } = mergeCycle(previous, { ...base, lines: ["DOS", "TRES"], from: 1 });

    expect(state.lines).toEqual(["uno", "DOS", "TRES"]);
  });

  it("a gap is declared instead of being papered over", () => {
    // We hold 2 lines and the server sends from the 5th: three are missing.
    // Splicing regardless would leave a log that reads as continuous without
    // being so.
    const previous: CycleControl = { ...base, lines: ["uno", "dos"] };
    const { hasGap } = mergeCycle(previous, { ...base, lines: ["seis"], from: 5 });

    expect(hasGap).toBe(true);
  });
});

describe("applyEvent", () => {
  it("writes the quotes under the key useQuotes reads", () => {
    // If the key did not match, the stream would update one entry and the
    // screen would read another: the prices would not move and there would be
    // no error at all.
    const client = new QueryClient();
    const key = keys.quotes(undefined);

    applyEvent(
      client,
      "quotes",
      {
        mark: "2026-08-08T15:31:00+00:00",
        quotes: [
          {
            symbol: "SAN.MC",
            price: 4.8,
            as_of: "2026-08-08T15:30:00+00:00",
            updated_at: "2026-08-08T15:31:00+00:00",
            age_seconds: 60,
          },
        ],
      },
      key,
    );

    expect(client.getQueryData(key)).toHaveLength(1);
  });

  it("stores the arrival mark in the cache, not in the hook", () => {
    // The stream is opened once (in the Layout) and the screens are what need
    // the mark. When this lived in the hook's state, a screen asking for it
    // with `useStream({enabled:false})` always got null: its instance never saw
    // an event. Hence the cache.
    const client = new QueryClient();

    applyEvent(
      client,
      "quotes",
      { mark: "m", quotes: [] },
      keys.quotes(undefined),
      1_700_000,
    );

    expect(client.getQueryData(keys.quotesMeta())).toEqual({ receivedAt: 1_700_000 });
  });

  it("invalidates the history when the cycle finishes", async () => {
    // It is the only moment when the history changes all at once: the cycle has
    // just written positions, decisions and orders. Without this the screen
    // would keep showing the previous state until someone reloaded, and in an
    // experiment under watch that reads as "it did nothing".
    const client = new QueryClient();
    const running: CycleControl = {
      enabled: true, running: true, stage: "analizando", lines: [],
    };
    client.setQueryData(keys.cycleControl(), running);
    client.setQueryData(keys.positions("europa-01"), { items: [], total: 0 });

    applyEvent(
      client,
      "cycle",
      { ...running, running: false, stage: "inactivo", lines: [], from: 0 },
      keys.quotes(undefined),
    );

    const positions = client
      .getQueryCache()
      .getAll()
      .find((entry) => entry.queryKey[0] === "positions");
    expect(positions?.state.isInvalidated).toBe(true);
  });

  it("invalidates nothing while the cycle is still running", () => {
    // Invalidating on every log line would fire a round of requests every two
    // seconds for the twenty minutes a cycle lasts.
    const client = new QueryClient();
    const running: CycleControl = {
      enabled: true, running: true, stage: "analizando", lines: ["uno"],
    };
    client.setQueryData(keys.cycleControl(), running);
    client.setQueryData(keys.positions("europa-01"), { items: [], total: 0 });

    applyEvent(
      client,
      "cycle",
      { ...running, lines: ["dos"], from: 1 },
      keys.quotes(undefined),
    );

    const positions = client
      .getQueryCache()
      .getAll()
      .find((entry) => entry.queryKey[0] === "positions");
    expect(positions?.state.isInvalidated).toBe(false);
  });

  it("an unknown event touches nothing", () => {
    const client = new QueryClient();
    applyEvent(client, "inventado", { lo: "que sea" }, keys.quotes(undefined));
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });
});

describe("realAge", () => {
  it("adds whatever has passed since the event arrived", () => {
    // `age_seconds` is computed by the server as it reads, so it freezes in the
    // cache: showing it as-is would say "60 s ago" for half an hour.
    const receivedAt = 1_000_000;
    const age = realAge({ age_seconds: 60 }, receivedAt, receivedAt + 90_000);

    expect(age).toBe(150);
  });

  it("falls back to what the server said when there was no event", () => {
    expect(realAge({ age_seconds: 42 }, null)).toBe(42);
  });

  it("does not turn a price with no age into a zero", () => {
    // A 0 would read as "just arrived", which is the opposite of "unknown".
    expect(realAge({ age_seconds: null }, 1_000)).toBeNull();
  });
});
