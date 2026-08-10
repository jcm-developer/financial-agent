import { useState } from "react";

import { useProfiles } from "@/api/hooks";
import type { ProfileSummary } from "@/api/types";
import { NewProfileForm } from "@/components/NewProfileForm";
import { Checkbox } from "@/components/Checkbox";
import { Block, Button, Card, PageTitle } from "@/components/pieces";
import { ProfileActions } from "@/components/ProfileActions";
import { ProfileCard } from "@/components/ProfileCard";
import { Section } from "@/components/Section";
import { useTitle } from "@/layout/useTitle";

/**
 * The experiment list, in cards with the figures that matter (F5.2).
 *
 * **The order is deliberate and not the API's.** `/api/profiles` returns them by
 * creation date, which after a few weeks buries the running one under three
 * drafts. Here the active ones come first, then paused, then drafts, then
 * archived, and within each group the most recently updated. The question this
 * screen answers is "which experiment is alive", so that is what the top of the
 * list has to hold.
 *
 * @return The rendered screen.
 */
export function Profiles() {
  useTitle("Experimentos");
  const [showArchived, setShowArchived] = useState(false);
  const [creating, setCreating] = useState(false);
  const profiles = useProfiles(showArchived);

  return (
    <>
      <PageTitle
        aside={
          !creating && (
            <Button onClick={() => setCreating(true)}>Nuevo experimento</Button>
          )
        }
      >
        Experimentos
      </PageTitle>

      {creating && (
        <div className="mb-8">
          <NewProfileForm onCancel={() => setCreating(false)} />
        </div>
      )}

      {/* The toggle goes with the archive action, not after it: archiving takes
          an experiment out of this list, and without a way back the button would
          look like it deleted something. */}
      <Checkbox
        className="mb-6"
        checked={showArchived}
        onChange={(e) => setShowArchived(e.target.checked)}
        label="Ver también los archivados"
      />

      <Section query={profiles}>
        {(data: ProfileSummary[]) =>
          data.length === 0 ? <NoProfiles onCreate={() => setCreating(true)} /> : (
            <ul className="flex flex-col gap-3">
              {sorted(data).map((profile) => (
                <li key={profile.id}>
                  <ProfileCard
                    profile={profile}
                    actions={<ProfileActions profile={profile} />}
                  />
                </li>
              ))}
            </ul>
          )
        }
      </Section>
    </>
  );
}

/** Where each status sits in the list. Lower comes first. */
const STATUS_ORDER: Record<ProfileSummary["status"], number> = {
  active: 0,
  paused: 1,
  draft: 2,
  archived: 3,
};

/**
 * Sorts the experiments by whether they are running, then by how recently they
 * changed.
 *
 * @param profiles - The list as the API returned it.
 * @return A new sorted array; the argument is left untouched because it is the
 *     query cache's own object and mutating it would reorder it under React.
 */
function sorted(profiles: ProfileSummary[]): ProfileSummary[] {
  return [...profiles].sort((a, b) => {
    const byStatus = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
    if (byStatus !== 0) return byStatus;
    return b.updated_at.localeCompare(a.updated_at);
  });
}

/**
 * The empty state, which on this screen is the first thing a new installation
 * sees.
 *
 * It is worded as instructions and not as "no hay nada" because at this point
 * nothing is wrong: the application has just been installed and the next step is
 * to create something. The console command stays alongside the button because it
 * is the one that works before the interface is reachable —a fresh clone, a
 * container that has not come up— and it is what the README documents.
 *
 * @param props - Empty-state props.
 * @param props.onCreate - Opens the creation form.
 * @return The rendered empty state.
 */
function NoProfiles({ onCreate }: { onCreate: () => void }) {
  return (
    <Card padding="p-6" dashed>
      <p className="text-body-sm text-text-secondary">
        No hay ningún experimento todavía. Un experimento es un mercado, un capital, un
        criterio de riesgo y un modelo; todo lo demás se mide contra eso.
      </p>
      <div className="mt-4">
        <Button onClick={onCreate}>Crear el primero</Button>
      </div>
      <p className="mt-4 text-body-sm text-text-muted">O desde la consola:</p>
      <Block className="mt-2">
        python run.py new-profile --name europa-01 --market eu --watch 89
      </Block>
    </Card>
  );
}
