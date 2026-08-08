import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { keys } from "@/api/keys";
import type {
  ActionResult,
  Analytics,
  CycleControl,
  CycleDetail,
  IngestStatus,
  MarketInfo,
  Page_CycleRow,
  Page_DecisionRow,
  Page_OrderRow,
  Page_PositionRow,
  Page_RiskEventRow,
  ProfileDetail,
  ProfileSummary,
  QuoteRow,
} from "@/api/types";

/**
 * Un hook por endpoint, con el tipo generado puesto.
 *
 * Los tipos salen de `@/api/types`, que genera `tools/gen_api_types.py` del
 * OpenAPI. Eso hace que un cambio en `api/models.py` rompa la compilacion del
 * frontend en lugar de romper la pantalla en tiempo de ejecucion, que era la
 * razon de generar los tipos antes de escribir la interfaz (F3.6).
 *
 * Los envoltorios paginados se usan tal como los genera el script
 * (`Page_PositionRow` y compañia) en lugar de declarar aqui un `Pagina<T>`
 * propio: un generico escrito a mano dejaria de cuadrar en silencio el dia que
 * `Page` cambie en `models.py`, que es justo lo que los tipos generados evitan.
 */

/** Los filtros de una tabla, tal cual viajan en la query. */
type Filtros = Record<string, string | number | undefined>;

/**
 * Reads the markets the project knows about, with their calendar and currency.
 *
 * @return The query for `GET /api/markets`.
 */
export function useMarkets() {
  return useQuery({
    queryKey: keys.markets(),
    queryFn: ({ signal }) => api.get<MarketInfo[]>("/api/markets", undefined, signal),
    // El horario y el universo de una bolsa no cambian durante una sesion; lo
    // unico vivo es `is_operating`, y para eso ya hay un refetch al enfocar.
    staleTime: 5 * 60_000,
  });
}

/**
 * Reads every experiment profile.
 *
 * @return The query for `GET /api/profiles`.
 */
export function useProfiles() {
  return useQuery({
    queryKey: keys.profiles(),
    queryFn: ({ signal }) => api.get<ProfileSummary[]>("/api/profiles", undefined, signal),
  });
}

/**
 * Reads one profile in full, settings included.
 *
 * @param ref - Profile name or id. Undefined leaves the query disabled.
 * @return The query for `GET /api/profiles/:ref`.
 */
export function useProfile(ref: string | undefined) {
  return useQuery({
    queryKey: keys.profile(ref ?? ""),
    queryFn: ({ signal }) =>
      api.get<ProfileDetail>(`/api/profiles/${encodeURIComponent(ref!)}`, undefined, signal),
    enabled: Boolean(ref),
  });
}

/**
 * Reads the last known price of each symbol. The stream keeps it fresh.
 *
 * @param symbols - Symbols to ask for. Empty or undefined asks for all of them.
 * @return The query for `GET /api/quotes`.
 */
export function useQuotes(symbols?: string[]) {
  const lista = symbols?.length ? symbols.join(",") : undefined;
  return useQuery({
    queryKey: keys.quotes(lista),
    queryFn: ({ signal }) =>
      api.get<QuoteRow[]>("/api/quotes", { symbols: lista }, signal),
    // Sin refetch por intervalo: de mantenerlos frescos se encarga el SSE, y un
    // sondeo en paralelo pediria lo mismo dos veces (D6).
    staleTime: Infinity,
  });
}

/**
 * Reads how the ingestor is doing: last tick, symbols covered, failures.
 *
 * @return The query for `GET /api/ingest-status`.
 */
export function useIngestStatus() {
  return useQuery({
    queryKey: keys.ingestStatus(),
    queryFn: ({ signal }) =>
      api.get<IngestStatus>("/api/ingest-status", undefined, signal),
    staleTime: Infinity,
  });
}

/**
 * Reads whether a cycle is running and whether the controls are enabled at all.
 *
 * @return The query for `GET /api/cycles/control/status`.
 */
export function useCycleControl() {
  return useQuery({
    queryKey: keys.cycleControl(),
    queryFn: ({ signal }) =>
      api.get<CycleControl>("/api/cycles/control/status", undefined, signal),
    staleTime: Infinity,
  });
}

/**
 * Reads the computed analytics of one experiment: equity curve, calibration,
 * per-symbol breakdown.
 *
 * @param perfil - Profile name. Undefined leaves the query disabled.
 * @return The query for `GET /api/analytics`.
 */
export function useAnalytics(perfil: string | undefined) {
  return useQuery({
    queryKey: keys.analytics(perfil ?? ""),
    queryFn: ({ signal }) =>
      api.get<Analytics>("/api/analytics", { profile: perfil }, signal),
    enabled: Boolean(perfil),
  });
}

// ----------------------------------------------------------------------
// Tablas del historico
// ----------------------------------------------------------------------

/**
 * Los filtros entran en la clave de cache a proposito.
 *
 * Asi volver de «solo rechazadas» a «todas» pinta al instante desde la cache en
 * lugar de esperar otra peticion, y dos pantallas con filtros distintos no se
 * sobrescriben la una a la otra.
 *
 * @template T - The generated `Page_*` wrapper the endpoint returns.
 * @param clave - Base cache key, which the filters extend.
 * @param ruta - Relative API path of the table.
 * @param perfil - Profile name. Undefined leaves the query disabled.
 * @param filtros - Query filters, part of the cache key.
 * @return The query for that page of the table.
 */
function usePagina<T>(
  clave: readonly unknown[],
  ruta: string,
  perfil: string | undefined,
  filtros: Filtros,
) {
  return useQuery({
    queryKey: [...clave, filtros],
    queryFn: ({ signal }) =>
      api.get<T>(ruta, { profile: perfil, ...filtros }, signal),
    enabled: Boolean(perfil),
  });
}

/**
 * Reads the positions of an experiment, open and closed.
 *
 * @param perfil - Profile name. Undefined leaves the query disabled.
 * @param filtros - Query filters, part of the cache key.
 * @return The query for `GET /api/positions`.
 */
export function usePositions(perfil: string | undefined, filtros: Filtros = {}) {
  return usePagina<Page_PositionRow>(
    keys.positions(perfil ?? ""), "/api/positions", perfil, filtros,
  );
}

/**
 * Reads what the model proposed on each cycle, verdict included.
 *
 * @param perfil - Profile name. Undefined leaves the query disabled.
 * @param filtros - Query filters, part of the cache key.
 * @return The query for `GET /api/decisions`.
 */
export function useDecisions(perfil: string | undefined, filtros: Filtros = {}) {
  return usePagina<Page_DecisionRow>(
    keys.decisions(perfil ?? ""), "/api/decisions", perfil, filtros,
  );
}

/**
 * Reads the orders the simulated broker executed.
 *
 * @param perfil - Profile name. Undefined leaves the query disabled.
 * @param filtros - Query filters, part of the cache key.
 * @return The query for `GET /api/orders`.
 */
export function useOrders(perfil: string | undefined, filtros: Filtros = {}) {
  return usePagina<Page_OrderRow>(
    keys.orders(perfil ?? ""), "/api/orders", perfil, filtros,
  );
}

/**
 * Reads what the risk manager rejected or resized, and under which rule.
 *
 * @param perfil - Profile name. Undefined leaves the query disabled.
 * @param filtros - Query filters, part of the cache key.
 * @return The query for `GET /api/risk-events`.
 */
export function useRiskEvents(perfil: string | undefined, filtros: Filtros = {}) {
  return usePagina<Page_RiskEventRow>(
    keys.riskEvents(perfil ?? ""), "/api/risk-events", perfil, filtros,
  );
}

/**
 * Reads the cycles an experiment has run.
 *
 * @param perfil - Profile name. Undefined leaves the query disabled.
 * @param filtros - Query filters, part of the cache key.
 * @return The query for `GET /api/cycles`.
 */
export function useCycles(perfil: string | undefined, filtros: Filtros = {}) {
  return usePagina<Page_CycleRow>(
    keys.cycles(perfil ?? ""), "/api/cycles", perfil, filtros,
  );
}

/**
 * Reads one cycle in full, with the settings it ran under.
 *
 * @param id - Cycle id. Undefined leaves the query disabled.
 * @return The query for `GET /api/cycles/:id`.
 */
export function useCycle(id: string | undefined) {
  return useQuery({
    queryKey: keys.cycle(id ?? ""),
    queryFn: ({ signal }) =>
      api.get<CycleDetail>(`/api/cycles/${encodeURIComponent(id!)}`, undefined, signal),
    enabled: Boolean(id),
  });
}

// ----------------------------------------------------------------------
// Control del ciclo (F3.4)
// ----------------------------------------------------------------------

/**
 * Lanzar y parar un ciclo.
 *
 * No se invalidan las tablas al terminar la mutacion, porque **en ese momento el
 * ciclo acaba de arrancar y todavia no ha escrito nada**: son ~20 minutos de
 * trabajo. Lo que refresca las tablas es el evento `cycle` del stream cuando el
 * ciclo pasa a no estar corriendo.
 *
 * @param perfil - Profile the cycle runs against.
 * @return The mutation for `POST /api/cycles/run`, which takes `{dry_run}` and
 *     writes the returned state straight into the control cache.
 */
export function useLanzarCiclo(perfil: string | undefined) {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: (opciones: { dry_run?: boolean } = {}) =>
      api.post<CycleControl>("/api/cycles/run", {
        profile: perfil,
        dry_run: opciones.dry_run ?? false,
      }),
    onSuccess: (estado) => cliente.setQueryData(keys.cycleControl(), estado),
  });
}

/**
 * Asks the running cycle to stop.
 *
 * @return The mutation for `POST /api/cycles/stop`.
 */
export function usePararCiclo() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ActionResult>("/api/cycles/stop"),
    onSuccess: () => cliente.invalidateQueries({ queryKey: keys.cycleControl() }),
  });
}
