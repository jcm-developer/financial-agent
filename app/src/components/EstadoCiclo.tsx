import type { CycleRow } from "@/api/types";
import { Etiqueta } from "@/components/piezas";

/**
 * El estado de un ciclo, con el matiz que aportó F6.9.
 *
 * Un `failed` porque el analista se quedó sin respuesta —cuota agotada, proveedor
 * caído— no es lo mismo que un `failed` del broker o de la base de datos, y hasta
 * F6.9 no había forma de distinguirlos: el ciclo terminaba en `completed` con cero
 * propuestas y se leía como un día tranquilo. Ahora el recuento de llamadas está
 * en la fila, así que se enseña aquí, que es donde se mira.
 */
export function EstadoCiclo({ ciclo }: { ciclo: CycleRow }) {
  const llamadas = ciclo.analyst_calls ?? 0;
  const fallos = ciclo.analyst_failures ?? 0;
  const sinModelo = llamadas > 0 && fallos === llamadas;

  return (
    <span
      className={
        ciclo.status === "failed"
          ? "text-delta-bad"
          : ciclo.status === "halted"
            ? "text-warning"
            : "text-text-secondary"
      }
      title={ciclo.error ?? undefined}
    >
      {ciclo.status}
      {sinModelo && (
        <Etiqueta title="Ninguna llamada al modelo obtuvo respuesta: este ciclo no analizó nada (F6.9)">
          SIN MODELO
        </Etiqueta>
      )}
      {!sinModelo && fallos > 0 && (
        <Etiqueta
          tono="atencion"
          title={`${fallos} de ${llamadas} llamadas al modelo se quedaron sin respuesta: a esos símbolos no se les llegó a mirar`}
        >
          {fallos}/{llamadas} SIN RESPUESTA
        </Etiqueta>
      )}
    </span>
  );
}
