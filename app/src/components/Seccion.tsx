import type { ReactNode } from "react";

/**
 * Los tres estados de F4.8 en un sitio: cargando, error y datos.
 *
 * Existe porque la alternativa —`datos?.map(...)` en cada pantalla— pinta un
 * error de la API como una sección en blanco, y una sección en blanco se lee como
 * «no hay nada», que es una afirmación distinta y en un experimento de diez días
 * es la diferencia entre «hoy no operó» y «llevo tres días sin ver los datos».
 *
 * El caso vacío no está aquí: «no hay posiciones» y «no hay decisiones» se
 * explican de formas distintas, así que lo redacta cada pantalla.
 */
interface Props<T> {
  titulo?: string;
  consulta: { data?: T; error: Error | null; isPending: boolean };
  children: (datos: T) => ReactNode;
}

export function Seccion<T>({ titulo, consulta, children }: Props<T>) {
  return (
    <section className="mb-8">
      {titulo && (
        <h2 className="mb-3 text-[13px] font-semibold tracking-wide text-text-secondary uppercase">
          {titulo}
        </h2>
      )}
      {consulta.isPending && <p className="text-[13px] text-text-muted">Cargando…</p>}
      {consulta.error && <AvisoDeError error={consulta.error} />}
      {consulta.data !== undefined && children(consulta.data)}
    </section>
  );
}

export function AvisoDeError({ error }: { error: Error }) {
  return (
    <p className="rounded-md border border-negative/40 bg-card p-3 text-[13px] text-negative-ink">
      {error.message}
      <br />
      <span className="text-text-muted">
        En desarrollo hace falta la API escuchando: <code>python run.py api</code>
      </span>
    </p>
  );
}
