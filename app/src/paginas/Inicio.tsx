import { Navigate } from "react-router";

import { useProfiles } from "@/api/hooks";
import { AvisoDeError } from "@/components/Seccion";

/**
 * La raíz decide a dónde ir en función de lo que haya.
 *
 * **Con un solo experimento activo se entra directo a él**, que es la decisión nº
 * 5 del plan: los experimentos se hacen de uno en uno, así que pasar siempre por
 * la lista sería un clic de peaje en todas las visitas. Con varios activos, o con
 * ninguno, la lista sí es la respuesta correcta.
 *
 * `replace` para no dejar la redirección en el historial: sin eso, el botón de
 * volver atrás rebota entre la raíz y el resumen.
 */
export function Inicio() {
  const { data, isPending, error } = useProfiles();

  if (isPending) return <p className="text-[13px] text-text-muted">Cargando…</p>;
  if (error) return <AvisoDeError error={error} />;

  const activos = (data ?? []).filter((fila) => fila.status === "active");
  if (activos.length === 1) {
    return <Navigate to={`/p/${encodeURIComponent(activos[0]!.name)}/resumen`} replace />;
  }
  return <Navigate to="/perfiles" replace />;
}
