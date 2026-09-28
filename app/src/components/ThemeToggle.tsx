import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

import { Button } from "@/components/pieces";
import { applyTheme, currentTheme, parseTheme, THEME_STORAGE_KEY, type Theme } from "@/lib/theme";

/**
 * The light/dark switch in the header.
 *
 * One button that names the theme it goes to, not a two-position control: with
 * only two states, the label of the other one is the whole instruction, and it
 * fits the header next to the live indicator without a second row.
 *
 * It listens to `storage` so a second open tab follows the first one. Without
 * that, two tabs on different themes would each save their own on the next
 * click and the one that wins would be whichever was clicked last.
 *
 * @return The rendered button.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== THEME_STORAGE_KEY) return;
      const next = parseTheme(event.newValue);
      applyTheme(next);
      setTheme(next);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const next: Theme = theme === "dark" ? "light" : "dark";

  return (
    <Button
      variant="ghost"
      size="sm"
      icon={next === "dark" ? Moon : Sun}
      title={next === "dark" ? "Cambiar al tema oscuro" : "Cambiar al tema claro"}
      onClick={() => {
        applyTheme(next);
        setTheme(next);
      }}
    >
      {next === "dark" ? "Oscuro" : "Claro"}
    </Button>
  );
}
