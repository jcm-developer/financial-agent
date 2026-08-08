import { Link, useLocation } from "react-router";

import { clasesBoton, Tarjeta, TituloBloque } from "@/components/piezas";

/**
 * 404 del enrutador, que no es lo mismo que el 404 de la API.
 *
 * Se enseña la ruta pedida a propósito: la API devuelve el index.html en
 * cualquier ruta que no empiece por `/api/` (F3.7), así que un enlace mal escrito
 * llega hasta aquí en lugar de dar un error de servidor. Sin la ruta a la vista,
 * una errata en la URL parece un fallo de la aplicación.
 */
export function NoEncontrado() {
  const { pathname } = useLocation();

  return (
    <Tarjeta relleno="p-6">
      <TituloBloque como="h1" className="text-[15px]">
        Esta página no existe
      </TituloBloque>
      <p className="mt-2 text-[13px] text-text-secondary">
        No hay nada en <code className="text-foreground">{pathname}</code>.
      </p>
      <Link to="/" className={clasesBoton("neutro", "mt-4")}>
        Volver al inicio
      </Link>
    </Tarjeta>
  );
}
