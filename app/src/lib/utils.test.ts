import { describe, expect, it } from "vitest";

import { cn } from "./utils";

/**
 * These exist because of a bug that shipped as far as a screenshot: the design
 * system's type scale is made of names `tailwind-merge` has never seen, and its
 * fallback for an unknown `text-*` class is to treat it as a colour. A size and
 * a colour then looked like two colours, the later one won, and the primary
 * button lost its white label — navy on navy, invisible, with the typecheck, the
 * tests and the build all green.
 *
 * So the rule this file locks down is: **a size and a colour must survive each
 * other, and two sizes must not.**
 */
describe("cn", () => {
  it("conserva el color cuando detrás va un tamaño de la escala", () => {
    // El caso exacto que dejó el botón primario sin texto.
    const classes = cn("bg-primary text-primary-foreground", "h-8 px-3 text-body-sm");

    expect(classes).toContain("text-primary-foreground");
    expect(classes).toContain("text-body-sm");
  });

  it("conserva el tamaño cuando detrás va un color", () => {
    // El orden inverso, que es el de <Th>: text-caption y luego text-text-secondary.
    const classes = cn("text-caption", "text-text-secondary");

    expect(classes).toContain("text-caption");
    expect(classes).toContain("text-text-secondary");
  });

  it("dos tamaños siguen cancelándose, que es para lo que sirve el merge", () => {
    expect(cn("text-body", "text-h1")).toBe("text-h1");
    expect(cn("text-caption", "text-body-sm")).toBe("text-body-sm");
  });

  it("dos colores siguen cancelándose", () => {
    expect(cn("text-delta-good", "text-delta-bad")).toBe("text-delta-bad");
  });

  it("cubre la escala entera, no solo los tamaños que se usan hoy", () => {
    const sizes = [
      "text-display",
      "text-h1",
      "text-h2",
      "text-h3",
      "text-h4",
      "text-body-lg",
      "text-body",
      "text-body-sm",
      "text-caption",
      "text-code",
    ];

    for (const size of sizes) {
      const classes = cn("text-foreground", size);
      expect(classes, `${size} se comió el color`).toContain("text-foreground");
    }
  });
});
