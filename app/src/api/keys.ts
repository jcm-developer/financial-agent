/**
 * Claves de TanStack Query, todas en un sitio.
 *
 * Estan centralizadas porque el hook de SSE escribe en la cache por clave
 * (`setQueryData`): con las claves escritas a mano en cada componente, un
 * `["quotes"]` frente a un `["quotes", undefined]` serian dos entradas
 * distintas, el stream actualizaria una y la pantalla leeria la otra, y el
 * sintoma seria "los precios no se mueven" sin ningun error por ningun lado.
 */

export const keys = {
  markets: () => ["markets"] as const,
  market: (code: string) => ["markets", code] as const,

  profiles: () => ["profiles"] as const,
  profile: (ref: string) => ["profiles", ref] as const,
  profileSettings: (ref: string) => ["profiles", ref, "settings"] as const,
  profileLimits: (ref: string) => ["profiles", ref, "limits"] as const,
  profileUniverse: (ref: string) => ["profiles", ref, "universe"] as const,
  settingsHistory: (ref: string) => ["profiles", ref, "settings", "history"] as const,

  quotes: (symbols?: string) => ["quotes", symbols ?? ""] as const,
  ingestStatus: () => ["ingest-status"] as const,

  cycleControl: () => ["cycles", "control"] as const,
  cycles: (profile: string) => ["cycles", profile] as const,
  cycle: (id: string) => ["cycles", "detail", id] as const,

  positions: (profile: string) => ["positions", profile] as const,
  decisions: (profile: string) => ["decisions", profile] as const,
  orders: (profile: string) => ["orders", profile] as const,
  riskEvents: (profile: string) => ["risk-events", profile] as const,
} as const;
