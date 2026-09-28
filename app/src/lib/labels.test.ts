import { describe, expect, it } from "vitest";

import { decisionActionLabel } from "./labels";

describe("decisionActionLabel", () => {
  it("names an entry's actions as they always were", () => {
    expect(decisionActionLabel("buy", "entry", 12)).toBe("Compra");
    expect(decisionActionLabel("hold", "entry", null)).toBe("Mantener");
  });

  it("reads a buy on a review as an add, not as a second position", () => {
    expect(decisionActionLabel("buy", "exit", 20)).toBe("Ampliar");
    expect(decisionActionLabel("buy", "exit", null)).toBe("Ampliar");
  });

  it("tells a partial sale from a whole close by the weight kept", () => {
    expect(decisionActionLabel("sell", "exit", 5)).toBe("Reducir");
    expect(decisionActionLabel("sell", "exit", null)).toBe("Venta");
  });
});
