import { useLocation, useNavigate } from "react-router";

import { Select } from "@/components/Select";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { profileStatusLabel } from "@/lib/labels";

/**
 * Global profile selector.
 *
 * Changing profile **navigates**, keeping the section: if you are on
 * `/p/europa-01/positions` and pick another experiment you end up on
 * `/p/other/positions`, not back at the summary: comparing the same screen
 * across two experiments is a central gesture.
 *
 * The dropdown is the shared `<Select>` (`components/Select.tsx`), with its
 * label above and the trigger at the sidebar's full width: it heads the group of
 * links it scopes, so it takes the width of those links.
 *
 * @param props - Selector props.
 * @param props.className - Classes for the wrapping block.
 * @return The rendered select, or null while there are no profiles to choose from.
 */
export function ProfileSelector({ className }: { className?: string }) {
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
    ...(ref ? [] : ([["", "Elegir experimento"]] as [string, string][])),
    ...profiles.map(
      (row): [string, string] => [
        row.name,
        row.status === "active" ? row.name : `${row.name} (${profileStatusLabel(row.status)})`,
      ],
    ),
  ];

  return (
    <Select
      label="Experimento"
      fieldClass={className}
      className="w-full"
      options={options}
      value={ref ?? ""}
      onChange={(name) => navigate(`/p/${encodeURIComponent(name)}/${section}`)}
    />
  );
}
