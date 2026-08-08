import { useParams } from "react-router";

import { useProfiles } from "@/api/hooks";
import type { ProfileSummary } from "@/api/types";

/**
 * El perfil que se está mirando, sacado de la URL (`/p/:perfil/...`).
 *
 * **Vive en la URL y no en un contexto de React** (decisión del tramo C): todo
 * este proyecto se dedica a no confundir dos experimentos, y un selector en
 * memoria es la forma más fácil de estar mirando el equivocado tras recargar o
 * volver atrás. Con el nombre en la URL, la pregunta "¿qué experimento es esto?"
 * la responde la barra de direcciones.
 *
 * Se busca en la lista de perfiles en vez de pedir `/api/profiles/:ref` para no
 * duplicar la petición: el selector ya necesita la lista entera, y así un perfil
 * que no existe se distingue de un fallo de red sin inventar estados.
 */
export interface PerfilActivo {
  /** Lo que dice la URL. Vacío en las rutas que no llevan perfil. */
  referencia: string | undefined;
  perfil: ProfileSummary | undefined;
  perfiles: ProfileSummary[] | undefined;
  cargando: boolean;
  error: Error | null;
  /** True cuando la lista ya llegó y la referencia no está en ella. */
  noEncontrado: boolean;
}

export function usePerfilActivo(): PerfilActivo {
  const { perfil: referencia } = useParams<{ perfil: string }>();
  const consulta = useProfiles();

  const encontrado = referencia
    ? consulta.data?.find((fila) => fila.name === referencia || fila.id === referencia)
    : undefined;

  return {
    referencia,
    perfil: encontrado,
    perfiles: consulta.data,
    cargando: consulta.isPending,
    error: consulta.error,
    noEncontrado:
      Boolean(referencia) && consulta.data !== undefined && encontrado === undefined,
  };
}
