import type { PositionRow } from "@/api/types";
import { Etiqueta } from "@/components/piezas";
import { fechaHora } from "@/lib/formato";

/**
 * La etiqueta de procedencia del precio (F3.2).
 *
 * Se enseña siempre que haya precio porque es la diferencia entre un P&L que
 * significa algo y uno que mezcla el cierre de anteayer con el precio de hace un
 * minuto. Y cuando no hay ninguno se dice: la posición se valora al precio de
 * entrada, o sea que su P&L es cero por falta de datos, no por no haberse movido.
 *
 * Está aquí y no dentro de una pantalla porque las posiciones abiertas salen en
 * dos —el resumen y la de posiciones— y estaban divergiendo: la del resumen se
 * callaba el caso sin precio y ponía «de hace minutos» donde la otra ponía la hora
 * exacta. Con dos copias, la que se lee más es la que peor informa.
 *
 * @param props - Provenance props.
 * @param props.fila - Position row, of which the price source and its timestamp
 *     are read.
 * @return The tag saying where the price came from, always with the full
 *     sentence in its `title`.
 */
export function Procedencia({ fila }: { fila: PositionRow }) {
  if (!fila.price_source) {
    return (
      <Etiqueta tono="malo" title="Sin precio: la posición se valora a su precio de entrada">
        SIN PRECIO
      </Etiqueta>
    );
  }

  const enVivo = fila.price_source === "live";

  return (
    <Etiqueta
      tono={enVivo ? "bueno" : "atencion"}
      title={
        enVivo
          ? `Cotización del ingestor (${fechaHora(fila.last_price_as_of)})`
          : `El precio que vio el analista en su último ciclo (${fechaHora(fila.last_price_as_of)})`
      }
    >
      {enVivo ? "VIVO" : "CICLO"}
    </Etiqueta>
  );
}
