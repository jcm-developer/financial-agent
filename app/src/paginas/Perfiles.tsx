import { Link } from "react-router";

import { useProfiles } from "@/api/hooks";
import type { ProfileSummary } from "@/api/types";
import { Bloque, clasesTarjeta, Tarjeta, TituloPagina } from "@/components/piezas";
import { Seccion } from "@/components/Seccion";
import { useTitulo } from "@/layout/useTitulo";

/**
 * Lista de experimentos.
 *
 * Es la versión mínima que hace navegable la aplicación: sin ella no hay forma de
 * llegar a un perfil. Las tarjetas con métricas, el alta y el duplicado son F5.2,
 * F5.3 y F5.4, y llegan en el tramo D con `shadcn`.
 */
export function Perfiles() {
  useTitulo("Experimentos");
  const perfiles = useProfiles();

  return (
    <>
      <TituloPagina>Experimentos</TituloPagina>

      <Seccion consulta={perfiles}>
        {(datos: ProfileSummary[]) =>
          datos.length === 0 ? (
            <Tarjeta relleno="p-6">
              <p className="text-[13px] text-text-secondary">
                No hay ningún experimento todavía. Se crean desde la consola mientras el alta
                de F5.3 no exista:
              </p>
              <Bloque className="mt-3">
                python run.py new-profile --name europa-01 --market eu --watch 89
              </Bloque>
            </Tarjeta>
          ) : (
            <ul className="flex flex-col gap-2">
              {datos.map((fila) => (
                <li key={fila.id}>
                  <Link
                    to={`/p/${encodeURIComponent(fila.name)}/resumen`}
                    className={clasesTarjeta(
                      "px-4 py-3",
                      "flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 transition-colors hover:bg-surface-sunken",
                    )}
                  >
                    <span className="font-medium">{fila.name}</span>
                    <span className="text-[13px] text-text-secondary">
                      {fila.market.toUpperCase()} · {fila.llm_model}
                    </span>
                    <span
                      className={
                        fila.status === "active"
                          ? "text-xs font-semibold text-delta-good"
                          : "text-xs text-text-muted"
                      }
                    >
                      {fila.status}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )
        }
      </Seccion>
    </>
  );
}
