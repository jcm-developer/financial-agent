import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { keys } from "@/api/keys";
import type {
  ActionResult,
  Analytics,
  CycleControl,
  CycleDetail,
  DerivedLimits,
  IngestStatus,
  MarketInfo,
  Page_CycleRow,
  Page_DecisionRow,
  Page_OrderRow,
  Page_PositionRow,
  Page_RiskEventRow,
  ProfileCreate,
  ProfileDetail,
  ProfileDuplicate,
  ProfilePatch,
  ProfileSummary,
  QuoteRow,
  SettingsApplied,
  SettingsBundle,
  SettingsUpdate,
} from "@/api/types";

/**
 * One hook per endpoint, with the generated type applied.
 *
 * The types come from `@/api/types`, which `tools/gen_api_types.py` generates
 * from the OpenAPI document. That makes a change in `api/models.py` break the
 * frontend's compilation instead of breaking the screen at runtime, which was
 * the reason for generating the types before writing the interface (F3.6).
 *
 * The paginated wrappers are used exactly as the script generates them
 * (`Page_PositionRow` and friends) instead of declaring a `Page<T>` of our own:
 * a hand-written generic would silently stop matching the day `Page` changes in
 * `models.py`, which is precisely what the generated types prevent.
 */

/**
 * How often a screen that shows data asks the server again.
 *
 * **One minute, because that is the ingestor's cadence and not because it is a
 * round number** (D4): `quotes_live` gets one write per symbol per minute, so
 * polling every fifteen seconds would spend four requests to be told the same
 * price. And the feed itself is fifteen minutes behind in Europe (F2.1c), so a
 * faster interval would not buy a fresher number, only more traffic.
 *
 * **It is opted into hook by hook and is not a default of the client**, which is
 * the decision worth writing down. A `refetchInterval` in `createQueryClient`
 * would also reach three groups of queries where it does damage: the ones the
 * SSE already feeds (`quotes`, `ingest-status`, `cycles/control`), where a
 * parallel poll asks for the same thing twice and breaks what D6 bought; the
 * configuration forms, whose values would be replaced under the user's fingers
 * mid-edit; and `limits-preview`, cached once per slider position, which would
 * re-ask for every pair ever tried, forever. So the rule is: **a screen that
 * reads history or valuations refreshes, a form does not.**
 *
 * `refetchIntervalInBackground` stays at its default of false on purpose: a tab
 * left open in another window stops polling, and the refetch on focus is what
 * brings it up to date when it comes back.
 */
export const REFRESH_MS = 60_000;

/** A table's filters, exactly as they travel in the query string. */
type Filters = Record<string, string | number | undefined>;

/**
 * Reads the markets the project knows about, with their calendar and currency.
 *
 * @return The query for `GET /api/markets`.
 */
export function useMarkets() {
  return useQuery({
    queryKey: keys.markets(),
    queryFn: ({ signal }) => api.get<MarketInfo[]>("/api/markets", undefined, signal),
    // An exchange's hours and universe do not change during a session; the only
    // live field is `is_operating`, and the refetch on focus covers that.
    staleTime: 5 * 60_000,
  });
}

/**
 * Reads every experiment profile.
 *
 * @param includeArchived - Whether archived profiles come along. It is part of
 *     the cache key, so the two answers do not overwrite each other: without
 *     that, switching the toggle off would leave the archived ones in the cache
 *     under the same key and they would keep showing.
 * @return The query for `GET /api/profiles`.
 */
export function useProfiles(includeArchived = false) {
  return useQuery({
    queryKey: keys.profiles(includeArchived),
    queryFn: ({ signal }) =>
      api.get<ProfileSummary[]>(
        "/api/profiles",
        includeArchived ? { include_archived: true } : undefined,
        signal,
      ),
    // The list carries `metrics`, and `metrics` carries the equity: it is where
    // the eight figures of Resumen and every card on Inicio come from. Without
    // the interval the capital of an experiment under watch stays frozen at
    // whatever it was when the tab was opened.
    refetchInterval: REFRESH_MS,
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
  const list = symbols?.length ? symbols.join(",") : undefined;
  return useQuery({
    queryKey: keys.quotes(list),
    queryFn: ({ signal }) =>
      api.get<QuoteRow[]>("/api/quotes", { symbols: list }, signal),
    // No interval refetch: keeping these fresh is the SSE's job, and polling in
    // parallel would ask for the same thing twice (D6).
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
 * @param profile - Profile name. Undefined leaves the query disabled.
 * @return The query for `GET /api/analytics`.
 */
export function useAnalytics(profile: string | undefined) {
  return useQuery({
    queryKey: keys.analytics(profile ?? ""),
    queryFn: ({ signal }) =>
      api.get<Analytics>("/api/analytics", { profile }, signal),
    enabled: Boolean(profile),
    refetchInterval: REFRESH_MS,
  });
}

// ----------------------------------------------------------------------
// History tables
// ----------------------------------------------------------------------

/**
 * The filters go into the cache key on purpose.
 *
 * That way going back from "rejected only" to "all" paints instantly from the
 * cache instead of waiting for another request, and two screens with different
 * filters do not overwrite each other.
 *
 * **These five tables are the reason `REFRESH_MS` exists.** The prices of the
 * book do not travel over the stream: `/api/positions` is what values an open
 * position —it reads `quotes_live`, falls back to the last `market_snapshots`,
 * and from there computes `last_price`, `market_value`, `unrealized_pnl` and the
 * distance to the stop— so the only way to see a price move is to ask the
 * endpoint again. **Valuing them in the browser from the `quotes` cache was the
 * alternative and it is rejected for the same reason as F6.8**: it would be a
 * second implementation of a calculation the server already owns, condemned to
 * disagree with it, and the screen would be showing a P&L the API never said.
 *
 * @template T - The generated `Page_*` wrapper the endpoint returns.
 * @param key - Base cache key, which the filters extend.
 * @param path - Relative API path of the table.
 * @param profile - Profile name. Undefined leaves the query disabled.
 * @param filters - Query filters, part of the cache key.
 * @return The query for that page of the table.
 */
function usePage<T>(
  key: readonly unknown[],
  path: string,
  profile: string | undefined,
  filters: Filters,
) {
  return useQuery({
    queryKey: [...key, filters],
    queryFn: ({ signal }) =>
      api.get<T>(path, { profile, ...filters }, signal),
    enabled: Boolean(profile),
    refetchInterval: REFRESH_MS,
  });
}

/**
 * Reads the positions of an experiment, open and closed.
 *
 * @param profile - Profile name. Undefined leaves the query disabled.
 * @param filters - Query filters, part of the cache key.
 * @return The query for `GET /api/positions`.
 */
export function usePositions(profile: string | undefined, filters: Filters = {}) {
  return usePage<Page_PositionRow>(
    keys.positions(profile ?? ""), "/api/positions", profile, filters,
  );
}

/**
 * Reads what the model proposed on each cycle, verdict included.
 *
 * @param profile - Profile name. Undefined leaves the query disabled.
 * @param filters - Query filters, part of the cache key.
 * @return The query for `GET /api/decisions`.
 */
export function useDecisions(profile: string | undefined, filters: Filters = {}) {
  return usePage<Page_DecisionRow>(
    keys.decisions(profile ?? ""), "/api/decisions", profile, filters,
  );
}

/**
 * Reads the orders the simulated broker executed.
 *
 * @param profile - Profile name. Undefined leaves the query disabled.
 * @param filters - Query filters, part of the cache key.
 * @return The query for `GET /api/orders`.
 */
export function useOrders(profile: string | undefined, filters: Filters = {}) {
  return usePage<Page_OrderRow>(
    keys.orders(profile ?? ""), "/api/orders", profile, filters,
  );
}

/**
 * Reads what the risk manager rejected or resized, and under which rule.
 *
 * @param profile - Profile name. Undefined leaves the query disabled.
 * @param filters - Query filters, part of the cache key.
 * @return The query for `GET /api/risk-events`.
 */
export function useRiskEvents(profile: string | undefined, filters: Filters = {}) {
  return usePage<Page_RiskEventRow>(
    keys.riskEvents(profile ?? ""), "/api/risk-events", profile, filters,
  );
}

/**
 * Reads the cycles an experiment has run.
 *
 * @param profile - Profile name. Undefined leaves the query disabled.
 * @param filters - Query filters, part of the cache key.
 * @return The query for `GET /api/cycles`.
 */
export function useCycles(profile: string | undefined, filters: Filters = {}) {
  return usePage<Page_CycleRow>(
    keys.cycles(profile ?? ""), "/api/cycles", profile, filters,
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
// Cycle control (F3.4)
// ----------------------------------------------------------------------

/**
 * Launches a cycle.
 *
 * The tables are not invalidated when the mutation finishes, because **at that
 * moment the cycle has only just started and has written nothing yet**: it is
 * ~20 minutes of work. What refreshes the tables is the stream's `cycle` event
 * once the cycle stops running.
 *
 * @param profile - Profile the cycle runs against.
 * @return The mutation for `POST /api/cycles/run`, which takes `{dry_run}` and
 *     writes the returned state straight into the control cache.
 */
export function useRunCycle(profile: string | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (options: { dry_run?: boolean } = {}) =>
      api.post<CycleControl>("/api/cycles/run", {
        profile,
        dry_run: options.dry_run ?? false,
      }),
    onSuccess: (state) => client.setQueryData(keys.cycleControl(), state),
  });
}

/**
 * Liquidates the book to end an experiment (F5.8).
 *
 * Everything is invalidated on success, not just the control state: closing
 * sells the whole book, so positions, orders, cycles and the analytics all
 * change at once. It is the same reasoning as deleting a profile.
 *
 * @param profile - Experiment to close.
 * @return The mutation for `POST /api/cycles/close-experiment`.
 */
export function useCloseExperiment(profile: string | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<CycleControl>("/api/cycles/close-experiment", { profile }),
    onSuccess: (state) => {
      client.setQueryData(keys.cycleControl(), state);
      client.invalidateQueries();
    },
  });
}

/**
 * Asks the running cycle to stop.
 *
 * @return The mutation for `POST /api/cycles/stop`.
 */
export function useStopCycle() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<ActionResult>("/api/cycles/stop"),
    onSuccess: () => client.invalidateQueries({ queryKey: keys.cycleControl() }),
  });
}

// ----------------------------------------------------------------------
// Configuration writes (F5.3, F5.4, F6.8)
// ----------------------------------------------------------------------

/**
 * An experiment's 41 parameters, plus the limits they currently imply.
 *
 * @param ref - Profile name or id. Undefined leaves the query disabled.
 * @return The query for `GET /api/profiles/:ref/settings`.
 */
export function useProfileSettings(ref: string | undefined) {
  return useQuery({
    queryKey: keys.profileSettings(ref ?? ""),
    queryFn: ({ signal }) =>
      api.get<SettingsBundle>(
        `/api/profiles/${encodeURIComponent(ref!)}/settings`, undefined, signal,
      ),
    enabled: Boolean(ref),
  });
}

/**
 * What the two sliders would give, without writing anything.
 *
 * This is how the form shows the derived limits **while the slider moves**
 * (F6.8). It is a request per position of the slider, which sounds like a lot
 * and is not: it goes against a local SQLite, `staleTime: Infinity` means each
 * pair is asked for once, and going back over values already tried paints from
 * the cache. The alternative was redoing `derive_limits` in TypeScript, which is
 * the one thing F6.5 forbids.
 *
 * @param risk - Risk profile, 1 to 10.
 * @param diversification - Diversification, 1 to 10.
 * @return The query for `GET /api/profiles/limits-preview`.
 */
export function useLimitsPreview(risk: number, diversification: number) {
  return useQuery({
    queryKey: ["limits-preview", risk, diversification] as const,
    queryFn: ({ signal }) =>
      api.get<DerivedLimits>(
        "/api/profiles/limits-preview",
        { risk_profile: risk, diversification },
        signal,
      ),
    staleTime: Infinity,
  });
}

/**
 * The eleven effective limits and which of them come from the sliders.
 *
 * The derivation is **not repeated in the frontend** (F6.8): `derive_limits`
 * interpolates by stretches in `src/risk_presets.py`, and a second
 * implementation in TypeScript would be two tables condemned to disagree — with
 * the interface promising limits the Risk Manager does not apply, which is the
 * one lie this screen must not tell.
 *
 * @param ref - Profile name or id. Undefined leaves the query disabled.
 * @return The query for `GET /api/profiles/:ref/limits`.
 */
export function useProfileLimits(ref: string | undefined) {
  return useQuery({
    queryKey: keys.profileLimits(ref ?? ""),
    queryFn: ({ signal }) =>
      api.get<DerivedLimits>(
        `/api/profiles/${encodeURIComponent(ref!)}/limits`, undefined, signal,
      ),
    enabled: Boolean(ref),
  });
}

/**
 * Everything a configuration write may have changed.
 *
 * The profile list carries the metrics and the risk summary, and the active
 * profile is resolved against it (`useActiveProfile`), so a rename or a status
 * change that did not invalidate it would leave the header naming a profile that
 * no longer exists under that name.
 *
 * @param client - The query client to invalidate on.
 * @param ref - Profile whose own entries are refreshed too.
 */
function invalidateProfile(client: ReturnType<typeof useQueryClient>, ref?: string) {
  // By prefix, not by the exact key: the list is cached once per
  // `include_archived`, and archiving writes into one of the two while the other
  // is what is on screen.
  client.invalidateQueries({ queryKey: ["profiles"] });
  if (ref) {
    client.invalidateQueries({ queryKey: keys.profile(ref) });
    client.invalidateQueries({ queryKey: keys.profileLimits(ref) });
    client.invalidateQueries({ queryKey: keys.profileSettings(ref) });
  }
}

/**
 * Creates an experiment.
 *
 * It answers with the profile in `draft`: the API creates it that way and
 * activating it is a separate call, which is what F5.3 relies on so a failed
 * settings patch leaves a visible, deletable draft instead of an experiment
 * running with parameters the user did not choose.
 *
 * @return The mutation for `POST /api/profiles`.
 */
export function useCreateProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ProfileCreate) => api.post<ProfileDetail>("/api/profiles", body),
    onSuccess: () => invalidateProfile(client),
  });
}

/**
 * Changes an experiment's name, description or status.
 *
 * @return The mutation for `PATCH /api/profiles/:ref`, taking the reference and
 *     the patch together so one hook serves a list of profiles.
 */
export function useUpdateProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ ref, patch }: { ref: string; patch: ProfilePatch }) =>
      api.patch<ProfileDetail>(`/api/profiles/${encodeURIComponent(ref)}`, patch),
    onSuccess: (_data, { ref }) => invalidateProfile(client, ref),
  });
}

/**
 * Writes the experiment's parameters.
 *
 * @return The mutation for `PATCH /api/profiles/:ref/settings`, which answers
 *     with the list of fields that actually changed.
 */
export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ ref, changes }: { ref: string; changes: SettingsUpdate }) =>
      api.patch<SettingsApplied>(
        `/api/profiles/${encodeURIComponent(ref)}/settings`, changes,
      ),
    onSuccess: (_data, { ref }) => invalidateProfile(client, ref),
  });
}

/**
 * Clones an experiment's settings and universe, not its history.
 *
 * @return The mutation for `POST /api/profiles/:ref/duplicate`.
 */
export function useDuplicateProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ ref, body }: { ref: string; body: ProfileDuplicate }) =>
      api.post<ProfileDetail>(
        `/api/profiles/${encodeURIComponent(ref)}/duplicate`, body,
      ),
    onSuccess: () => invalidateProfile(client),
  });
}

/**
 * Deletes an experiment and everything hanging off it.
 *
 * The name has to be repeated in `confirm`, and that is the API's rule and not
 * the screen's: it is the only call in the whole API that destroys data which
 * took weeks to produce.
 *
 * @return The mutation for `DELETE /api/profiles/:ref?confirm=`.
 */
export function useDeleteProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ ref, confirm }: { ref: string; confirm: string }) =>
      api.delete<ActionResult>(`/api/profiles/${encodeURIComponent(ref)}`, { confirm }),
    // Everything is invalidated, not just the profiles: deleting drags along
    // cycles, positions, orders and decisions, and any table still cached would
    // be showing the history of an experiment that no longer exists.
    onSuccess: () => client.invalidateQueries(),
  });
}
