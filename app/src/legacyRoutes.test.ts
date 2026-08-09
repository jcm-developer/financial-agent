import { describe, expect, it } from "vitest";

import {
  LEGACY_PROFILE_PATHS,
  LEGACY_TOP_PATHS,
  legacyTarget,
  translateSearch,
} from "@/legacyRoutes";

/**
 * Where a link saved before F8.8 ends up (F8.10).
 *
 * The three things checked here are the three that fail silently: a relative
 * jump that resolves over the URL segment instead of the route, a query string
 * dropped on the way, and the parameter that was renamed along with its screen.
 * All three redirect to a real page and look right; only the destination is
 * wrong.
 */

describe("legacyTarget", () => {
  it("takes the eight profile sections to their new name", () => {
    const routes: [string, string][] = [
      ["/p/europa-01/resumen", "/p/europa-01/summary"],
      ["/p/europa-01/analitica", "/p/europa-01/analytics"],
      ["/p/europa-01/posiciones", "/p/europa-01/positions"],
      ["/p/europa-01/decisiones", "/p/europa-01/decisions"],
      ["/p/europa-01/ordenes", "/p/europa-01/orders"],
      ["/p/europa-01/riesgo", "/p/europa-01/risk"],
      ["/p/europa-01/ciclos", "/p/europa-01/cycles"],
      ["/p/europa-01/ajustes", "/p/europa-01/settings"],
    ];

    for (const [old, current] of routes) {
      expect(legacyTarget(old)).toBe(current);
    }
    // The table and the assertions above cannot drift apart unnoticed.
    expect(routes).toHaveLength(LEGACY_PROFILE_PATHS.length);
  });

  it("takes the two root routes to their new name", () => {
    expect(legacyTarget("/perfiles")).toBe("/profiles");
    expect(legacyTarget("/diagnostico")).toBe("/diagnostics");
    expect(LEGACY_TOP_PATHS).toHaveLength(2);
  });

  it("lands inside the profile and not beside it", () => {
    // The failure this pins down: a relative `../cycles` with `relative="path"`
    // resolves over the URL segment and gives `/p/cycles`, which is a route that
    // exists —`:profile` would match "cycles"— so the symptom is an experiment
    // called "cycles" that does not exist, not an error.
    expect(legacyTarget("/p/europa-01/ciclos")).not.toBe("/p/cycles");
    expect(legacyTarget("/p/europa-01/ciclos")?.startsWith("/p/europa-01/")).toBe(true);
  });

  it("keeps the query string", () => {
    expect(legacyTarget("/p/europa-01/decisiones", "?symbol=SAN.MC&action=buy")).toBe(
      "/p/europa-01/decisions?symbol=SAN.MC&action=buy",
    );
  });

  it("renames the parameter that was renamed with its screen", () => {
    // Without this the link to one specific cycle redirects correctly and opens
    // the screen with no detail unfolded, which is the half of the link that
    // carried the information.
    expect(legacyTarget("/p/europa-01/ciclos", "?ciclo=abc-123")).toBe(
      "/p/europa-01/cycles?cycle=abc-123",
    );
  });

  it("does not re-encode the profile name", () => {
    // Re-encoding what already arrived encoded turns a %20 into a %2520 and the
    // profile stops being found, with the URL looking almost right.
    expect(legacyTarget("/p/euro%20pa/posiciones")).toBe("/p/euro%20pa/positions");
  });

  it("leaves alone what it does not recognise", () => {
    // A route that never existed has to keep reaching NotFound: redirecting it
    // somewhere plausible would hide the typo.
    expect(legacyTarget("/p/europa-01/summary")).toBeNull();
    expect(legacyTarget("/p/europa-01/inventada")).toBeNull();
    expect(legacyTarget("/inventada")).toBeNull();
    expect(legacyTarget("/")).toBeNull();
    // Not a section of the profile: three segments are required, in that shape.
    expect(legacyTarget("/x/europa-01/ciclos")).toBeNull();
  });
});

describe("translateSearch", () => {
  it("gives back nothing when there was nothing", () => {
    expect(translateSearch("")).toBe("");
    expect(translateSearch("?")).toBe("");
  });

  it("touches only the keys that changed", () => {
    expect(translateSearch("?offset=40&ciclo=x")).toBe("?offset=40&cycle=x");
  });

  it("keeps repeated keys", () => {
    expect(translateSearch("?ciclo=a&ciclo=b")).toBe("?cycle=a&cycle=b");
  });
});
