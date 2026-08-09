/**
 * TanStack Query keys, all in one place.
 *
 * They are centralised because the SSE hook writes into the cache by key
 * (`setQueryData`): with keys written by hand in every component, a
 * `["quotes"]` against a `["quotes", undefined]` would be two different
 * entries, the stream would update one and the screen would read the other,
 * and the symptom would be "the prices are not moving" with no error anywhere.
 */

export const keys = {
  markets: () => ["markets"] as const,
  market: (code: string) => ["markets", code] as const,

  /**
   * The profile list.
   *
   * `includeArchived` is part of the key because the two answers are different
   * lists: sharing a key would leave the archived ones in the cache after the
   * toggle went off, and they would keep showing until something else evicted
   * them. Invalidating by the `["profiles"]` prefix still reaches both.
   */
  profiles: (includeArchived = false) => ["profiles", { includeArchived }] as const,
  profile: (ref: string) => ["profiles", ref] as const,
  profileSettings: (ref: string) => ["profiles", ref, "settings"] as const,
  profileLimits: (ref: string) => ["profiles", ref, "limits"] as const,
  profileUniverse: (ref: string) => ["profiles", ref, "universe"] as const,
  settingsHistory: (ref: string) => ["profiles", ref, "settings", "history"] as const,

  quotes: (symbols?: string) => ["quotes", symbols ?? ""] as const,
  /**
   * When the last batch of quotes arrived.
   *
   * It lives in the cache and not in the SSE hook's state because **the stream
   * is opened once, in the Layout**, and the screens are what need the value. A
   * `useStream()` per screen just to read it would open one SSE connection per
   * screen, which is exactly what F3.5 set out to avoid.
   */
  quotesMeta: () => ["quotes", "meta"] as const,
  ingestStatus: () => ["ingest-status"] as const,

  cycleControl: () => ["cycles", "control"] as const,
  cycles: (profile: string) => ["cycles", profile] as const,
  cycle: (id: string) => ["cycles", "detail", id] as const,

  analytics: (profile: string) => ["analytics", profile] as const,
  positions: (profile: string) => ["positions", profile] as const,
  decisions: (profile: string) => ["decisions", profile] as const,
  orders: (profile: string) => ["orders", profile] as const,
  riskEvents: (profile: string) => ["risk-events", profile] as const,
} as const;

/**
 * Prefixes of everything a cycle may have changed by the time it ends.
 *
 * They are invalidated when the stream sees the cycle go from running to
 * stopped: at that moment it has just written positions, decisions, orders and
 * verdicts, and without this the screen would keep showing the previous state
 * until someone reloaded. Invalidating by prefix avoids enumerating every
 * combination of filters.
 */
export const HISTORY_PREFIXES = [
  ["profiles"],
  ["positions"],
  ["decisions"],
  ["orders"],
  ["risk-events"],
  ["cycles"],
  ["analytics"],
] as const;
