import type { ReactNode } from "react";

import { Boton, Tarjeta } from "@/components/piezas";
import { cn } from "@/lib/utils";

/**
 * Tabla de datos, escrita a mano en vez de traída de shadcn/ui.
 *
 * La tabla de shadcn son envoltorios sobre `<table>` sin nada de Radix debajo, así
 * que copiarla aportaría un fichero más y ninguna capacidad; y estas ya tienen que
 * llevar la paleta heredada y `tabular-nums` en las columnas de cifras. shadcn se
 * traerá cuando haga falta algo que sí necesita Radix: el diálogo de confirmación
 * de F5.4 y los avisos.
 *
 * `overflow-x-auto` en el contenedor y no en la página: una tabla ancha tiene que
 * desplazarse dentro de su hueco, sin arrastrar el resto de la pantalla (F4.9).
 */
export function Tabla({
  titulo,
  children,
  className,
}: {
  /** Descripción para lectores de pantalla. */
  titulo: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Tarjeta relleno="p-0" className="overflow-x-auto">
      <table className={cn("w-full text-[13px]", className)}>
        <caption className="sr-only">{titulo}</caption>
        {children}
      </table>
    </Tarjeta>
  );
}

export function Th({
  children,
  numerica = false,
  className,
}: {
  children: ReactNode;
  numerica?: boolean;
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={cn(
        "px-3 py-2 font-medium whitespace-nowrap text-text-muted",
        numerica ? "text-right" : "text-left",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  numerica = false,
  encabezado = false,
  className,
  title,
}: {
  children: ReactNode;
  numerica?: boolean;
  /**
   * La celda que nombra la fila, como `<th scope="row">`.
   *
   * No es decoración: en una tabla de cotizaciones, sin la cabecera de fila un
   * lector de pantalla lee «17,42» sin decir de qué símbolo, y la columna del
   * símbolo es justo la que da sentido a las otras tres.
   */
  encabezado?: boolean;
  className?: string;
  title?: string;
}) {
  const clases = cn(
    "px-3 py-1.5 align-top",
    numerica && "tabular text-right whitespace-nowrap",
    encabezado && "text-left font-normal",
    className,
  );

  if (encabezado) {
    return (
      <th scope="row" title={title} className={clases}>
        {children}
      </th>
    );
  }

  return (
    <td title={title} className={clases}>
      {children}
    </td>
  );
}

export function Cabecera({ children }: { children: ReactNode }) {
  return (
    <thead>
      <tr className="border-b border-border">{children}</tr>
    </thead>
  );
}

export function Fila({ children }: { children: ReactNode }) {
  return <tr className="border-b border-border last:border-0">{children}</tr>;
}

/**
 * Estado vacío. Se redacta caso por caso a propósito.
 *
 * «No hay posiciones» y «no hay decisiones» significan cosas muy distintas en un
 * experimento de diez días, y un texto genérico obliga a ir a mirar la base de
 * datos para saber cuál de las dos es.
 */
export function Vacio({ children }: { children: ReactNode }) {
  return (
    <Tarjeta discontinua relleno="px-4 py-6" className="text-[13px] text-text-muted">
      {children}
    </Tarjeta>
  );
}

/**
 * Paginación por desplazamiento.
 *
 * Enseña «41–80 de 480» y no solo las flechas: en un histórico que crece cada día
 * saber cuánto hay detrás es la mitad de la información, y es lo que dice si
 * merece la pena seguir mirando.
 */
export function Paginacion({
  total,
  limite,
  desplazamiento,
  onCambio,
}: {
  total: number;
  limite: number;
  desplazamiento: number;
  onCambio: (nuevo: number) => void;
}) {
  if (total <= limite) return null;

  const desde = desplazamiento + 1;
  const hasta = Math.min(desplazamiento + limite, total);

  return (
    <div className="mt-3 flex items-center justify-between gap-3 text-[13px]">
      <span className="tabular text-text-muted">
        {desde}–{hasta} de {total}
      </span>
      <div className="flex gap-2">
        <Boton
          disabled={desplazamiento === 0}
          onClick={() => onCambio(Math.max(0, desplazamiento - limite))}
        >
          Anteriores
        </Boton>
        <Boton disabled={hasta >= total} onClick={() => onCambio(desplazamiento + limite)}>
          Siguientes
        </Boton>
      </div>
    </div>
  );
}
