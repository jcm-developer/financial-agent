import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

import { Boton } from "@/components/piezas";

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
    <Boton
      variante="sutil"
      icono={tema === "dark" ? Moon : Sun}
      onClick={() => setTema(siguiente)}
      // El icono solo no dice qué va a pasar, y un botón de tema sin etiqueta es
      // adivinanza para un lector de pantalla (F4.9).
      aria-label={siguiente === "dark" ? "Cambiar a tema oscuro" : "Cambiar a tema claro"}
    >
      {tema === "dark" ? "Oscuro" : "Claro"}
    </Boton>
  );
}
