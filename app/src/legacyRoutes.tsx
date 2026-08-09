import { Navigate, useLocation } from "react-router";

/**
 * The routes F8.8 renamed, kept alive so a link saved before the migration still
 * lands where it used to (F8.10).
 *
 * **These ten names in Spanish are a deliberate exception to the language
 * convention**, and the only one in `app/`. They are not code that got left
 * behind: they are state the browser remembers —bookmarks, history, a link
 * pasted into a note— and that state was written before F8.8 moved the code to
 * English. The exception is written down in TASKS.md as well as here, because a
 * compatibility layer is not something anyone retires on their own initiative.
 * If it ever goes, it goes whole and at once, the same way the migration did.
 *
 * What happened before this existed, checked against the running application
 * rather than assumed: the server answers 200 with `index.html` —the SPA
 * fallback of F3.7 does not know the router's routes— and the router falls
 * through to the catch-all, so `NotFound` paints. It names the route and offers
 * the way home, so it is not a blank page; it is a dead end.
 */

/** The eight sections under `/p/:profile/`, old name to new. */
const PROFILE_ROUTES: Record<string, string> = {
  resumen: "summary",
  analitica: "analytics",
  posiciones: "positions",
  decisiones: "decisions",
  ordenes: "orders",
  riesgo: "risk",
  ciclos: "cycles",
  ajustes: "settings",
};

/** The two routes that hang off the root, old name to new. */
const TOP_ROUTES: Record<string, string> = {
  perfiles: "profiles",
  diagnostico: "diagnostics",
};

/**
 * Query parameters that were renamed along with their screen.
 *
 * `?ciclo=` is the only one so far, and leaving it out would have made the
 * redirect look right and behave wrong: the link to one specific cycle would
 * reach the cycles screen with no detail unfolded, which is precisely the part
 * of the link that carried the information.
 */
const QUERY_PARAMS: Record<string, string> = {
  ciclo: "cycle",
};

/** Every old path that has to be registered, so the routes cannot drift from the tables. */
export const LEGACY_PROFILE_PATHS = Object.keys(PROFILE_ROUTES);

/** Every old root-level path that has to be registered. */
export const LEGACY_TOP_PATHS = Object.keys(TOP_ROUTES);

/**
 * Renames the query parameters that travelled with a renamed screen.
 *
 * @param search - The query string as it arrived, leading `?` included.
 * @return The query string with the renamed keys, or empty when there was none.
 */
export function translateSearch(search: string): string {
  if (!search || search === "?") return "";

  const translated = new URLSearchParams();
  for (const [key, value] of new URLSearchParams(search)) {
    translated.append(QUERY_PARAMS[key] ?? key, value);
  }

  const text = translated.toString();
  return text ? `?${text}` : "";
}

/**
 * Where a pre-F8.8 address ends up today.
 *
 * It builds an **absolute** path instead of a relative one, and that is the
 * whole reason this is a function and not a `to=".."` written into each route:
 * inside `p/:profile` a relative `../positions` resolves by route and gives
 * `/p/:profile/positions`, but the same string with `relative="path"` resolves
 * over the URL segment and quietly gives `/p/positions`. Two spellings that look
 * identical and land in different places is the kind of thing a test has to
 * pin down rather than a reader.
 *
 * @param pathname - The path being visited, without the query string.
 * @param search - The query string, leading `?` included.
 * @return The address to redirect to, or null when the path is not a renamed one.
 */
export function legacyTarget(pathname: string, search = ""): string | null {
  const segments = pathname.split("/").filter(Boolean);
  const query = translateSearch(search);

  const [first, second, third] = segments;

  if (segments.length === 1 && first !== undefined) {
    const renamed = TOP_ROUTES[first];
    return renamed ? `/${renamed}${query}` : null;
  }

  if (segments.length === 3 && first === "p" && second !== undefined && third !== undefined) {
    const renamed = PROFILE_ROUTES[third];
    // `second` is left exactly as it arrived: it is the profile name, already
    // percent-encoded by whoever wrote the link, and re-encoding it here would
    // turn a `%20` into a `%2520`.
    return renamed ? `/p/${second}/${renamed}${query}` : null;
  }

  return null;
}

/**
 * Sends a pre-F8.8 address to its current one.
 *
 * `replace` so the jump does not land in the history: without it the back button
 * bounces between the old route and the new one, which is the same reason `Home`
 * already uses it.
 *
 * @return The redirect, or the 404 route when the path turned out not to be a
 *     renamed one — which cannot happen through the registered routes, and is
 *     here so a wrong registration fails visibly instead of redirecting to
 *     `/undefined`.
 */
export function LegacyRedirect() {
  const { pathname, search } = useLocation();
  const target = legacyTarget(pathname, search);

  return <Navigate to={target ?? "/404"} replace />;
}
