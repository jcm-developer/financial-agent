import { useState } from "react";

import type { DecisionOutcomes, OutcomeCell } from "@/api/types";
import { BlockTitle, Card, LinkButton } from "@/components/pieces";
import { Row, Table, TableHead, Td, Th } from "@/components/Table";
import { integer, percent } from "@/lib/format";

/** Below this, a hit rate is an anecdote; the cell says so. */
const MIN_SAMPLE = 5;

/** How a decision is judged, for the column header's title. */
const RULE =
  "Comprar, ampliar y mantener una posición aciertan si el precio sube; quedarse fuera " +
  "y vender, si no sube. Se mide desde el precio que vio el modelo.";

const EMPTY_OUTCOMES = "Todavía no hay decisiones que juzgar: no ha corrido ningún ciclo.";

/** How each horizon heads its column. */
function horizonLabel(sessions: number): string {
  return sessions === 0 ? "Hasta el último cierre" : `A ${sessions} sesiones`;
}

const ACTION_LABELS: Record<string, string> = {
  buy: "Comprar",
  hold: "Quedarse fuera",
};

/**
 * One group judged at one horizon: the hit rate, how many it rests on, and the
 * mean move.
 *
 * **The mean move is not coloured by sign**, which is the one place in the
 * application where a percentage is not: for the decisions that stayed out, a
 * rise is the model being *wrong*, so green would say the opposite of the truth
 * on half the rows. The hit rate carries the verdict; the move is context.
 *
 * @param props - Cell props.
 * @param props.cell - The group at that horizon.
 * @param props.total - How many decisions the group has, judged or not.
 * @return The rendered cell.
 */
function OutcomeTd({ cell, total }: { cell: OutcomeCell; total: number }) {
  if (cell.judged === 0) {
    return (
      <Td
        numeric
        title={
          cell.sessions === 0
            ? "Ninguna tiene aún un cierre completo detrás."
            : `Ninguna tiene aún ${cell.sessions} sesiones cerradas detrás.`
        }
      >
        <span className="text-text-muted">pendiente</span>
      </Td>
    );
  }
  const few = cell.judged < MIN_SAMPLE;
  return (
    <Td
      numeric
      title={`${cell.right} de ${cell.judged} acertaron${
        cell.judged < total ? `; ${total - cell.judged} sin juzgar todavía` : ""
      }. Movimiento medio del precio desde el que vio el modelo: ${percent(
        cell.avg_return_pct,
        { sign: true },
      )}.`}
    >
      <span className={few ? "text-text-muted" : undefined}>
        {percent(cell.hit_rate_pct)}
      </span>
      <span className="block text-caption text-text-muted">
        {cell.right}/{cell.judged} · {percent(cell.avg_return_pct, { sign: true })}
      </span>
    </Td>
  );
}

/**
 * Whether each decision was right, `hold` included (F9.27).
 *
 * **A table and not a chart**, against the rest of the screen: it is a small
 * grid of rates each resting on a different count, and a bar per cell would draw
 * a 1-of-1 exactly as tall as a 30-of-30. Counts under five are muted.
 *
 * The first column, «hasta el último cierre», is there because the fixed
 * horizons take a week and a month to fill: without it the block would say
 * «pendiente» for the whole first week of every experiment.
 *
 * @param props - Block props.
 * @param props.outcomes - The scorecard from `/api/analytics`.
 * @return The rendered card.
 */
export function Outcomes({ outcomes }: { outcomes: DecisionOutcomes | null | undefined }) {
  const [byConviction, setByConviction] = useState(false);
  const horizons = outcomes?.horizons ?? [];

  return (
    <Card as="section">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-3">
        <BlockTitle>¿Acertó cada decisión?</BlockTitle>
        {outcomes && outcomes.ladder.length > 0 && (
          <LinkButton
            onClick={() => setByConviction((v) => !v)}
            aria-pressed={byConviction}
            className="text-caption"
          >
            {byConviction ? "Ver por acción" : "Ver por convicción"}
          </LinkButton>
        )}
      </div>

      {!outcomes || outcomes.decisions === 0 ? (
        <p className="py-8 text-body-sm text-text-secondary">{EMPTY_OUTCOMES}</p>
      ) : (
        <>
          <p className="mb-4 text-caption font-normal text-text-secondary">
            {integer(outcomes.judged)} de {integer(outcomes.decisions)} decisiones con{" "}
            {horizons.find((h) => h > 0) ?? 5} sesiones cerradas
          </p>

          {byConviction ? (
            <Table title="Acierto de las decisiones de entrada por convicción declarada">
              <TableHead>
                <Th>Convicción</Th>
                <Th>Acción</Th>
                <Th numeric>Decisiones</Th>
                {horizons.map((h) => (
                  <Th key={h} numeric>{horizonLabel(h)}</Th>
                ))}
              </TableHead>
              <tbody>
                {outcomes.ladder.map((rung) => (
                  <Row key={`${rung.bucket}-${rung.action}`}>
                    <Td header>
                      {rung.bucket}–{rung.bucket + 9}
                    </Td>
                    <Td>{ACTION_LABELS[rung.action] ?? rung.action}</Td>
                    <Td numeric>{integer(rung.decisions)}</Td>
                    {rung.horizons.map((cell) => (
                      <OutcomeTd key={cell.sessions} cell={cell} total={rung.decisions} />
                    ))}
                  </Row>
                ))}
              </tbody>
            </Table>
          ) : (
            <Table title="Acierto de las decisiones por acción y plazo">
              <TableHead>
                <Th>
                  <span title={RULE}>Decisión</span>
                </Th>
                <Th numeric>Decisiones</Th>
                {horizons.map((h) => (
                  <Th key={h} numeric>{horizonLabel(h)}</Th>
                ))}
                <Th numeric>Stop / objetivo</Th>
              </TableHead>
              <tbody>
                {outcomes.groups.map((group) => (
                  <Row key={`${group.kind}-${group.action}`}>
                    <Td header>{group.label}</Td>
                    <Td numeric>{integer(group.decisions)}</Td>
                    {group.horizons.map((cell) => (
                      <OutcomeTd key={cell.sessions} cell={cell} total={group.decisions} />
                    ))}
                    <Td
                      numeric
                      title="Compras con niveles: cuántas tocaron antes el stop y cuántas el objetivo."
                    >
                      {group.action === "buy" && group.kind === "entry"
                        ? `${group.stops ?? 0} / ${group.targets ?? 0}`
                        : "—"}
                    </Td>
                  </Row>
                ))}
              </tbody>
            </Table>
          )}
        </>
      )}
    </Card>
  );
}
