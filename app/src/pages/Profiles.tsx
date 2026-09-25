import { useState } from "react";

import { useProfiles } from "@/api/hooks";
import type { ProfileSummary } from "@/api/types";
import { NewProfileForm } from "@/components/NewProfileForm";
import { Checkbox } from "@/components/Checkbox";
import { Button, Card, PageTitle } from "@/components/pieces";
import { ProfileActions } from "@/components/ProfileActions";
import { ProfileCard } from "@/components/ProfileCard";
import { Section } from "@/components/Section";
import { useTitle } from "@/layout/useTitle";

/**
 * The experiment list, in cards with the figures that matter.
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

      {/* Archiving takes an experiment out of this list; without a way back the
          button would look like it deleted something. */}
      <Checkbox
        className="mb-6"
        checked={showArchived}
        onChange={(e) => setShowArchived(e.target.checked)}
        label="Mostrar archivados"
      />

      <Section query={profiles}>
        {(data: ProfileSummary[]) =>
          data.length === 0 ? <NoProfiles onCreate={() => setCreating(true)} /> : (
            <ul className="flex flex-col gap-4">
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
 * sees, so it carries the button to create one.
 *
 * @param props - Empty-state props.
 * @param props.onCreate - Opens the creation form.
 * @return The rendered empty state.
 */
function NoProfiles({ onCreate }: { onCreate: () => void }) {
  return (
    <Card padding="p-6" dashed className="flex flex-wrap items-center justify-between gap-4">
      <p className="text-body-sm text-text-secondary">Todavía no hay ningún experimento.</p>
      <Button variant="primary" onClick={onCreate}>
        Crear el primero
      </Button>
    </Card>
  );
}
