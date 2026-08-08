import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** El helper que espera shadcn/ui en cada componente que copia su CLI. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
