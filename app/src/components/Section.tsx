import type { ReactNode } from "react";

import { Alert, Loading, SectionTitle } from "@/components/pieces";

/**
 * The three states of F4.8 in one place: loading, error and data.
 *
 * It exists because the alternative —`data?.map(...)` in every screen— paints an
 * API error as a blank section, and a blank section reads as "there is nothing",
 * which is a different claim, and in a ten-day experiment it is the difference
 * between "it did not trade today" and "I have not seen the data for three days".
 *
 * The empty case is not here: "no positions" and "no decisions" are explained in
 * different ways, so each screen words it itself.
 */
interface Props<T> {
  title?: string;
  query: { data?: T; error: Error | null; isPending: boolean };
  children: (data: T) => ReactNode;
}

/**
 * Renders a query's three states, so a failure never reads as an empty section.
 *
 * @template T - Shape of the query's data.
 * @param props - Section props.
 * @param props.title - Optional heading for the block.
 * @param props.query - The query, of which only data, error and pending are read.
 * @param props.children - Called with the data once it has landed.
 * @return The rendered section: the loading notice, the error, or the children.
 */
export function Section<T>({ title, query, children }: Props<T>) {
  return (
    <section className="mb-8">
      {title && <SectionTitle className="mb-3">{title}</SectionTitle>}
      {query.isPending && <Loading />}
      {query.error && <ErrorAlert error={query.error} />}
      {query.data !== undefined && children(query.data)}
    </section>
  );
}

/**
 * The alert shown when a query fails, with the hint that most often explains it.
 *
 * @param props - Alert props.
 * @param props.error - The error, whose message is already written for the screen.
 * @return The rendered alert.
 */
export function ErrorAlert({ error }: { error: Error }) {
  return (
    <Alert>
      {error.message}
      <br />
      <span className="text-text-muted">
        En desarrollo hace falta la API escuchando: <code>python run.py api</code>
      </span>
    </Alert>
  );
}
