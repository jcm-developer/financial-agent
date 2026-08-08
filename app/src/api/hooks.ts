import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import { keys } from "@/api/keys";
import type {
  CycleControl,
  IngestStatus,
  MarketInfo,
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
 * Aqui solo estan los endpoints que hacen falta ya. Los de las tablas paginadas
 * llegan con sus pantallas en el tramo D, donde se puede decidir el tamaño de
 * pagina con la tabla delante.
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
