import type { ReactNode } from "react";

import { Aviso, Cargando, TituloSeccion } from "@/components/piezas";

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

/**
 * Renders a query's three states, so a failure never reads as an empty section.
 *
 * @template T - Shape of the query's data.
 * @param props - Section props.
 * @param props.titulo - Optional heading for the block.
 * @param props.consulta - The query, of which only data, error and pending are read.
 * @param props.children - Called with the data once it has landed.
 * @return The rendered section: the loading notice, the error, or the children.
 */
export function Seccion<T>({ titulo, consulta, children }: Props<T>) {
  return (
    <section className="mb-8">
      {titulo && <TituloSeccion className="mb-3">{titulo}</TituloSeccion>}
      {consulta.isPending && <Cargando />}
      {consulta.error && <AvisoDeError error={consulta.error} />}
      {consulta.data !== undefined && children(consulta.data)}
    </section>
  );
}

/**
 * The alert shown when a query fails, with the hint that most often explains it.
 *
 * @param props - Alert props.
 * @param props.error - The error, whose message is already written for the screen.
 * @return The rendered alert.
 */
export function AvisoDeError({ error }: { error: Error }) {
  return (
    <Aviso>
      {error.message}
      <br />
      <span className="text-text-muted">
        En desarrollo hace falta la API escuchando: <code>python run.py api</code>
      </span>
    </Aviso>
  );
}
