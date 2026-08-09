import { Navigate } from "react-router";

import { useProfiles } from "@/api/hooks";
import { Loading } from "@/components/pieces";
import { ErrorAlert } from "@/components/Section";

/**
 * The root decides where to go depending on what there is.
 *
 * **With a single active experiment you go straight into it**, which is decision
 * nº 5 of the plan: experiments are run one at a time, so always going through
 * the list would be a toll click on every visit. With several active, or with
 * none, the list is the right answer.
 *
 * `replace` so the redirect is not left in the history: without it, the back
 * button bounces between the root and the summary.
 *
 * @return A redirect to the only active experiment, or to the profile list.
 */
export function Home() {
  const { data, isPending, error } = useProfiles();

  if (isPending) return <Loading />;
  if (error) return <ErrorAlert error={error} />;

  const active = (data ?? []).filter((row) => row.status === "active");
  if (active.length === 1) {
    return <Navigate to={`/p/${encodeURIComponent(active[0]!.name)}/summary`} replace />;
  }
  return <Navigate to="/profiles" replace />;
}
