import type { SchemaTable } from "@/api/types";

/**
 * Where each table of the schema diagram goes, and where each relation's line
 * runs.
 *
 * **Pure and apart from the page** so the arrangement can be tested without a
 * browser: the page only paints what this returns.
 *
 * The layout is by **depth**, left to right: a table nobody references sits in
 * the first column, a table that references it one column to the right, and so
 * on. A foreign key therefore always points **left**, from the child to its
 * parent, which is the direction deletions cascade in: reading the diagram
 * right to left is reading what a `delete` takes with it.
 *
 * Each box shows the table's name, its row count and only its **key columns**
 * —primary key first, then foreign keys—, because those are the ones the lines
 * attach to. `agent_settings` alone has 51 columns: drawn whole, it would be a
 * box three screens tall with two lines on it. The full columns are listed
 * under the diagram.
 */

export const BOX_WIDTH = 216;
export const HEADER_HEIGHT = 30;
export const SUBHEADER_HEIGHT = 22;
export const ROW_HEIGHT = 20;
const COLUMN_GAP = 88;
const ROW_GAP = 22;
const PADDING = 12;

export interface BoxRow {
  column: string;
  kind: "pk" | "fk";
  /** Vertical centre of the row, in diagram coordinates. */
  y: number;
}

export interface Box {
  table: SchemaTable;
  x: number;
  y: number;
  height: number;
  rows: BoxRow[];
  /** True for a table with no relation in either direction. */
  isolated: boolean;
}

export interface Edge {
  /** The table holding the foreign key. */
  from: string;
  /** The table it points at. */
  to: string;
  column: string;
  onDelete: string;
  /** SVG path, from the child's left edge to the parent's right edge. */
  path: string;
}

export interface SchemaLayout {
  boxes: Box[];
  edges: Edge[];
  width: number;
  height: number;
}

/**
 * Lays the tables out by depth and routes one curve per foreign key.
 *
 * Tables with no relation at all go in a last column of their own: mixed into
 * the first one they would read as roots of something.
 *
 * @param tables - The schema's tables, as the API sends them.
 * @return Boxes, edges and the size of the drawing.
 */
export function layoutSchema(tables: SchemaTable[]): SchemaLayout {
  const byName = new Map(tables.map((t) => [t.name, t]));
  const parents = new Map<string, string[]>();
  const referenced = new Set<string>();
  for (const table of tables) {
    const ps = table.foreign_keys
      .map((fk) => fk.references_table)
      .filter((p) => p !== table.name && byName.has(p));
    parents.set(table.name, [...new Set(ps)]);
    ps.forEach((p) => referenced.add(p));
  }

  const isolated = (name: string) =>
    (parents.get(name) ?? []).length === 0 && !referenced.has(name);

  const depthCache = new Map<string, number>();
  const depth = (name: string, visiting = new Set<string>()): number => {
    const cached = depthCache.get(name);
    if (cached !== undefined) return cached;
    if (visiting.has(name)) return 0; // a cycle: break it rather than recurse forever
    visiting.add(name);
    const ps = parents.get(name) ?? [];
    const value = ps.length === 0 ? 0 : 1 + Math.max(...ps.map((p) => depth(p, visiting)));
    visiting.delete(name);
    depthCache.set(name, value);
    return value;
  };

  const connected = tables.filter((t) => !isolated(t.name));
  const lonely = tables.filter((t) => isolated(t.name));
  const maxDepth = connected.reduce((max, t) => Math.max(max, depth(t.name)), -1);

  const columns: SchemaTable[][] = Array.from({ length: maxDepth + 1 }, () => []);
  connected.forEach((t) => columns[depth(t.name)]?.push(t));
  if (lonely.length) columns.push(lonely);

  // Order within a column by the mean position of the parents already placed,
  // which keeps most lines short and uncrossed; the first column by name.
  const order = new Map<string, number>();
  columns.forEach((column, index) => {
    if (index === 0) {
      column.sort((a, b) => a.name.localeCompare(b.name));
    } else {
      const score = (t: SchemaTable) => {
        const ps = parents.get(t.name) ?? [];
        return ps.length ? ps.reduce((sum, p) => sum + (order.get(p) ?? 0), 0) / ps.length : 1e9;
      };
      column.sort((a, b) => score(a) - score(b) || a.name.localeCompare(b.name));
    }
    column.forEach((t, position) => order.set(t.name, position));
  });

  const boxes: Box[] = [];
  let height = 0;
  columns.forEach((column, index) => {
    let y = PADDING;
    const x = PADDING + index * (BOX_WIDTH + COLUMN_GAP);
    for (const table of column) {
      const rows = keyColumns(table).map((row, i) => ({
        ...row,
        y: y + HEADER_HEIGHT + SUBHEADER_HEIGHT + i * ROW_HEIGHT + ROW_HEIGHT / 2,
      }));
      const boxHeight = HEADER_HEIGHT + SUBHEADER_HEIGHT + rows.length * ROW_HEIGHT + 6;
      boxes.push({ table, x, y, height: boxHeight, rows, isolated: isolated(table.name) });
      y += boxHeight + ROW_GAP;
    }
    height = Math.max(height, y - ROW_GAP + PADDING);
  });

  const boxOf = new Map(boxes.map((b) => [b.table.name, b]));
  const edges: Edge[] = [];
  for (const box of boxes) {
    for (const fk of box.table.foreign_keys) {
      const parent = boxOf.get(fk.references_table);
      if (!parent || parent === box) continue;
      const fromRow = box.rows.find((r) => r.column === fk.column);
      const toRow = parent.rows.find((r) => r.column === fk.references_column);
      const x1 = box.x;
      const y1 = fromRow ? fromRow.y : box.y + HEADER_HEIGHT / 2;
      const x2 = parent.x + BOX_WIDTH;
      const y2 = toRow ? toRow.y : parent.y + HEADER_HEIGHT / 2;
      const bend = Math.max(40, (x1 - x2) / 2);
      edges.push({
        from: box.table.name,
        to: parent.table.name,
        column: fk.column,
        onDelete: fk.on_delete,
        path: `M ${x1} ${y1} C ${x1 - bend} ${y1}, ${x2 + bend} ${y2}, ${x2} ${y2}`,
      });
    }
  }

  const width = PADDING * 2 + columns.length * BOX_WIDTH + (columns.length - 1) * COLUMN_GAP;
  return { boxes, edges, width: Math.max(width, BOX_WIDTH), height };
}

/**
 * The columns a box shows: the primary key first, then each foreign key once.
 *
 * @param table - The table.
 * @return The rows to draw, without their position yet.
 */
export function keyColumns(table: SchemaTable): Omit<BoxRow, "y">[] {
  const rows: Omit<BoxRow, "y">[] = table.columns
    .filter((c) => c.primary_key)
    .map((c) => ({ column: c.name, kind: "pk" as const }));
  for (const fk of table.foreign_keys) {
    if (!rows.some((r) => r.column === fk.column)) rows.push({ column: fk.column, kind: "fk" });
  }
  return rows;
}

/**
 * What a table weighs in total, data and indexes, or null when unmeasured.
 *
 * @param table - The table.
 * @return Bytes, or null when the server's SQLite has no `dbstat`.
 */
export function totalBytes(table: SchemaTable): number | null {
  if (table.table_bytes === null || table.table_bytes === undefined) return null;
  return table.table_bytes + (table.index_bytes ?? 0);
}
