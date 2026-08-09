import { useProfiles } from "@/api/hooks";
import type { ProfileSummary } from "@/api/types";
import { Block, Card, PageTitle } from "@/components/pieces";
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
  const profiles = useProfiles();

  return (
    <>
      <PageTitle>Experimentos</PageTitle>

      <Section query={profiles}>
        {(data: ProfileSummary[]) =>
          data.length === 0 ? <NoProfiles /> : (
            <ul className="flex flex-col gap-3">
              {sorted(data).map((profile) => (
                <li key={profile.id}>
                  <ProfileCard profile={profile} />
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
 * there is nothing wrong: the application has just been installed and the next
 * step is a command. Creating one from the interface is F5.3.
 *
 * @return The rendered empty state.
 */
function NoProfiles() {
  return (
    <Card padding="p-6" dashed>
      <p className="text-[13px] text-text-secondary">
        No hay ningún experimento todavía. Se crean desde la consola mientras el alta de
        F5.3 no exista:
      </p>
      <Block className="mt-3">
        python run.py new-profile --name europa-01 --market eu --watch 89
      </Block>
      <p className="mt-3 text-[13px] text-text-muted">
        El mercado decide horario, calendario, divisa, benchmark y suelo de liquidez, y no
        se puede cambiar después.
      </p>
    </Card>
  );
}
