/**
 * Light or dark, chosen by hand and remembered per browser.
 *
 * **The default is light and it does not follow the operating system.** Light
 * is Verdana as written and was the only theme for seven weeks, so a machine set
 * to dark mode opening the app one morning in black would look like a bug, not a
 * feature. Whoever wants dark asks for it once and keeps it.
 *
 * The first paint is not decided here: the inline script in `index.html` reads
 * the same key and sets the attribute before React loads, because doing it from
 * here would flash the light theme on every load for whoever chose dark. That
 * script cannot import this module, so the key is written twice and
 * `theme.test.ts` checks that both copies agree.
 */

export type Theme = "light" | "dark";

/**
 * A new key and not the `theme` the pre-Verdana switch used. That one is still
 * sitting in some browsers with whatever was chosen in August, and reading it
 * would reopen the app in the old choice without anyone having asked for it.
 */
export const THEME_STORAGE_KEY = "colorScheme";

/** The browser chrome's colour for each theme: the page background of each. */
export const THEME_COLOR: Record<Theme, string> = {
  light: "#f8fafc",
  dark: "#020617",
};

/**
 * @param value - Whatever storage returned, which may be anything or nothing.
 * @return The theme it names, light for anything that is not exactly `dark`.
 */
export function parseTheme(value: string | null | undefined): Theme {
  return value === "dark" ? "dark" : "light";
}

/** @return The theme the document is showing now. */
export function currentTheme(): Theme {
  return parseTheme(document.documentElement.dataset.theme);
}

/**
 * Shows a theme and remembers it.
 *
 * Storage can throw —a private window, blocked site data— and the switch must
 * still work for the page in front of the user, so a failed write is dropped
 * and the theme simply does not survive the reload.
 *
 * @param theme - The theme to show.
 */
export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", THEME_COLOR[theme]);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Not persisted; the page still switches.
  }
}
