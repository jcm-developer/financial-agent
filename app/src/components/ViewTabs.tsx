import { Link } from "react-router";

import { cn } from "@/lib/utils";

/**
 * The views of one screen, as a row of links that write `?view=`.
 *
 * **Links and not `role="tab"` buttons**, because each view is an address: a
 * rejected proposal is worth sending to someone as `…/decisions?view=risk`, and
 * the back button has to undo a change of view like it undoes a change of
 * screen. A tablist would keep the view in React state and lose both.
 *
 * The first view is the screen's default and carries no parameter, so the plain
 * address of the screen and the first tab are the same page and not two.
 *
 * `aria-current` carries which one is open for a screen reader; the fill is the
 * same navy-over-white as the active filter chip, so it is never colour alone.
 *
 * @param props - Tabs props.
 * @param props.label - What the group of views is, for the `<nav>`'s name.
 * @param props.views - `[key, text]` pairs, the default first.
 * @param props.current - The key of the view on screen.
 * @return The rendered row of links.
 */
export function ViewTabs({
  label,
  views,
  current,
}: {
  label: string;
  views: readonly (readonly [string, string])[];
  current: string;
}) {
  const [fallback] = views[0] ?? [""];

  return (
    <nav aria-label={label} className="mb-6 flex flex-wrap gap-1 rounded-md border border-border bg-card p-1 sm:w-fit">
      {views.map(([key, text]) => {
        const active = key === current;
        return (
          <Link
            key={key}
            to={key === fallback ? "." : `?view=${encodeURIComponent(key)}`}
            replace
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex-1 rounded-sm px-4 py-1.5 text-center text-body-sm whitespace-nowrap transition-colors duration-150 sm:flex-none",
              active
                ? "bg-primary font-medium text-primary-foreground"
                : "text-text-secondary hover:bg-surface-sunken hover:text-foreground",
            )}
          >
            {text}
          </Link>
        );
      })}
    </nav>
  );
}

/**
 * Reads the view out of the query string, falling back to the default.
 *
 * An unknown `?view=` —a typo, a view that was renamed— shows the default
 * instead of an empty screen: the address still names the right screen, and
 * that is the part worth honouring.
 *
 * @param search - The raw `view` parameter, or null.
 * @param views - The screen's `[key, text]` pairs, the default first.
 * @return The key to render.
 */
export function pickView(
  search: string | null,
  views: readonly (readonly [string, string])[],
): string {
  const keys = views.map(([key]) => key);
  return search && keys.includes(search) ? search : (keys[0] ?? "");
}
