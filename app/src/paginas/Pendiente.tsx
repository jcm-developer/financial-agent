import { Tarjeta, TituloPagina } from "@/components/piezas";

/**
 * Hueco de una pantalla que llega en el tramo D, diciendo **qué** tarea la trae.
 *
 * Un "próximamente" a secas es la clase de cartel que sobrevive meses porque
 * nadie sabe qué falta. Nombrando la tarea, la pantalla se convierte en su propia
 * lista de pendientes, y quien la abra durante el experimento sabe si está viendo
 * un hueco o una avería.
 */
export function Pendiente({
  titulo,
  tarea,
  descripcion,
}: {
  titulo: string;
  tarea: string;
  descripcion: string;
}) {
  return (
    <>
      <TituloPagina>{titulo}</TituloPagina>
      <Tarjeta discontinua relleno="p-6">
        <p className="text-[13px] text-text-secondary">{descripcion}</p>
        <p className="mt-3 text-[13px] text-text-muted">
          Pendiente de <span className="font-semibold text-foreground">{tarea}</span>. Los
          datos ya están publicados en la API; falta la pantalla.
        </p>
      </Tarjeta>
    </>
  );
}
