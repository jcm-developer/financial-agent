import type { PositionRow } from "@/api/types";

/**
 * What the open book adds up to, computed from the rows on screen.
 *
 * **It is computed here and not read from `metrics`, and that is the whole
 * point.** `/api/profiles` also carries figures for the portfolio, but they are
 * valued at a different price: `equity` comes from `equity_snapshots`, one row
 * per cycle, marked at the price of the bar the analyst decided on. The table on
 * the positions screen is valued at the ingestor's minute price. Both are right
 * and they are meant to differ —it is the two clocks of EXPERIMENT.md— but a
 * card sitting on top of a table has to agree **with that table**: a total that
 * did not match the column under it would read as an arithmetic bug, and the
 * user would be left checking a sum that was never wrong.
 *
 * So the rule for this screen is: everything here is the live price, the same
 * one every row shows. The cycle-priced figures live on Resumen.
 *
 * **Nothing here nets out commissions**, because the row does not carry them:
 * `entry_commission` stays in `sim_positions` and never reaches the API. The
 * invested figure is therefore the cost of the shares and not what left the
 * account, and the stop figure is what the exits would book before the exit leg
 * is charged. Both are noted on the cards themselves; guessing the tariff here
 * from the symbol's suffix would be `src/fees.py` reimplemented in TypeScript,
 * which is the mistake F6.8 exists to prevent.
 */

/** The open book's totals, with the holes in them counted rather than hidden. */
export interface OpenSummary {
  /** How many open positions went into this. */
  count: number;
  /** Cost of the shares held: entry price times quantity. */
  invested: number;
  /** Market value at the live price. Null when not one position has a price. */
  marketValue: number | null;
  /** Unrealised P&L at the live price. Null when nothing could be valued. */
  unrealizedPnl: number | null;
  /** That P&L over what the valued positions cost. Null on the same condition. */
  unrealizedPnlPct: number | null;
  /**
   * What would be booked if every stop triggered right now: `(stop − entry) ×
   * qty`, summed. Negative in the normal case and positive when a raised stop
   * has locked a gain in. Null when no open position carries a stop.
   */
  stopOutcome: number | null;
  /** Positions with no price, left out of the value and the P&L. */
  withoutPrice: number;
  /** Positions with no stop, left out of `stopOutcome`. */
  withoutStop: number;
}

/**
 * Adds up the open positions the screen is showing.
 *
 * **A position with no price is counted, not treated as zero.** Yahoo stops
 * serving a symbol from time to time, and a missing price added in as 0 would
 * quietly shrink the portfolio's value and inflate the loss — a wrong number
 * that looks like a right one. Here those positions stay out of the totals and
 * come back as `withoutPrice`, so the card can say the total is partial. The
 * same goes for a position with no stop, which must not count as a stop at zero.
 *
 * @param rows - The open positions, exactly as the table received them. Closed
 *     ones are ignored, so passing an unfiltered page is safe.
 * @return The totals, with null where there was nothing to total.
 */
export function summarizeOpen(rows: readonly PositionRow[]): OpenSummary {
  const open = rows.filter((row) => row.status === "open");

  let invested = 0;
  let marketValue = 0;
  let unrealizedPnl = 0;
  // The base of the percentage is the cost of the *valued* positions, not of all
  // of them: dividing a P&L that covers three positions by the cost of five
  // understates it, and it is the kind of error nobody catches by eye.
  let investedValued = 0;
  let stopOutcome = 0;
  let valued = 0;
  let stopped = 0;

  for (const row of open) {
    invested += row.entry_price * row.qty;

    if (row.market_value !== null && row.market_value !== undefined) {
      marketValue += row.market_value;
      unrealizedPnl += row.unrealized_pnl ?? 0;
      investedValued += row.entry_price * row.qty;
      valued += 1;
    }

    if (row.stop_price !== null && row.stop_price !== undefined) {
      stopOutcome += (row.stop_price - row.entry_price) * row.qty;
      stopped += 1;
    }
  }

  return {
    count: open.length,
    invested: round(invested),
    marketValue: valued ? round(marketValue) : null,
    unrealizedPnl: valued ? round(unrealizedPnl) : null,
    unrealizedPnlPct:
      valued && investedValued ? round((unrealizedPnl / investedValued) * 100) : null,
    stopOutcome: stopped ? round(stopOutcome) : null,
    withoutPrice: open.length - valued,
    withoutStop: open.length - stopped,
  };
}

/**
 * Two decimals, which is where the API rounds too.
 *
 * Without it a sum of floats shows up as `159,73000000000002` the moment a
 * `toFixed` is not in the way, and `money()` would hide it while `signClass()`
 * would still see a number that is not the one displayed.
 *
 * @param value - The accumulated amount.
 * @return The same amount to the cent.
 */
function round(value: number): number {
  return Math.round(value * 100) / 100;
}
