import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { keys } from "@/api/keys";
import type {
  ActionResult,
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

export function useMarkets() {
  return useQuery({
    queryKey: keys.markets(),
    queryFn: ({ signal }) => api.get<MarketInfo[]>("/api/markets", undefined, signal),
    // El horario y el universo de una bolsa no cambian durante una sesion; lo
    // unico vivo es `is_operating`, y para eso ya hay un refetch al enfocar.
    staleTime: 5 * 60_000,
  });
}

export function useProfiles() {
  return useQuery({
    queryKey: keys.profiles(),
    queryFn: ({ signal }) => api.get<ProfileSummary[]>("/api/profiles", undefined, signal),
  });
}

export function useProfile(ref: string | undefined) {
  return useQuery({
    queryKey: keys.profile(ref ?? ""),
    queryFn: ({ signal }) =>
      api.get<ProfileDetail>(`/api/profiles/${encodeURIComponent(ref!)}`, undefined, signal),
    enabled: Boolean(ref),
  });
}

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

export function useIngestStatus() {
  return useQuery({
    queryKey: keys.ingestStatus(),
    queryFn: ({ signal }) =>
      api.get<IngestStatus>("/api/ingest-status", undefined, signal),
    staleTime: Infinity,
  });
}

export function useCycleControl() {
  return useQuery({
    queryKey: keys.cycleControl(),
    queryFn: ({ signal }) =>
      api.get<CycleControl>("/api/cycles/control/status", undefined, signal),
    staleTime: Infinity,
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

export function usePositions(perfil: string | undefined, filtros: Filtros = {}) {
  return usePagina<Page_PositionRow>(
    keys.positions(perfil ?? ""), "/api/positions", perfil, filtros,
  );
}

export function useDecisions(perfil: string | undefined, filtros: Filtros = {}) {
  return usePagina<Page_DecisionRow>(
    keys.decisions(perfil ?? ""), "/api/decisions", perfil, filtros,
  );
}

export function useOrders(perfil: string | undefined, filtros: Filtros = {}) {
  return usePagina<Page_OrderRow>(
    keys.orders(perfil ?? ""), "/api/orders", perfil, filtros,
  );
}

export function useRiskEvents(perfil: string | undefined, filtros: Filtros = {}) {
  return usePagina<Page_RiskEventRow>(
    keys.riskEvents(perfil ?? ""), "/api/risk-events", perfil, filtros,
  );
}

export function useCycles(perfil: string | undefined, filtros: Filtros = {}) {
  return usePagina<Page_CycleRow>(
    keys.cycles(perfil ?? ""), "/api/cycles", perfil, filtros,
  );
}

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

export function usePararCiclo() {
  const cliente = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ActionResult>("/api/cycles/stop"),
    onSuccess: () => cliente.invalidateQueries({ queryKey: keys.cycleControl() }),
  });
}
