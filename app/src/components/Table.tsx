import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";

import { Button, Card } from "@/components/pieces";
import { cn } from "@/lib/utils";

/**
 * Data table, written to Verdana's list spec: 48 px rows, 8×16 of cell padding,
 * a #F1F5F9 divider between rows, and #F8FAFC on hover.
 *
 * It is written by hand instead of pulled from shadcn/ui because shadcn's table
 * is a set of wrappers over `<table>` with no Radix underneath, so copying it
 * would add one more file and no capability; and these already have to carry the
 * palette and put the figure columns in Fira Code.
 *
 * `overflow-x-auto` on the container and not on the page: a wide table has to
 * scroll inside its own slot, without dragging the rest of the screen.
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
      <table className={cn("w-full text-body-sm", className)}>
        <caption className="sr-only">{title}</caption>
        {children}
      </table>
    </Card>
  );
}

/**
 * Column header cell.
 *
 * The header row is the one place uppercase and tracking are used outside a
 * chip: it is what separates the labels from the figures underneath without
 * spending a rule or a fill on it.
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
        "h-12 px-4 py-2 text-caption tracking-[0.5px] whitespace-nowrap text-text-secondary uppercase",
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
 * @param props.numeric - Right-aligns and puts the figure in Fira Code.
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
    "px-4 py-2 align-top",
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
 * A body row: 48 px tall, divided from the next by the light slate rule, and
 * tinted on hover so the eye can track across a wide table without losing it.
 *
 * @param props - Row props.
 * @param props.children - The `Td` cells.
 * @param props.expanded - Whether a `DetailRow` follows this one.
 * @return The rendered `<tr>`.
 */
export function Row({
  children,
  expanded = false,
}: {
  children: ReactNode;
  /**
   * Drops the bottom rule because a `DetailRow` comes next.
   *
   * A row and its detail are one row split in two, and the divider between them
   * would read as two: the eye would tie the text to the row underneath instead
   * of to the one it belongs to.
   */
  expanded?: boolean;
}) {
  return (
    <tr
      className={cn(
        "h-12 border-b border-surface-sunken transition-colors duration-150 last:border-0 hover:bg-background",
        expanded && "border-b-0",
      )}
    >
      {children}
    </tr>
  );
}

/**
 * The continuation of the `Row` above it: one cell spanning the whole width,
 * for what does not fit in a column.
 *
 * It exists because prose and a table pull in opposite directions. A thesis of
 * four lines inside the symbol column turns a 48 px row into a 200 px one and
 * pushes the figures —which is what the table is for— off the screen; the same
 * text underneath, folded away until asked for, costs nothing and reads better
 * at full width.
 *
 * **It is a second `<tr>` and not a taller cell** so the row above keeps its
 * height and its alignment: `align-top` over a cell that is four lines tall
 * leaves every figure floating at the top of the row.
 *
 * @param props - Detail props.
 * @param props.columns - Columns of the table, so the cell spans all of them.
 *     Passing fewer would leave the row short and break the hover band.
 * @param props.children - What the row unfolds.
 * @return The rendered `<tr>`.
 */
export function DetailRow({
  columns,
  children,
}: {
  columns: number;
  children: ReactNode;
}) {
  return (
    <tr className="border-b border-surface-sunken transition-colors duration-150 last:border-0 hover:bg-background">
      <td colSpan={columns} className="px-4 pt-0 pb-3">
        {children}
      </td>
    </tr>
  );
}

/**
 * The heading of a group of rows, and the control that folds it.
 *
 * A history that arrives in batches —eight cycles a session, twenty to
 * forty-five decisions each— read as one flat list of fifty rows with nothing
 * saying where one batch ended and the next began. This is the row that says it,
 * and it is a `<button>` filling the cell rather than a caption with a chevron
 * beside it: the whole width is the hit area, and the keyboard gets the fold for
 * free.
 *
 * **The two levels are told apart with the surface tokens that already exist,
 * never with a colour of their own**: `day` rests on the page ground and `cycle`
 * on the card's white, and each darkens one step on hover. Verdana has no token
 * for slate-200 as a surface —the value is `--color-border`, a line— and
 * borrowing it to paint a band is how a design system acquires a second palette.
 *
 * @param props - Group row props.
 * @param props.columns - Columns of the table, so the header spans all of them.
 * @param props.level - Which of the two levels this heads.
 * @param props.open - Whether the group is unfolded, announced as `aria-expanded`.
 * @param props.onToggle - Called when the header is pressed.
 * @param props.title - The whole sentence, since the chevron alone says nothing.
 * @param props.children - The heading's content, laid out in the cell.
 * @return The rendered `<tr>`.
 */
export function GroupRow({
  columns,
  level = "day",
  open,
  onToggle,
  title,
  children,
}: {
  columns: number;
  level?: "day" | "cycle";
  open: boolean;
  onToggle: () => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <tr
      className={cn(
        "border-b last:border-0",
        level === "day" ? "border-border" : "border-surface-sunken",
      )}
    >
      <td colSpan={columns} className="p-0">
        <button
          type="button"
          aria-expanded={open}
          title={title}
          onClick={onToggle}
          className={cn(
            "flex w-full items-center gap-2 px-4 text-left",
            "transition-colors duration-150 ease-calm",
            level === "day"
              ? "h-12 bg-background text-body-sm font-semibold text-foreground hover:bg-surface-sunken"
              : "h-10 pl-10 text-caption text-text-secondary hover:bg-background",
          )}
        >
          <ChevronRight
            aria-hidden
            className={cn(
              "size-3.5 shrink-0 transition-transform duration-150",
              open && "rotate-90",
            )}
          />
          {children}
        </button>
      </td>
    </tr>
  );
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
    <Card dashed padding="px-6 py-10" className="text-body-sm text-text-secondary">
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
    <div className="mt-4 flex items-center justify-between gap-4">
      <span className="tabular text-body-sm text-text-secondary">
        {from}–{to} de {total}
      </span>
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
        >
          Anteriores
        </Button>
        <Button size="sm" disabled={to >= total} onClick={() => onChange(offset + limit)}>
          Siguientes
        </Button>
      </div>
    </div>
  );
}
