import type { PositionRow } from "@/api/types";
import { Tag } from "@/components/pieces";
import { dateTime } from "@/lib/format";

/**
 * The price provenance tag (F3.2).
 *
 * It is shown whenever there is a price, because it is the difference between a
 * P&L that means something and one that mixes the close from two days ago with
 * a price from a minute ago. And when there is none, it says so: the position is
 * valued at its entry price, which means its P&L is zero for lack of data, not
 * because it did not move.
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
 * @return The tag saying where the price came from, always with the full
 *     sentence in its `title`.
 */
export function PriceSource({ row }: { row: PositionRow }) {
  if (!row.price_source) {
    return (
      <Tag tone="bad" title="Sin precio: la posición se valora a su precio de entrada">
        SIN PRECIO
      </Tag>
    );
  }

  const live = row.price_source === "live";

  return (
    <Tag
      tone={live ? "good" : "warning"}
      title={
        live
          ? `Cotización del ingestor (${dateTime(row.last_price_as_of)})`
          : `El precio que vio el analista en su último ciclo (${dateTime(row.last_price_as_of)})`
      }
    >
      {live ? "VIVO" : "CICLO"}
    </Tag>
  );
}
