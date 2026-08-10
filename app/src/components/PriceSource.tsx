import type { PositionRow } from "@/api/types";
import { Tag } from "@/components/pieces";
import { dateTime } from "@/lib/format";

/**
 * The price provenance tag (F3.2), shown **only when the price is not the normal
 * one** (F4.18).
 *
 * It used to label all three cases, `VIVO` included, and that was the mistake:
 * with the ingestor healthy `VIVO` appears on every row of every table, so it
 * stopped being information and became a green column repeated down the screen.
 * An always-on label is read as decoration, and then the two labels that do carry
 * a warning get read as decoration too.
 *
 * **The other two stay, and they are the reason this component still exists.**
 * `CICLO` says the row is valued with the price the analyst saw on its last
 * cycle, which can be from the day before yesterday; `SIN PRECIO` says the
 * position is valued at its entry, so its P&L is zero for lack of data and not
 * because it did not move. Those are exactly the cases where the figure next to
 * the tag does not mean what it appears to mean.
 *
 * **The absence of a tag is therefore the claim "this price is live"**, which is
 * why the freshness has to keep travelling somewhere: the exact timestamp stays
 * in the cell's `title`, and the Ingesta screen carries the ingestor's health.
 *
 * It lives here and not inside a screen because open positions appear on two
 * —the summary and the positions screen— and they were diverging: the summary
 * one kept quiet about the no-price case and said "a few minutes ago" where the
 * other gave the exact time. With two copies, the one read more often is the one
 * that informs worse.
 *
 * @param props - Provenance props.
 * @param props.row - Position row, of which the price source and its timestamp
 *     are read.
 * @return The tag when the price is stale or missing, and nothing when it is
 *     live.
 */
export function PriceSource({ row }: { row: PositionRow }) {
  if (!row.price_source) {
    return (
      <Tag tone="bad" title="Sin precio: la posición se valora a su precio de entrada">
        SIN PRECIO
      </Tag>
    );
  }

  if (row.price_source === "live") return null;

  return (
    <Tag
      tone="warning"
      title={`El precio que vio el analista en su último ciclo (${dateTime(row.last_price_as_of)})`}
    >
      CICLO
    </Tag>
  );
}
