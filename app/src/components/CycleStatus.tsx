import type { CycleRow } from "@/api/types";
import { Tag } from "@/components/pieces";

/**
 * A cycle's status, with the nuance F6.9 added.
 *
 * A `failed` because the analyst got no answer —quota exhausted, provider down—
 * is not the same as a `failed` from the broker or the database, and until F6.9
 * there was no way to tell them apart: the cycle ended `completed` with zero
 * proposals and read as a quiet day. Now the call count is on the row, so it is
 * shown here, which is where it gets looked at.
 *
 * @param props - Status props.
 * @param props.cycle - Cycle row, of which the status, the error and the
 *     analyst call counts are read.
 * @return The status, followed by a tag when the model answered partially or
 *     not at all.
 */
export function CycleStatus({ cycle }: { cycle: CycleRow }) {
  const calls = cycle.analyst_calls ?? 0;
  const failures = cycle.analyst_failures ?? 0;
  const noModel = calls > 0 && failures === calls;

  return (
    <span
      className={
        cycle.status === "failed"
          ? "text-delta-bad"
          : cycle.status === "halted"
            ? "text-warning"
            : "text-text-secondary"
      }
      title={cycle.error ?? undefined}
    >
      {cycle.status}
      {noModel && (
        <Tag title="Ninguna llamada al modelo obtuvo respuesta: este ciclo no analizó nada (F6.9)">
          SIN MODELO
        </Tag>
      )}
      {!noModel && failures > 0 && (
        <Tag
          tone="warning"
          title={`${failures} de ${calls} llamadas al modelo se quedaron sin respuesta: a esos símbolos no se les llegó a mirar`}
        >
          {failures}/{calls} SIN RESPUESTA
        </Tag>
      )}
    </span>
  );
}
