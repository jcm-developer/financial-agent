import { Link, useLocation } from "react-router";

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
    <div className="rounded-lg border border-border bg-card p-6">
      <h1 className="text-[15px] font-semibold">Esta página no existe</h1>
      <p className="mt-2 text-[13px] text-text-secondary">
        No hay nada en <code className="text-foreground">{pathname}</code>.
      </p>
      <Link
        to="/"
        className="mt-4 inline-block rounded-md border border-border bg-card px-3 py-1.5 text-[13px] hover:bg-surface-sunken"
      >
        Volver al inicio
      </Link>
    </div>
  );
}
