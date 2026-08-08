import { useLocation, useNavigate } from "react-router";

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
 * Es un `<select>` nativo a propósito y no el de shadcn: con un experimento
 * activo a la vez la lista tiene dos o tres entradas, y el nativo ya es accesible
 * con teclado sin traerse un popover. Cuando haya que enseñar métricas dentro de
 * cada opción se cambia.
 */
export function SelectorPerfil() {
  const { referencia, perfiles } = usePerfilActivo();
  const navegar = useNavigate();
  const ubicacion = useLocation();

  if (!perfiles?.length) return null;

  const seccion = referencia
    ? ubicacion.pathname.split("/").slice(3).join("/") || "resumen"
    : "resumen";

  return (
    <label className="flex items-center gap-2 text-[13px]">
      <span className="text-text-muted">Experimento</span>
      <select
        className="min-h-8 rounded-md border border-border bg-card px-2 py-1 text-[13px] hover:bg-surface-sunken"
        value={referencia ?? ""}
        onChange={(evento) => navegar(`/p/${encodeURIComponent(evento.target.value)}/${seccion}`)}
      >
        {!referencia && <option value="">— elige uno —</option>}
        {perfiles.map((fila) => (
          <option key={fila.id} value={fila.name}>
            {fila.name}
            {fila.status === "active" ? "" : ` (${fila.status})`}
          </option>
        ))}
      </select>
    </label>
  );
}
