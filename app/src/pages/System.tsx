import { useSearchParams } from "react-router";

import { PageTitle } from "@/components/pieces";
import { pickView, ViewTabs } from "@/components/ViewTabs";
import { useTitle } from "@/layout/useTitle";
import { Database } from "@/pages/Database";
import { Diagnostics } from "@/pages/Diagnostics";

/** The two views of the screen, the default first. */
const VIEWS = [
  ["ingest", "Ingesta"],
  ["database", "Base de datos"],
] as const;

/**
 * Ingesta and Base de datos, as two views of one screen.
 *
 * Both are **diagnosis and not experiment**: whether the prices are arriving,
 * and what the file holds. Neither depends on the profile being looked at, and
 * neither is opened on a normal day — they are where you go when something on
 * the other screens looks wrong. Two sidebar entries for that gave them the
 * same weight as Resumen.
 *
 * The old addresses, `/diagnostics` and `/database`, redirect here with their
 * view.
 *
 * @return The rendered screen.
 */
export function System() {
  const [params] = useSearchParams();
  const view = pickView(params.get("view"), VIEWS);
  useTitle(view === "database" ? "Base de datos" : "Ingesta");

  return (
    <>
      <PageTitle>Sistema</PageTitle>
      <ViewTabs label="Vistas del sistema" views={VIEWS} current={view} className="mb-6" />
      {view === "database" ? <Database /> : <Diagnostics />}
    </>
  );
}
