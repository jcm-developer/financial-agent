import { useLocation, useNavigate } from "react-router";

import { Select } from "@/components/piezas";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";

/**
 * Selector de perfil global (F5.5).
 *
 * Cambiar de perfil **navega**, conservando la sección: si estás en
 * `/p/europa-01/posiciones` y eliges otro experimento acabas en
 * `/p/otro/posiciones`, no de vuelta al resumen. Comparar la misma pantalla de
 * dos experimentos es el gesto que F5.6 llama central, y mandarte al inicio en
 * cada salto lo convertiría en cuatro clics.
 *
 * El desplegable es el `<Select>` compartido (`components/piezas.tsx`), en su
 * variante de etiqueta a la izquierda: en la cabecera no hay alto que gastar.
 */
export function SelectorPerfil() {
  const { referencia, perfiles } = usePerfilActivo();
  const navegar = useNavigate();
  const ubicacion = useLocation();

  if (!perfiles?.length) return null;

  const seccion = referencia
    ? ubicacion.pathname.split("/").slice(3).join("/") || "resumen"
    : "resumen";

  // El nombre es único y es lo que va en la URL, así que sirve de valor y de clave.
  const opciones: [string, string][] = [
    ...(referencia ? [] : ([["", "— elige uno —"]] as [string, string][])),
    ...perfiles.map(
      (fila): [string, string] => [
        fila.name,
        fila.status === "active" ? fila.name : `${fila.name} (${fila.status})`,
      ],
    ),
  ];

  return (
    <Select
      fila
      etiqueta="Experimento"
      opciones={opciones}
      value={referencia ?? ""}
      onChange={(evento) => navegar(`/p/${encodeURIComponent(evento.target.value)}/${seccion}`)}
    />
  );
}
