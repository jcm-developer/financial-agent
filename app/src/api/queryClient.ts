import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";

/**
 * Builds the application's TanStack Query client.
 *
 * El `QueryClient` de la aplicacion, con dos decisiones que no son el default.
 *
 * @return A client that keeps queries fresh for 30 s, retries a query twice
 *     unless the API returned a 4xx, and never retries a mutation.
 */
export function crearQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Los datos de este proyecto no cambian por si solos salvo los precios,
        // y esos llegan por SSE (F4.5). Un `staleTime` alto evita que cambiar de
        // pestaña dispare una tanda de refetches que no van a traer nada nuevo.
        staleTime: 30_000,
        // Reintentar un 404 o un 422 es pedir el mismo error otra vez, y encima
        // retrasa el mensaje que el usuario necesita ver.
        retry: (intento, error) => {
          if (error instanceof ApiError && error.isClientError) return false;
          return intento < 2;
        },
        refetchOnWindowFocus: true,
      },
      mutations: {
        // Una escritura que falla no se repite sola: en esta API las escrituras
        // crean perfiles y lanzan ciclos, y repetirlas a ciegas puede duplicar
        // lo que ya se hizo.
        retry: false,
      },
    },
  });
}
