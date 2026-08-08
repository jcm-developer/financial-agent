import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

type Tema = "dark" | "light";

/**
 * Interruptor de tema, la otra mitad de F4.2.
 *
 * Comparte la clave `tema` de `localStorage` con el script en línea de
 * `index.html`, que es el que evita el fogonazo del tema equivocado al cargar. Si
 * alguien cambia el nombre de la clave en un sitio y no en el otro, el síntoma es
 * que la preferencia deja de recordarse entre recargas sin que nada falle.
 */
const CLAVE = "tema";

function leerTema(): Tema {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

export function BotonTema() {
  const [tema, setTema] = useState<Tema>(leerTema);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", tema === "dark");
    document.documentElement.style.colorScheme = tema;
    try {
      localStorage.setItem(CLAVE, tema);
    } catch {
      /* Modo privado: el tema aplica en esta pestaña y no se recuerda. */
    }
  }, [tema]);

  const siguiente = tema === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      onClick={() => setTema(siguiente)}
      className="flex min-h-8 items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-[13px] text-text-secondary hover:bg-surface-sunken"
      // El icono solo no dice qué va a pasar, y un botón de tema sin etiqueta es
      // adivinanza para un lector de pantalla (F4.9).
      aria-label={siguiente === "dark" ? "Cambiar a tema oscuro" : "Cambiar a tema claro"}
    >
      {tema === "dark" ? <Moon className="size-3.5" /> : <Sun className="size-3.5" />}
      {tema === "dark" ? "Oscuro" : "Claro"}
    </button>
  );
}
