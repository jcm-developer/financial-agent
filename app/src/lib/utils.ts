import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * The type scale's utility names, which `tailwind-merge` has to be told about.
 *
 * ⚠️ **Without this it silently deletes colours.** `tailwind-merge` classifies an
 * unknown `text-*` class by guessing, and it guesses *colour* for
 * `text-body-sm`: merging `text-primary-foreground` with `text-body-sm` then
 * looks like two colours competing, the later one wins, and the first
 * disappears. The symptom is a navy button with no label on it — navy text on a
 * navy fill — and nothing fails, neither the typecheck nor the build.
 *
 * It cost a screenshot to find, so it is written down here: every size added to
 * the `@theme` block in `index.css` has to be added to this list too.
 */
const FONT_SIZES = [
  "display",
  "h1",
  "h2",
  "h3",
  "h4",
  "body-lg",
  "body",
  "body-sm",
  "caption",
  "code",
] as const;

const merge = extendTailwindMerge({
  extend: {
    classGroups: {
      // The group's name is what makes it conflict with itself and with nothing
      // else: two sizes cancel each other, a size and a colour do not.
      "font-size": [{ text: [...FONT_SIZES] }],
    },
  },
});

/**
 * Merges Tailwind class names, letting later classes win over earlier ones.
 *
 * The helper every component copied from the shadcn/ui CLI expects.
 *
 * @param inputs - Class values, including conditional objects and arrays.
 * @return The merged class string, free of conflicting Tailwind utilities.
 */
export function cn(...inputs: ClassValue[]) {
  return merge(clsx(inputs));
}
