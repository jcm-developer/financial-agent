import { Chip, type ChipVariant } from "@/components/pieces";
import { Tooltip } from "@/components/Tooltip";
import type { ProfileSummary } from "@/api/types";
import { profileStatusLabel } from "@/lib/labels";

/**
 * What an experiment's four states mean, said in words.
 *
 * The colour never carries it on its own: the chip says the state, and the
 * tooltip says what it implies. "paused" and "draft" both mean "it is not
 * running", and they are very different problems.
 */
type Status = ProfileSummary["status"];

const MEANING: Record<Status, string> = {
  draft: "Creado pero sin activar: no corre ciclos.",
  active: "En marcha: corre sus ciclos a las horas fijadas.",
  paused: "Detenido: conserva su histórico y sus posiciones.",
  archived: "Retirado del listado; su histórico se conserva.",
};

const TONE: Record<Status, ChipVariant> = {
  // Only the two states that ask something of you take a hue. Tinting all four
  // would turn the list into four colours competing, and the question the list
  // answers is "which one is running", not "how does each one feel".
  draft: "neutral",
  active: "success",
  paused: "warning",
  archived: "neutral",
};

/**
 * The chip saying whether an experiment is running.
 *
 * @param props - Chip props.
 * @param props.status - The profile's status.
 * @return The rendered chip, with the whole sentence in its tooltip.
 */
export function ProfileStatus({ status }: { status: Status }) {
  return (
    <Tooltip content={MEANING[status]}>
      <Chip variant={TONE[status]}>{profileStatusLabel(status)}</Chip>
    </Tooltip>
  );
}
