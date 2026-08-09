import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merges Tailwind class names, letting later classes win over earlier ones.
 *
 * The helper every component copied from the shadcn/ui CLI expects.
 *
 * @param inputs - Class values, including conditional objects and arrays.
 * @return The merged class string, free of conflicting Tailwind utilities.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
