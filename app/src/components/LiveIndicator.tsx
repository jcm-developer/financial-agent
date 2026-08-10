import { Chip, type ChipVariant } from "@/components/pieces";
import { Tooltip } from "@/components/Tooltip";
import { cn } from "@/lib/utils";
import type { StreamState } from "@/api/stream";

/**
 * "Live data / disconnected" (F4.5).
 *
 * Three states and not two, because lumping "reconnecting" in with
 * "disconnected" would lie in the bad direction: the server **retires
 * connections every 15 minutes on purpose** (F3.5) and `EventSource` restores
 * them on its own, so a two-state indicator would flash red every quarter of an
 * hour on a perfectly healthy connection. After three quarters nobody believes
 * it, and then they do not believe the real red either.
 */

const APPEARANCE: Record<StreamState, { text: string; variant: ChipVariant }> = {
  live: { text: "datos en vivo", variant: "success" },
  connecting: { text: "reconectando…", variant: "warning" },
  disconnected: { text: "desconectado", variant: "error" },
};

interface Props {
  state: StreamState;
  reconnections?: number;
  notice?: string | null;
}

/**
 * The stream's connection state, as a badge in the header.
 *
 * @param props - Indicator props.
 * @param props.state - Connection state, which decides text and colour.
 * @param props.reconnections - How many times it has reconnected, shown in the
 *     tooltip so a connection that keeps dropping is visible.
 * @param props.notice - Last notice the server sent before cutting.
 * @return The rendered badge.
 */
export function LiveIndicator({ state, reconnections = 0, notice }: Props) {
  const { text, variant } = APPEARANCE[state];

  return (
    // Colour cannot be the only carrier of meaning: the chip already says the
    // state in words, and the tooltip adds the detail for whoever needs it.
    <Tooltip
      content={
        notice
          ? `${text} — último aviso del servidor: ${notice}`
          : reconnections
            ? `${text} — ${reconnections} reconexiones`
            : text
      }
    >
      <Chip variant={variant}>
        <span
          aria-hidden
          className={cn(
            "size-1.5 rounded-full bg-current",
            state === "live" && "animate-pulse",
          )}
        />
        {text}
      </Chip>
    </Tooltip>
  );
}
