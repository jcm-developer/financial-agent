import { Link } from "react-router";

import { useProfiles } from "@/api/hooks";
import type { ProfileSummary } from "@/api/types";
import { Block, cardClasses, Card, PageTitle } from "@/components/pieces";
import { Section } from "@/components/Section";
import { useTitle } from "@/layout/useTitle";

/**
 * The experiment list.
 *
 * It is the minimal version that makes the application navigable: without it
 * there is no way to reach a profile. The cards with metrics, the creation form
 * and the duplicate action are F5.2, F5.3 and F5.4, and arrive in stretch D with
 * `shadcn`.
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
          data.length === 0 ? (
            <Card padding="p-6">
              <p className="text-[13px] text-text-secondary">
                No hay ningún experimento todavía. Se crean desde la consola mientras el alta
                de F5.3 no exista:
              </p>
              <Block className="mt-3">
                python run.py new-profile --name europa-01 --market eu --watch 89
              </Block>
            </Card>
          ) : (
            <ul className="flex flex-col gap-2">
              {data.map((row) => (
                <li key={row.id}>
                  <Link
                    to={`/p/${encodeURIComponent(row.name)}/summary`}
                    className={cardClasses(
                      "px-4 py-3",
                      "flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 transition-colors hover:bg-surface-sunken",
                    )}
                  >
                    <span className="font-medium">{row.name}</span>
                    <span className="text-[13px] text-text-secondary">
                      {row.market.toUpperCase()} · {row.llm_model}
                    </span>
                    <span
                      className={
                        row.status === "active"
                          ? "text-xs font-semibold text-delta-good"
                          : "text-xs text-text-muted"
                      }
                    >
                      {row.status}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )
        }
      </Section>
    </>
  );
}
