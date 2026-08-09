import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

import { Button } from "@/components/pieces";

type Theme = "dark" | "light";

/**
 * The theme switch, the other half of F4.2.
 *
 * It shares the `theme` key in `localStorage` with the inline script in
 * `index.html`, which is what avoids the flash of the wrong theme on load. If
 * someone changes the key's name in one place and not the other, the symptom is
 * that the preference stops being remembered across reloads without anything
 * failing.
 *
 * It was `tema` until the F8.8 sweep was finished off: a `localStorage` key is
 * code, like the JSON keys of `BarCache.stats()`. The inline script still reads
 * the old one as a fallback, so nobody's saved light theme flips to dark on the
 * next load.
 */
const KEY = "theme";

/**
 * Reads the theme already applied to the document.
 *
 * It is read from the DOM and not from `localStorage` because the inline script
 * in `index.html` already decided, and asking twice opens the door to the two
 * answers disagreeing.
 *
 * @return The theme in force.
 */
function readTheme(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

/**
 * The theme switch, which wins over the system preference in both directions.
 *
 * @return The rendered button.
 */
export function ThemeButton() {
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.style.colorScheme = theme;
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* Private mode: the theme applies in this tab and is not remembered. */
    }
  }, [theme]);

  const next = theme === "dark" ? "light" : "dark";

  return (
    <Button
      variant="subtle"
      icon={theme === "dark" ? Moon : Sun}
      onClick={() => setTheme(next)}
      // The icon alone does not say what will happen, and a theme button with no
      // label is guesswork for a screen reader (F4.9).
      aria-label={next === "dark" ? "Cambiar a tema oscuro" : "Cambiar a tema claro"}
    >
      {theme === "dark" ? "Oscuro" : "Claro"}
    </Button>
  );
}
