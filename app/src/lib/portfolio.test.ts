import { describe, expect, it } from "vitest";

import type { PositionRow } from "@/api/types";
import { summarizeOpen } from "@/lib/portfolio";

/**
 * The totals of the open book, which are checked here because they are exactly
 * the kind of arithmetic nobody verifies by eye once it is on screen.
 *
 * The cases that matter are the holes: a position Yahoo stopped pricing and a
 * position with no stop. Added in as zero, both produce a plausible number that
 * is wrong —a portfolio worth less than it is, a book that risks nothing— and
 * neither shows up as an error anywhere.
 */

/** An open position; each test overrides only the fields it is about. */
function position(fields: Partial<PositionRow>): PositionRow {
  return {
    id: "p1",
    symbol: "ALV.DE",
    status: "open",
    qty: 9,
    entry_price: 100,
    opened_at: "2026-08-10T08:25:00+00:00",
    market_value: 1080,
    unrealized_pnl: 180,
    ...fields,
  };
}

describe("summarizeOpen", () => {
  it("adds up cost, value and P&L of the open book", () => {
    const summary = summarizeOpen([
      position({ id: "a", qty: 10, entry_price: 100, market_value: 1080, unrealized_pnl: 80 }),
      position({ id: "b", qty: 5, entry_price: 200, market_value: 950, unrealized_pnl: -50 }),
    ]);

    expect(summary.count).toBe(2);
    expect(summary.invested).toBe(2000);
    expect(summary.marketValue).toBe(2030);
    expect(summary.unrealizedPnl).toBe(30);
    expect(summary.unrealizedPnlPct).toBe(1.5);
  });

  it("ignores the closed ones, so an unfiltered page is safe to pass", () => {
    const summary = summarizeOpen([
      position({ id: "a" }),
      position({ id: "b", status: "closed", realized_pnl: 500 }),
    ]);

    expect(summary.count).toBe(1);
    expect(summary.invested).toBe(900);
  });

  it("leaves a position with no price out instead of valuing it at zero", () => {
    // The trap: `market_value ?? 0` gives a market value of 1080 against 2000
    // invested, which reads as a 46 % loss that never happened.
    const summary = summarizeOpen([
      position({ id: "a", qty: 10, entry_price: 100, market_value: 1080, unrealized_pnl: 80 }),
      position({
        id: "b",
        qty: 10,
        entry_price: 100,
        market_value: null,
        unrealized_pnl: null,
        last_price: null,
      }),
    ]);

    expect(summary.count).toBe(2);
    // The cost is known for both: it does not depend on there being a price.
    expect(summary.invested).toBe(2000);
    expect(summary.marketValue).toBe(1080);
    expect(summary.unrealizedPnl).toBe(80);
    // 80 over the 1000 that *was* valued, not over the 2000 invested.
    expect(summary.unrealizedPnlPct).toBe(8);
    expect(summary.withoutPrice).toBe(1);
  });

  it("reports null rather than zero when nothing could be valued", () => {
    const summary = summarizeOpen([
      position({ market_value: null, unrealized_pnl: null }),
    ]);

    expect(summary.marketValue).toBeNull();
    expect(summary.unrealizedPnl).toBeNull();
    expect(summary.unrealizedPnlPct).toBeNull();
    expect(summary.withoutPrice).toBe(1);
  });

  it("books what the stops would give, against the entry price", () => {
    const summary = summarizeOpen([
      position({ id: "a", qty: 10, entry_price: 100, stop_price: 95 }),
      // A raised stop: this one would book a gain, and the sum has to net it.
      position({ id: "b", qty: 10, entry_price: 100, stop_price: 110 }),
    ]);

    expect(summary.stopOutcome).toBe(50);
    expect(summary.withoutStop).toBe(0);
  });

  it("counts a position with no stop instead of treating it as a stop at zero", () => {
    const summary = summarizeOpen([
      position({ id: "a", qty: 10, entry_price: 100, stop_price: 95 }),
      position({ id: "b", qty: 10, entry_price: 100, stop_price: null }),
    ]);

    expect(summary.stopOutcome).toBe(-50);
    expect(summary.withoutStop).toBe(1);
  });

  it("adds up the commissions and takes them off what the stops would give", () => {
    // `unrealized_pnl` arrives net from the API; the stop figure is netted here,
    // so the two cards mean the same thing and can be read one against the other.
    const summary = summarizeOpen([
      position({
        id: "a",
        qty: 10,
        entry_price: 100,
        stop_price: 95,
        entry_commission: 3,
        market_value: 1080,
        unrealized_pnl: 77,
      }),
    ]);

    expect(summary.commissions).toBe(3);
    expect(summary.unrealizedPnl).toBe(77);
    expect(summary.stopOutcome).toBe(-53);
    expect(summary.withoutCommission).toBe(0);
  });

  it("counts a position whose commission the ledger does not know", () => {
    // Its P&L came gross, so a total holding it is not comparable with one that
    // does not. Netting it by zero would claim the trade was free.
    const summary = summarizeOpen([
      position({ id: "a", entry_commission: 3, stop_price: 95 }),
      position({ id: "b", entry_commission: null, stop_price: 95 }),
    ]);

    expect(summary.commissions).toBe(3);
    expect(summary.withoutCommission).toBe(1);
  });

  it("gives an empty book zeros for the cost and nulls for the rest", () => {
    const summary = summarizeOpen([]);

    expect(summary.count).toBe(0);
    expect(summary.invested).toBe(0);
    expect(summary.marketValue).toBeNull();
    expect(summary.stopOutcome).toBeNull();
  });

  it("rounds to the cent, so the card and the sign agree", () => {
    // 3 × 0,1 is 0,30000000000000004 in binary floating point.
    const summary = summarizeOpen([
      position({ id: "a", qty: 3, entry_price: 0.1, market_value: 0.3, unrealized_pnl: 0 }),
    ]);

    expect(summary.invested).toBe(0.3);
  });
});
