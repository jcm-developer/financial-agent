import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";

/**
 * Builds the application's TanStack Query client, with two non-default choices.
 *
 * @return A client that keeps queries fresh for 30 s, retries a query twice
 *     unless the API returned a 4xx, and never retries a mutation.
 */
export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // This project's data does not change on its own except for prices, and
        // those arrive over SSE (F4.5). A high `staleTime` stops a tab switch
        // from firing a round of refetches that would bring nothing new.
        staleTime: 30_000,
        // Retrying a 404 or a 422 asks for the same error again, and delays the
        // message the user needs to see.
        retry: (attempt, error) => {
          if (error instanceof ApiError && error.isClientError) return false;
          return attempt < 2;
        },
        refetchOnWindowFocus: true,
      },
      mutations: {
        // A failed write does not repeat itself: in this API writes create
        // profiles and launch cycles, and repeating them blindly can duplicate
        // what was already done.
        retry: false,
      },
    },
  });
}
