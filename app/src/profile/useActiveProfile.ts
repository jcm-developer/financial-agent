import { useParams } from "react-router";

import { useProfiles } from "@/api/hooks";
import type { ProfileSummary } from "@/api/types";

/**
 * The profile being looked at, taken from the URL (`/p/:profile/...`).
 *
 * **It lives in the URL and not in a React context** (decision of stretch C):
 * this whole project is about not confusing two experiments, and a selector held
 * in memory is the easiest way to end up looking at the wrong one after a reload
 * or a back button. With the name in the URL, the question "which experiment is
 * this?" is answered by the address bar.
 *
 * It is looked up in the profile list instead of requesting
 * `/api/profiles/:ref` so as not to duplicate the request: the selector already
 * needs the whole list, and this way a profile that does not exist can be told
 * from a network failure without inventing states.
 */
export interface ActiveProfile {
  /** What the URL says. Empty on the routes that carry no profile. */
  ref: string | undefined;
  profile: ProfileSummary | undefined;
  profiles: ProfileSummary[] | undefined;
  loading: boolean;
  error: Error | null;
  /** True once the list has landed and the reference is not in it. */
  notFound: boolean;
}

/**
 * Resolves the profile named in the URL against the loaded profile list.
 *
 * @return The active profile alongside the list, the loading and error state,
 *     and whether the URL names a profile that does not exist.
 */
export function useActiveProfile(): ActiveProfile {
  const { profile: ref } = useParams<{ profile: string }>();
  // Archived ones come along on purpose (F5.4). Archiving takes an experiment
  // out of the *list*, not out of existence: its history is intact and is
  // exactly what it was kept for. Without this, archiving would break every
  // saved link to it and the screen would claim it does not exist, which is a
  // different and false statement. The selector already labels it "(archived)".
  const query = useProfiles(true);

  const found = ref
    ? query.data?.find((row) => row.name === ref || row.id === ref)
    : undefined;

  return {
    ref,
    profile: found,
    profiles: query.data,
    loading: query.isPending,
    error: query.error,
    notFound: Boolean(ref) && query.data !== undefined && found === undefined,
  };
}
