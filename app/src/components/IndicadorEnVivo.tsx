import { Insignia } from "@/components/piezas";
import { cn } from "@/lib/utils";
import type { EstadoStream } from "@/api/stream";

/**
 * "Datos en vivo / desconectado" (F4.5).
 *
 * Tres estados y no dos, porque agrupar "reconectando" con "desconectado"
 * mentiria en la direccion mala: el servidor **retira las conexiones cada 15
 * minutos a proposito** (F3.5) y `EventSource` las restablece solo, asi que un
 * indicador de dos estados parpadearia en rojo cada cuarto de hora en una
 * conexion perfectamente sana. A los tres cuartos de hora nadie se lo cree, y
 * entonces tampoco se cree el rojo de verdad.
 */

const APARIENCIA: Record<EstadoStream, { texto: string; clase: string }> = {
  vivo: { texto: "datos en vivo", clase: "text-delta-good border-current" },
  conectando: { texto: "reconectando…", clase: "text-warning border-current" },
  desconectado: { texto: "desconectado", clase: "text-delta-bad border-current" },
};

interface Props {
  estado: EstadoStream;
  reconexiones?: number;
  aviso?: string | null;
}

/**
 * The stream's connection state, as a badge in the header.
 *
 * @param props - Indicator props.
 * @param props.estado - Connection state, which decides text and colour.
 * @param props.reconexiones - How many times it has reconnected, shown in the
 *     tooltip so a connection that keeps dropping is visible.
 * @param props.aviso - Last notice the server sent before cutting.
 * @return The rendered badge.
 */
export function IndicadorEnVivo({ estado, reconexiones = 0, aviso }: Props) {
  const { texto, clase } = APARIENCIA[estado];

  return (
    <Insignia
      compacta
      className={cn("gap-2", clase)}
      // El color no puede ser el unico portador del significado (F4.9): el texto
      // ya lo dice, y el title añade el detalle para quien lo necesite.
      title={
        aviso
          ? `${texto} — ultimo aviso del servidor: ${aviso}`
          : reconexiones
            ? `${texto} — ${reconexiones} reconexiones`
            : texto
      }
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full bg-current",
          estado === "vivo" && "animate-pulse",
        )}
      />
      {texto}
    </Insignia>
  );
}
