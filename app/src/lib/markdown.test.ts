import { describe, expect, it } from "vitest";

import { parseInline, parseMarkdown } from "@/lib/markdown";

describe("parseInline", () => {
  it("finds bold, inline code and citations between plain text", () => {
    expect(parseInline("Mantuve **SAP** por `rsi_14` [SAP.DE 2026-09-29].")).toEqual([
      { kind: "text", text: "Mantuve " },
      { kind: "bold", text: "SAP" },
      { kind: "text", text: " por " },
      { kind: "code", text: "rsi_14" },
      { kind: "text", text: " " },
      { kind: "citation", symbol: "SAP.DE", date: "2026-09-29" },
      { kind: "text", text: "." },
    ]);
  });

  it("leaves a bracket that is not a citation as text", () => {
    expect(parseInline("Titular [N3] y [M1]")).toEqual([
      { kind: "text", text: "Titular [N3] y [M1]" },
    ]);
  });
});

describe("parseMarkdown", () => {
  it("joins the lines of a paragraph and splits on blank lines", () => {
    const blocks = parseMarkdown("Uno\ndos\n\nTres");
    expect(blocks.map((b) => b.kind)).toEqual(["paragraph", "paragraph"]);
    expect(blocks[0]).toEqual({
      kind: "paragraph",
      content: [{ kind: "text", text: "Uno dos" }],
    });
  });

  it("reads headings, bullet and numbered lists", () => {
    const blocks = parseMarkdown("## Resumen\n- a\n- b\n\n1. uno\n2) dos");
    expect(blocks).toEqual([
      { kind: "heading", level: 2, content: [{ kind: "text", text: "Resumen" }] },
      {
        kind: "list",
        ordered: false,
        items: [[{ kind: "text", text: "a" }], [{ kind: "text", text: "b" }]],
      },
      {
        kind: "list",
        ordered: true,
        items: [[{ kind: "text", text: "uno" }], [{ kind: "text", text: "dos" }]],
      },
    ]);
  });

  it("reads a table only when the second line is a separator", () => {
    const [table] = parseMarkdown("| Valor | Acción |\n|---|:---:|\n| SAP.DE | hold |");
    expect(table).toEqual({
      kind: "table",
      header: [[{ kind: "text", text: "Valor" }], [{ kind: "text", text: "Acción" }]],
      rows: [[[{ kind: "text", text: "SAP.DE" }], [{ kind: "text", text: "hold" }]]],
    });
    expect(parseMarkdown("| no es tabla |")[0]?.kind).toBe("paragraph");
  });

  it("keeps a code fence verbatim, markup included", () => {
    expect(parseMarkdown("```\n**no** negrita\n```")).toEqual([
      { kind: "code", text: "**no** negrita" },
    ]);
  });

  it("folds an indented continuation into its list item", () => {
    const [list] = parseMarkdown("- primero\n  sigue\n- segundo");
    expect(list).toEqual({
      kind: "list",
      ordered: false,
      items: [
        [{ kind: "text", text: "primero" }, { kind: "text", text: " sigue" }],
        [{ kind: "text", text: "segundo" }],
      ],
    });
  });
});
