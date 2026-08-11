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
 * **Commissions are in, and they come from the server** (F4.17): `unrealized_pnl`
 * arrives already net of `entry_commission`, and the commission itself travels
 * on the row so a card can say how much of the result is friction. What is still
 * missing is the **exit** leg, and it stays missing on purpose: working it out
 * here from the symbol's suffix would be `src/fees.py` reimplemented in
 * TypeScript, which is the mistake F6.8 exists to prevent. The one card affected
 * says so in its footnote.
 */

/** The open book's totals, with the holes in them counted rather than hidden. */
export interface OpenSummary {
  /** How many open positions went into this. */
  count: number;
  /** Cost of the shares held: entry price times quantity. */
  invested: number;
  /** Market value at the live price. Null when not one position has a price. */
  marketValue: number | null;
  /**
   * Unrealised P&L at the live price, **already net of the opening commission**:
   * the API subtracts it row by row. Null when nothing could be valued.
   */
  unrealizedPnl: number | null;
  /** That P&L over what the valued positions cost. Null on the same condition. */
  unrealizedPnlPct: number | null;
  /** Commission paid to open the open book, which the P&L above has taken off. */
  commissions: number;
  /**
   * What would be booked if every stop triggered right now: `(stop − entry) ×
   * qty` less the commission already paid, summed. Negative in the normal case
   * and positive when a raised stop has locked a gain in. It does **not** carry
   * the exit leg's commission, which the frontend cannot price. Null when no
   * open position carries a stop.
   */
  stopOutcome: number | null;
  /** Positions with no price, left out of the value and the P&L. */
  withoutPrice: number;
  /** Positions with no stop, left out of `stopOutcome`. */
  withoutStop: number;
  /**
   * Positions whose commission the broker's ledger does not know. Their P&L is
   * gross, so a total containing one is not comparable with one that has none.
   */
  withoutCommission: number;
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
  let commissions = 0;
  let valued = 0;
  let stopped = 0;
  let priced = 0;

  for (const row of open) {
    invested += row.entry_price * row.qty;

    const commission = row.entry_commission ?? 0;
    if (row.entry_commission !== null && row.entry_commission !== undefined) {
      commissions += row.entry_commission;
      priced += 1;
    }

    if (row.market_value !== null && row.market_value !== undefined) {
      marketValue += row.market_value;
      // Already net: `_value_position` takes the commission off before sending it.
      unrealizedPnl += row.unrealized_pnl ?? 0;
      investedValued += row.entry_price * row.qty;
      valued += 1;
    }

    if (row.stop_price !== null && row.stop_price !== undefined) {
      // Same shape as the P&L above, so the two cards are comparable: the price
      // leg less what has already been paid to be in the position.
      stopOutcome += (row.stop_price - row.entry_price) * row.qty - commission;
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
    commissions: round(commissions),
    stopOutcome: stopped ? round(stopOutcome) : null,
    withoutPrice: open.length - valued,
    withoutStop: open.length - stopped,
    withoutCommission: open.length - priced,
  };
}

/** An `exit_reason` taken apart: the rule that fired and the prose explaining it. */
export interface ExitReason {
  /** The rule, `stop_loss` or `llm_exit`, or null when the text declares none. */
  rule: string | null;
  /** The rest of the text, which is a paragraph when the analyst wrote it. */
  detail: string;
}

/**
 * Splits `[stop_loss] La accion ha perdido el soporte…` into its two halves.
 *
 * The reason travels as one string because that is what fits in the
 * `positions.exit_reason` column, and [src/cycle.py](src/cycle.py) writes it as
 * the rule in brackets followed by the analyst's paragraph. On screen they are
 * two different things: a rule is a label and reads down a column, prose is not
 * and does not.
 *
 * **Not every reason carries a rule.** A position closed by reconciliation is
 * saved as plain text with no bracket ([src/db.py](src/db.py)), so `rule` comes
 * back null and the whole string is detail. Inventing a rule for it would put a
 * word in the database's mouth.
 *
 * @param text - The stored `exit_reason`, or null when there is none.
 * @return The rule if the string declares one, and the rest as detail.
 */
export function splitExitReason(text: string | null | undefined): ExitReason {
  const value = text?.trim() ?? "";
  const match = /^\[([^\]]+)\]\s*([\s\S]*)$/.exec(value);
  if (!match) return { rule: null, detail: value };
  // The groups are not optional in the pattern, but `noUncheckedIndexedAccess`
  // cannot know that and it is right not to trust a regex to stay as written.
  return { rule: (match[1] ?? "").trim(), detail: (match[2] ?? "").trim() };
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
