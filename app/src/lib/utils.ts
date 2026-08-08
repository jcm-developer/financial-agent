import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merges Tailwind class names, letting later classes win over earlier ones.
 *
 * El helper que espera shadcn/ui en cada componente que copia su CLI.
 *
 * @param inputs - Class values, including conditional objects and arrays.
 * @return The merged class string, free of conflicting Tailwind utilities.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
