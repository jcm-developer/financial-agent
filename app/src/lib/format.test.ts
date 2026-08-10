import { describe, expect, it } from "vitest";

import { money, percent, sentence, signClass, signedMoney } from "@/lib/format";

/**
 * The signs, which had a bug on screen and therefore have tests now.
 *
 * A position 17 céntimos under water on 3.950 € is −0,00442 %: the API rounds it
 * to two decimals and sends `-0.0`, and the P&L cell read **`−0,17 € +0,00%`**.
 * Two causes, and both are in here because neither is visible by reading the
 * code:
 *
 *   * `-0 >= 0` is `true` in JavaScript, so the old `value >= 0 ? "+" : "−"`
 *     called negative zero positive.
 *   * `Intl` prints `-0,00` for a negative that rounds to zero, which is the
 *     same lie pointing the other way and was live in the unsigned branch.
 *
 * The rule these lock down: **the sign, the colour and the digits describe the
 * printed number**, so a figure that prints as zero carries no sign and no
 * colour.
 */

describe("percent", () => {
  it("signs a real change", () => {
    expect(percent(1.04, { sign: true })).toBe("+1,04%");
    expect(percent(-1.04, { sign: true })).toBe("−1,04%");
  });

  it("does not sign what rounds to zero", () => {
    // The reported case: -0.00442 arrives from the API already as -0.0.
    expect(percent(-0, { sign: true })).toBe("0,00%");
    expect(percent(-0.00442, { sign: true })).toBe("0,00%");
    expect(percent(0.004, { sign: true })).toBe("0,00%");
    expect(percent(0, { sign: true })).toBe("0,00%");
  });

  it("drops the formatter's minus on an unsigned zero too", () => {
    // `Intl.NumberFormat` on its own renders this as "-0,00".
    expect(percent(-0.00442)).toBe("0,00%");
    expect(percent(-0)).toBe("0,00%");
  });

  it("keeps the minus of a real negative in the unsigned branch", () => {
    expect(percent(-1.04)).toBe("-1,04%");
  });

  it("still signs half a cent, which is where the rounding of the sign could disagree", () => {
    // `Math.round(-0.5)` is `-0`, so rounding the signed value would drop a sign
    // that the formatter is about to print as "0,01".
    expect(percent(-0.005, { sign: true })).toBe("−0,01%");
    expect(percent(0.005, { sign: true })).toBe("+0,01%");
  });

  it("renders no value as an em dash, which is not a zero", () => {
    expect(percent(null)).toBe("—");
    expect(percent(undefined, { sign: true })).toBe("—");
  });
});

describe("signedMoney", () => {
  it("signs a real amount and carries the symbol it was given", () => {
    expect(signedMoney(-11.87, "€")).toBe("−11,87 €");
    expect(signedMoney(159.73, "$")).toBe("+159,73 $");
  });

  it("does not sign what rounds to zero", () => {
    expect(signedMoney(-0, "€")).toBe("0,00 €");
    expect(signedMoney(-0.004, "€")).toBe("0,00 €");
    expect(signedMoney(0, "€")).toBe("0,00 €");
  });

  it("renders no value as an em dash", () => {
    expect(signedMoney(null, "€")).toBe("—");
  });
});

describe("money", () => {
  it("formats in es-ES with the symbol after the amount", () => {
    // Four digits go ungrouped and five do, which is Spanish and not a quirk:
    // CLDR sets `minimumGroupingDigits: 2` for `es`. Asserted because it is the
    // kind of thing that gets 'fixed' into 3.950,27 by someone reading it as a bug.
    expect(money(3950.27, "€")).toBe("3950,27 €");
    expect(money(39500, "€")).toBe("39.500,00 €");
    expect(money(null, "€")).toBe("—");
  });
});

describe("signClass", () => {
  it("colours by the sign of what is printed", () => {
    expect(signClass(11.87)).toBe("text-delta-good");
    expect(signClass(-11.87)).toBe("text-delta-bad");
  });

  it("does not paint a figure that prints as zero", () => {
    // Red on a cell reading "0,00 €" says the position is losing while the
    // number next to it says it is not.
    expect(signClass(-0.004)).toBe("text-text-secondary");
    expect(signClass(-0)).toBe("text-text-secondary");
    expect(signClass(0)).toBe("text-text-secondary");
  });

  it("tells no data from zero", () => {
    expect(signClass(null)).toBe("text-text-muted");
    expect(signClass(undefined)).toBe("text-text-muted");
  });
});

describe("sentence", () => {
  it("capitalises the first letter and leaves the rest of the words alone", () => {
    // The stage travels lowercase because the server composes it after a label
    // ("Ciclo en marcha — En marcha, lanzado por el planificador"). CSS
    // `capitalize` would give "En Marcha, Lanzado Por El Planificador", which is
    // not Spanish, so it is done here.
    expect(sentence("en marcha, lanzado por el planificador")).toBe(
      "En marcha, lanzado por el planificador",
    );
    expect(sentence("")).toBe("");
  });
});
