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
        // Half of `REFRESH_MS`, so a tab switch does not fire a round of
        // refetches that would bring nothing new, but coming back to a tab left
        // for a while does not wait up to a minute to show the truth either.
        //
        // It used to say this project's data does not change on its own, and
        // that was never quite right: the scheduler runs cycles at the hours of
        // `CYCLE_TIMES` and the ingestor writes a price a minute, both from other
        // processes. What is true is that **nothing the user does in the
        // interface changes it**, and that is what makes an interval the right
        // tool here and a longer `staleTime` harmless. The interval itself is
        // per-hook: see `REFRESH_MS` in `@/api/hooks`.
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
