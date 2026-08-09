import { useEffect } from "react";

/**
 * The document title, per screen.
 *
 * In a single-page application the title does not change on its own, and that
 * breaks two things: a screen reader announces the same thing on every
 * navigation —so there is no way to know the page changed— and with several tabs
 * of the same experiment open they are all called the same.
 *
 * It carries the profile name when there is one, which is the same reason the
 * profile lives in the URL: knowing which experiment you are looking at without
 * having to work it out.
 *
 * @param section - Screen name, which leads the title.
 * @param profile - Active profile name. Omitted on the screens without one.
 */
export function useTitle(section: string, profile?: string) {
  useEffect(() => {
    document.title = [section, profile, "financial-agent"].filter(Boolean).join(" · ");
  }, [section, profile]);
}
