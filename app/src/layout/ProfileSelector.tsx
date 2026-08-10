import { useLocation, useNavigate } from "react-router";

import { Select } from "@/components/Select";
import { useActiveProfile } from "@/profile/useActiveProfile";

/**
 * Global profile selector (F5.5).
 *
 * Changing profile **navigates**, keeping the section: if you are on
 * `/p/europa-01/positions` and pick another experiment you end up on
 * `/p/other/positions`, not back at the summary. Comparing the same screen
 * across two experiments is the gesture F5.6 calls central, and sending you home
 * on every jump would turn it into four clicks.
 *
 * The dropdown is the shared `<Select>` (`components/pieces.tsx`), in its
 * label-to-the-left variant: there is no height to spend in the header.
 *
 * @return The rendered select, or null while there are no profiles to choose from.
 */
export function ProfileSelector() {
  const { ref, profiles } = useActiveProfile();
  const navigate = useNavigate();
  const location = useLocation();

  if (!profiles?.length) return null;

  const section = ref
    ? location.pathname.split("/").slice(3).join("/") || "summary"
    : "summary";

  // The name is unique and it is what travels in the URL, so it serves as both
  // value and key.
  const options: [string, string][] = [
    ...(ref ? [] : ([["", "— elige uno —"]] as [string, string][])),
    ...profiles.map(
      (row): [string, string] => [
        row.name,
        row.status === "active" ? row.name : `${row.name} (${row.status})`,
      ],
    ),
  ];

  return (
    <Select
      row
      label="Experimento"
      options={options}
      value={ref ?? ""}
      onChange={(name) => navigate(`/p/${encodeURIComponent(name)}/${section}`)}
    />
  );
}
