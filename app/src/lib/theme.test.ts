import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { parseTheme, THEME_COLOR, THEME_STORAGE_KEY } from "@/lib/theme";

describe("parseTheme", () => {
  it("reads dark only when it says exactly dark", () => {
    expect(parseTheme("dark")).toBe("dark");
    expect(parseTheme("light")).toBe("light");
    expect(parseTheme("Dark")).toBe("light");
    expect(parseTheme(null)).toBe("light");
    expect(parseTheme(undefined)).toBe("light");
  });
});

describe("the inline script in index.html", () => {
  // It sets the theme before React loads and cannot import the module, so the
  // key and the colours are duplicated there. If they drift, the choice is saved
  // under one key and read from another, and the app forgets it on every reload.
  const html = readFileSync(new URL("../../index.html", import.meta.url), "utf-8");

  it("reads the same storage key", () => {
    expect(html).toContain(`"${THEME_STORAGE_KEY}"`);
  });

  it("paints the same browser chrome colours", () => {
    expect(html).toContain(THEME_COLOR.light);
    expect(html).toContain(THEME_COLOR.dark);
  });
});
