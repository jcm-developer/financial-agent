import type { ReactNode } from "react";

import { Button, Card } from "@/components/pieces";
import { cn } from "@/lib/utils";

/**
 * Data table, written by hand instead of pulled from shadcn/ui.
 *
 * shadcn's table is a set of wrappers over `<table>` with no Radix underneath,
 * so copying it would add one more file and no capability; and these already
 * have to carry the inherited palette and `tabular-nums` on the figure columns.
 * shadcn will be brought in when something that does need Radix comes up: the
 * confirmation dialog of F5.4 and the toasts.
 *
 * `overflow-x-auto` on the container and not on the page: a wide table has to
 * scroll inside its own slot, without dragging the rest of the screen (F4.9).
 *
 * @param props - Table props.
 * @param props.title - Screen-reader description, rendered as a `<caption>`.
 * @param props.children - The `<thead>` and `<tbody>` of the table.
 * @param props.className - Extra classes for the `<table>`.
 * @return The rendered table inside its scrolling card.
 */
export function Table({
  title,
  children,
  className,
}: {
  /** Description for screen readers. */
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card padding="p-0" className="overflow-x-auto">
      <table className={cn("w-full text-[13px]", className)}>
        <caption className="sr-only">{title}</caption>
        {children}
      </table>
    </Card>
  );
}

/**
 * Column header cell.
 *
 * @param props - Header props.
 * @param props.children - The header text.
 * @param props.numeric - Right-aligns the column, to match its figures.
 * @param props.className - Extra classes.
 * @return The rendered `<th scope="col">`.
 */
export function Th({
  children,
  numeric = false,
  className,
}: {
  children: ReactNode;
  numeric?: boolean;
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={cn(
        "px-3 py-2 font-medium whitespace-nowrap text-text-muted",
        numeric ? "text-right" : "text-left",
        className,
      )}
    >
      {children}
    </th>
  );
}

/**
 * Body cell.
 *
 * @param props - Cell props.
 * @param props.children - The cell content.
 * @param props.numeric - Right-aligns and applies tabular figures.
 * @param props.header - Renders the cell as the row's header instead.
 * @param props.className - Extra classes.
 * @param props.title - Tooltip, used to carry what the colour alone would not say.
 * @return The rendered `<td>`, or a `<th scope="row">` when it names the row.
 */
export function Td({
  children,
  numeric = false,
  header = false,
  className,
  title,
}: {
  children: ReactNode;
  numeric?: boolean;
  /**
   * The cell that names the row, as `<th scope="row">`.
   *
   * It is not decoration: in a quotes table, without the row header a screen
   * reader reads "17,42" without saying which symbol, and the symbol column is
   * precisely the one that gives the other three their meaning.
   */
  header?: boolean;
  className?: string;
  title?: string;
}) {
  const classes = cn(
    "px-3 py-1.5 align-top",
    numeric && "tabular text-right whitespace-nowrap",
    header && "text-left font-normal",
    className,
  );

  if (header) {
    return (
      <th scope="row" title={title} className={classes}>
        {children}
      </th>
    );
  }

  return (
    <td title={title} className={classes}>
      {children}
    </td>
  );
}

/**
 * The header row of a table.
 *
 * @param props - Header props.
 * @param props.children - The `Th` cells.
 * @return The rendered `<thead>` with its row.
 */
export function TableHead({ children }: { children: ReactNode }) {
  return (
    <thead>
      <tr className="border-b border-border">{children}</tr>
    </thead>
  );
}

/**
 * A body row, separated from the next by a rule.
 *
 * @param props - Row props.
 * @param props.children - The `Td` cells.
 * @return The rendered `<tr>`.
 */
export function Row({ children }: { children: ReactNode }) {
  return <tr className="border-b border-border last:border-0">{children}</tr>;
}

/**
 * Empty state. Deliberately worded case by case.
 *
 * "No hay posiciones" and "no hay decisiones" mean very different things in a
 * ten-day experiment, and a generic text forces you to go and look at the
 * database to find out which of the two it is.
 *
 * @param props - Empty-state props.
 * @param props.children - The wording, written for this table and no other.
 * @return The rendered dashed card.
 */
export function Empty({ children }: { children: ReactNode }) {
  return (
    <Card dashed padding="px-4 py-6" className="text-[13px] text-text-muted">
      {children}
    </Card>
  );
}

/**
 * Offset pagination.
 *
 * It shows "41–80 de 480" and not just the arrows: in a history that grows every
 * day, knowing how much is behind it is half the information, and it is what
 * tells you whether it is worth looking further.
 *
 * @param props - Pagination props.
 * @param props.total - Rows the query matched, not rows on screen.
 * @param props.limit - Page size.
 * @param props.offset - Offset of the page on screen.
 * @param props.onChange - Called with the new offset.
 * @return The rendered controls, or null when everything fits on one page.
 */
export function Pagination({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (next: number) => void;
}) {
  if (total <= limit) return null;

  const from = offset + 1;
  const to = Math.min(offset + limit, total);

  return (
    <div className="mt-3 flex items-center justify-between gap-3 text-[13px]">
      <span className="tabular text-text-muted">
        {from}–{to} de {total}
      </span>
      <div className="flex gap-2">
        <Button
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Anteriores
        </Button>
        <Button disabled={to >= total} onClick={() => onChange(offset + limit)}>
          Siguientes
        </Button>
      </div>
    </div>
  );
}
