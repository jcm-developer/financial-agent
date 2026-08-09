import { Badge } from "@/components/pieces";
import type { ProfileSummary } from "@/api/types";

/**
 * What an experiment's four states mean, said in words.
 *
 * The colour never carries it on its own (F4.9): the badge already says the
 * state, and the `title` says what it implies, which is the part that decides
 * whether you have to do something about it. "paused" and "draft" both mean "it
 * is not running", and they are very different problems.
 */
type Status = ProfileSummary["status"];

const LABEL: Record<Status, string> = {
  draft: "borrador",
  active: "activo",
  paused: "pausado",
  archived: "archivado",
};

const MEANING: Record<Status, string> = {
  draft:
    "Creado pero sin activar: el planificador no lo toca y el ingestor no sigue sus símbolos.",
  active: "En marcha: el planificador lanza sus ciclos y el ingestor sigue sus símbolos.",
  paused:
    "Detenido a propósito: conserva su histórico y sus posiciones, pero no corre ningún ciclo.",
  archived: "Retirado del listado. Su histórico sigue entero y se puede volver a activar.",
};

const TONE: Record<Status, string> = {
  // Only the running state is coloured. Painting the other three would turn the
  // list into four colours competing, and the question the list answers is
  // "which one is running", not "how does each one feel".
  draft: "text-text-muted",
  active: "text-delta-good",
  paused: "text-warning",
  archived: "text-text-muted",
};

/**
 * The badge saying whether an experiment is running.
 *
 * @param props - Badge props.
 * @param props.status - The profile's status.
 * @return The rendered badge, carrying the whole sentence in its `title`.
 */
export function ProfileStatus({ status }: { status: Status }) {
  return (
    <Badge compact className={TONE[status]} title={MEANING[status]}>
      {LABEL[status]}
    </Badge>
  );
}

/**
 * What that status means, as a full sentence.
 *
 * Exported so a screen can spell it out where there is room for it, instead of
 * leaving four letters and a `title` nobody hovers.
 *
 * @param status - The profile's status.
 * @return The sentence, in the interface language.
 */
export function statusMeaning(status: Status): string {
  return MEANING[status];
}
