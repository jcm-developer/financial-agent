import { describe, expect, it } from "vitest";

import type { SchemaTable } from "@/api/types";
import { BOX_WIDTH, keyColumns, layoutSchema, totalBytes } from "@/lib/schemaLayout";

/** A table with an `id` primary key and the given foreign keys. */
function table(name: string, fks: [string, string][] = [], extra: string[] = []): SchemaTable {
  return {
    name,
    rows: 1,
    table_bytes: 4096,
    index_bytes: 1024,
    writable_by_api: false,
    columns: [
      { name: "id", type: "text", not_null: true, primary_key: true, default: null },
      ...[...fks.map(([col]) => col), ...extra].map((col) => ({
        name: col, type: "text", not_null: false, primary_key: false, default: null,
      })),
    ],
    foreign_keys: fks.map(([column, references_table]) => ({
      column, references_table, references_column: "id", on_delete: "CASCADE",
    })),
  };
}

const SCHEMA = [
  table("profiles"),
  table("portfolios", [["profile_id", "profiles"]]),
  table("cycles", [["portfolio_id", "portfolios"]]),
  table("decisions", [["cycle_id", "cycles"], ["portfolio_id", "portfolios"]]),
  table("bars_1m"),
];

describe("layoutSchema", () => {
  it("puts a child to the right of every table it references", () => {
    const { boxes } = layoutSchema(SCHEMA);
    const x = (name: string) => boxes.find((b) => b.table.name === name)!.x;

    expect(x("portfolios")).toBeGreaterThan(x("profiles"));
    expect(x("cycles")).toBeGreaterThan(x("portfolios"));
    // Two parents at different depths: it goes after the deeper one.
    expect(x("decisions")).toBeGreaterThan(x("cycles"));
  });

  it("keeps a table with no relation out of the first column", () => {
    const { boxes } = layoutSchema(SCHEMA);
    const lonely = boxes.find((b) => b.table.name === "bars_1m")!;

    expect(lonely.isolated).toBe(true);
    expect(lonely.x).toBeGreaterThan(boxes.find((b) => b.table.name === "decisions")!.x);
  });

  it("draws one line per foreign key, from the child's left edge to the parent's right edge", () => {
    const { boxes, edges } = layoutSchema(SCHEMA);
    const decisions = edges.filter((e) => e.from === "decisions");

    expect(decisions.map((e) => e.to).sort()).toEqual(["cycles", "portfolios"]);
    const edge = decisions[0]!;
    const child = boxes.find((b) => b.table.name === "decisions")!;
    const parent = boxes.find((b) => b.table.name === edge.to)!;
    expect(edge.path.startsWith(`M ${child.x} `)).toBe(true);
    expect(edge.path.endsWith(`${parent.x + BOX_WIDTH} ${parent.rows[0]!.y}`)).toBe(true);
  });

  it("survives a reference cycle instead of recursing forever", () => {
    const cyclic = [table("a", [["b_id", "b"]]), table("b", [["a_id", "a"]])];

    expect(() => layoutSchema(cyclic)).not.toThrow();
  });

  it("does not stack boxes of the same column on top of each other", () => {
    const { boxes } = layoutSchema([table("root"), table("x", [["r", "root"]]), table("y", [["r", "root"]])]);
    const [first, second] = boxes.filter((b) => b.table.name !== "root");

    expect(second!.y).toBeGreaterThanOrEqual(first!.y + first!.height);
  });
});

describe("keyColumns", () => {
  it("lists the primary key first and each foreign key once", () => {
    const t = table("decisions", [["cycle_id", "cycles"]], ["thesis"]);

    expect(keyColumns(t).map((r) => r.column)).toEqual(["id", "cycle_id"]);
  });
});

describe("totalBytes", () => {
  it("adds the indexes to the data", () => {
    expect(totalBytes(table("t"))).toBe(5120);
  });

  it("says unknown rather than zero when the server could not measure", () => {
    expect(totalBytes({ ...table("t"), table_bytes: null })).toBeNull();
  });
});
