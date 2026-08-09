import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { keys, HISTORY_PREFIXES } from "@/api/keys";
import type { CycleControl, IngestStatus, QuoteRow } from "@/api/types";

/**
 * The real-time hook (F4.5), on top of the SSE endpoint of F3.5.
 *
 * **It writes into the TanStack Query cache, not into state of its own.** That
 * is the design decision of stretch B: with two sources for the same price —the
 * response of `/api/quotes` and whatever arrives over the stream— the screen
 * would end up showing two different numbers depending on the component, and
 * there would be no single place to fix it. This way `useQuotes()` is the only
 * read and the stream merely keeps it fresh.
 *
 * And it is worth restating what F3.5 says without dressing it up: **underneath,
 * this polls**. The ingestor runs in another process, so the server looks at the
 * file every two seconds and sends only what changed. The gain is moving the
 * polling from the browser to the server, not that there is real push.
 */

export type StreamState = "connecting" | "live" | "disconnected";

/** What the `ingest` event carries: five of IngestStatus's thirteen fields. */
type IngestEvent = Pick<
  IngestStatus,
  | "healthy"
  | "message"
  | "last_tick_at"
  | "seconds_since_last_tick"
  | "consecutive_failures"
>;

/** What the `cycle` event carries: the state, and `from` only on increments. */
type CycleEvent = CycleControl & { from?: number };

interface QuotesEvent {
  quotes: QuoteRow[];
  mark: string;
}

/**
 * Merges the `ingest` event into whatever is already in the cache.
 *
 * **It cannot replace**: the event brings five fields and `/api/ingest-status`
 * returns thirteen —checked against the server, not assumed— so a
 * `setQueryData(event)` would wipe `avg_latency_ms`, `symbols_tracked`,
 * `bars_stored` and the list of recent ticks. The health panel would go
 * half-blank the moment the ingestor changed its verdict, which is exactly when
 * it gets looked at. TypeScript does not warn because the event is a valid
 * subset.
 *
 * If there is nothing in the cache yet it returns `undefined`: inventing the
 * other eight fields with zeros would assert things we do not know. The normal
 * query will bring them.
 *
 * @param previous - What the cache already holds, if the normal query has landed.
 * @param event - The five fields the `ingest` event carries.
 * @return The merged status, or undefined when there was nothing to merge into.
 */
export function mergeIngest(
  previous: IngestStatus | undefined,
  event: IngestEvent,
): IngestStatus | undefined {
  if (!previous) return undefined;
  return { ...previous, ...event };
}

/** What to do with a `cycle` event once the merge has been attempted. */
export interface CycleMergeResult {
  state: CycleControl;
  /** True if lines were lost and the server has to be re-read. */
  hasGap: boolean;
}

/**
 * Merges the `cycle` event, which arrives in two different shapes.
 *
 * The server sends the full state when the connection opens (with every line in
 * the buffer and no `from`) and afterwards only the new ones, with `from` saying
 * which index they start at. Resending the 400 lines every two seconds would
 * turn "live" into a trickle of megabytes, so the client has to splice.
 *
 * The interesting case is the gap: if `from` is greater than the lines we hold,
 * something was lost on the way. **It is not filled in by eye** — splicing over
 * the gap would produce a log that reads as continuous without being so, which
 * is worse than not having it — the server, which holds the truth, is asked for
 * a re-read.
 *
 * @param previous - Cycle state already in the cache, with the lines seen so far.
 * @param event - The event, carrying `from` only when it is incremental.
 * @return The merged state, and whether lines were lost and the caller must
 *     re-read from the server.
 */
export function mergeCycle(
  previous: CycleControl | undefined,
  event: CycleEvent,
): CycleMergeResult {
  const { from, ...state } = event;
  const incoming = event.lines ?? [];

  if (from === undefined) {
    // Initial state (or reconnection): the event already carries the whole buffer.
    return { state, hasGap: false };
  }

  const previousLines = previous?.lines ?? [];
  if (from > previousLines.length) {
    return { state: { ...state, lines: [...previousLines, ...incoming] }, hasGap: true };
  }
  return {
    state: { ...state, lines: [...previousLines.slice(0, from), ...incoming] },
    hasGap: false,
  };
}

/**
 * Applies one stream event to the query cache.
 *
 * It lives outside the hook so it can be tested.
 *
 * @param client - Query client that owns the cache.
 * @param name - Event name. Anything other than `quotes`, `ingest` or `cycle`
 *     is ignored.
 * @param data - Parsed event payload.
 * @param quotesKey - Cache key of the quotes query this stream feeds, which
 *     depends on the symbol list the connection asked for.
 * @param now - Arrival timestamp. Injectable so the tests do not need a clock.
 */
export function applyEvent(
  client: QueryClient,
  name: string,
  data: unknown,
  quotesKey: readonly unknown[],
  now: number = Date.now(),
): void {
  switch (name) {
    case "quotes": {
      const event = data as QuotesEvent;
      client.setQueryData(quotesKey, event.quotes);
      // The arrival mark goes into the cache too, not into the hook's state:
      // the stream is opened once in the Layout and the screens are what need
      // it. See `keys.quotesMeta`.
      client.setQueryData(keys.quotesMeta(), { receivedAt: now });
      break;
    }
    case "ingest": {
      client.setQueryData<IngestStatus | undefined>(keys.ingestStatus(), (previous) =>
        mergeIngest(previous, data as IngestEvent),
      );
      break;
    }
    case "cycle": {
      const previous = client.getQueryData<CycleControl>(keys.cycleControl());
      const { state, hasGap } = mergeCycle(previous, data as CycleEvent);
      client.setQueryData(keys.cycleControl(), state);
      if (hasGap) {
        void client.invalidateQueries({ queryKey: keys.cycleControl() });
      }
      // The cycle has just finished: it is the only moment when the history
      // changes all at once. Without this the screen would keep showing the
      // positions from before the cycle until someone reloaded by hand, and in
      // an experiment under watch that reads as "it did nothing".
      if (previous?.running && !state.running) {
        for (const prefix of HISTORY_PREFIXES) {
          void client.invalidateQueries({ queryKey: prefix });
        }
      }
      break;
    }
  }
}

interface StreamOptions {
  symbols?: string[];
  enabled?: boolean;
}

export interface Stream {
  state: StreamState;
  /** How many times it has reconnected. Useful to see a connection that dances. */
  reconnections: number;
  /** Reason for the last cut, if the server gave one. */
  lastNotice: string | null;
}

/**
 * Opens the SSE connection and keeps the cache fresh for as long as it lives.
 *
 * It is opened once, in the Layout: a `useStream()` per screen would open one
 * connection per screen, which is what F3.5 set out to avoid.
 *
 * @param options - Stream options.
 * @param options.symbols - Symbols to subscribe to. Empty or undefined asks for
 *     all of them.
 * @param options.enabled - Whether to connect at all. False reports the stream
 *     as disconnected without opening anything.
 * @return The connection state, the reconnection count and the last notice the
 *     server sent before cutting.
 */
export function useStream({ symbols, enabled = true }: StreamOptions = {}): Stream {
  const client = useQueryClient();
  const list = symbols?.length ? symbols.join(",") : undefined;

  const [state, setState] = useState<StreamState>("connecting");
  const [reconnections, setReconnections] = useState(0);
  const [lastNotice, setLastNotice] = useState<string | null>(null);
  // So the very first connection is not counted as a reconnection.
  const hasOpened = useRef(false);

  useEffect(() => {
    if (!enabled) {
      setState("disconnected");
      return;
    }

    const url = list
      ? `/api/stream?symbols=${encodeURIComponent(list)}`
      : "/api/stream";
    const source = new EventSource(url);
    const quotesKey = keys.quotes(list);

    const onMessage = (name: string) => (event: MessageEvent<string>) => {
      let data: unknown;
      try {
        data = JSON.parse(event.data);
      } catch {
        return; // An unreadable event is ignored; the next one brings the state.
      }
      if (name === "bye" || name === "error") {
        const notice = (data as { reason?: string; message?: string });
        setLastNotice(notice.message ?? notice.reason ?? null);
        return;
      }
      applyEvent(client, name, data, quotesKey);
    };

    const subscriptions = ["quotes", "ingest", "cycle", "bye", "error"] as const;
    const listeners = subscriptions.map((name) => {
      const listener = onMessage(name);
      source.addEventListener(name, listener as EventListener);
      return [name, listener] as const;
    });

    source.onopen = () => {
      setState("live");
      if (hasOpened.current) setReconnections((n) => n + 1);
      hasOpened.current = true;
      // On reconnect, whatever is in the cache may be stale: the server sends
      // the full state, but the paginated tables do not travel over the stream.
      void client.invalidateQueries({ queryKey: keys.ingestStatus() });
    };

    source.onerror = () => {
      // `EventSource` reconnects on its own —the reason for choosing SSE in D6—
      // so this is not always a failure: it also fires when the server retires
      // the connection by age (F3.5, 15 min). The state comes from `readyState`,
      // which is the only thing that tells "retrying" from "closed for good".
      setState(source.readyState === EventSource.CLOSED ? "disconnected" : "connecting");
    };

    return () => {
      for (const [name, listener] of listeners) {
        source.removeEventListener(name, listener as EventListener);
      }
      source.close();
    };
    // `client` is stable (it comes from the provider) but is declared for honesty.
  }, [client, list, enabled]);

  return { state, reconnections, lastNotice };
}

/**
 * When the last batch of quotes arrived, in `Date.now()` terms.
 *
 * It is read from the cache and not from the SSE hook so any screen can ask for
 * it without opening a connection of its own. `initialData` plus
 * `staleTime: Infinity` keep the entry always fresh, so `queryFn` never runs:
 * the only writer here is `applyEvent`.
 *
 * @return The arrival timestamp, or null while no batch has arrived yet.
 */
export function useQuotesReceivedAt(): number | null {
  const { data } = useQuery({
    queryKey: keys.quotesMeta(),
    queryFn: () => ({ receivedAt: null as number | null }),
    initialData: { receivedAt: null as number | null },
    staleTime: Infinity,
    gcTime: Infinity,
  });
  return data.receivedAt;
}

/**
 * The real age of a price, in seconds.
 *
 * The server's `age_seconds` plus whatever has passed since the event arrived.
 * See the note on `quotesMeta` for why it is not recomputed from `updated_at`
 * with the browser's clock.
 *
 * @param row - Quote row, of which only `age_seconds` is read.
 * @param receivedAt - When the batch arrived, from {@link useQuotesReceivedAt}.
 *     Null falls back to the server's age alone.
 * @param now - Current timestamp. Injectable so the tests do not need a clock.
 * @return The age in seconds, or null when the server did not report one.
 */
export function realAge(
  row: Pick<QuoteRow, "age_seconds">,
  receivedAt: number | null,
  now: number = Date.now(),
): number | null {
  if (row.age_seconds === null || row.age_seconds === undefined) return null;
  if (receivedAt === null) return row.age_seconds;
  return row.age_seconds + Math.max(0, (now - receivedAt) / 1000);
}
