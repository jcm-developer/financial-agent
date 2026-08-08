import { useState } from "react";

import { usePositions } from "@/api/hooks";
import type { PositionRow } from "@/api/types";
import { TituloPagina } from "@/components/piezas";
import { Procedencia } from "@/components/Procedencia";
import { Seccion } from "@/components/Seccion";
import { Cabecera, Fila, Paginacion, Tabla, Td, Th, Vacio } from "@/components/Tabla";
import {
  claseSigno,
  dinero,
  dineroConSigno,
  cantidad,
  fechaHora,
  porcentaje,
} from "@/lib/formato";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";
import { useTitulo } from "@/layout/useTitulo";

const LIMITE = 50;

/**
 * Posiciones abiertas y cerradas (F4.7).
 *
 * Son **dos tablas y no una con un filtro**, porque las columnas que importan son
 * distintas: en una abierta se mira el P&L no realizado y la distancia al stop, y
 * en una cerrada el precio de salida y el motivo. Meterlas juntas dejaría media
 * tabla con guiones.
 *
 * @return The rendered screen with both tables.
 */
export function Posiciones() {
  const { perfil, referencia } = usePerfilActivo();
  useTitulo("Posiciones", perfil?.name);
  const [desplazamiento, setDesplazamiento] = useState(0);

  const abiertas = usePositions(referencia, { status: "open", limit: 200 });
  const cerradas = usePositions(referencia, {
    status: "closed",
    limit: LIMITE,
    offset: desplazamiento,
  });

  const simbolo = perfil?.currency_symbol ?? "";

  return (
    <>
      <TituloPagina>Posiciones</TituloPagina>

      <Seccion titulo="Abiertas" consulta={abiertas}>
        {(pagina) =>
          pagina.items.length === 0 ? (
            <Vacio>Ninguna posición abierta ahora mismo.</Vacio>
          ) : (
            <Tabla titulo="Posiciones abiertas">
              <Cabecera>
                <Th>Símbolo</Th>
                <Th>Abierta</Th>
                <Th numerica>Cantidad</Th>
                <Th numerica>Entrada</Th>
                <Th numerica>Último</Th>
                <Th numerica>P&L</Th>
                <Th numerica>Stop</Th>
                <Th numerica>Objetivo</Th>
              </Cabecera>
              <tbody>
                {pagina.items.map((fila) => (
                  <FilaAbierta key={fila.id} fila={fila} simbolo={simbolo} />
                ))}
              </tbody>
            </Tabla>
          )
        }
      </Seccion>

      <Seccion titulo="Cerradas" consulta={cerradas}>
        {(pagina) => (
          <>
            {pagina.items.length === 0 ? (
              <Vacio>
                Todavía no se ha cerrado ninguna posición. Solo se cierran al tocar el stop o
                el objetivo, o si el analista ve la tesis deteriorada: el horizonte en días no
                cierra nada por sí solo.
              </Vacio>
            ) : (
              <Tabla titulo="Posiciones cerradas">
                <Cabecera>
                  <Th>Símbolo</Th>
                  <Th>Cerrada</Th>
                  <Th numerica>Cantidad</Th>
                  <Th numerica>Entrada</Th>
                  <Th numerica>Salida</Th>
                  <Th numerica>P&L</Th>
                  <Th>Motivo</Th>
                </Cabecera>
                <tbody>
                  {pagina.items.map((fila) => (
                    <FilaCerrada key={fila.id} fila={fila} simbolo={simbolo} />
                  ))}
                </tbody>
              </Tabla>
            )}
            <Paginacion
              total={pagina.total}
              limite={pagina.limit}
              desplazamiento={pagina.offset}
              onCambio={setDesplazamiento}
            />
          </>
        )}
      </Seccion>
    </>
  );
}

/**
 * One row of the open-positions table.
 *
 * @param props - Row props.
 * @param props.fila - The position.
 * @param props.simbolo - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function FilaAbierta({ fila, simbolo }: { fila: PositionRow; simbolo: string }) {
  return (
    <Fila>
      <Td>
        <span className="font-medium">{fila.symbol}</span>
        {fila.thesis && (
          <p className="mt-0.5 max-w-md text-xs leading-snug text-text-muted">
            {fila.thesis}
          </p>
        )}
      </Td>
      <Td className="whitespace-nowrap" title={fila.opened_at}>
        {fechaHora(fila.opened_at)}
      </Td>
      <Td numerica>{cantidad(fila.qty)}</Td>
      <Td numerica>{dinero(fila.entry_price, simbolo)}</Td>
      <Td numerica>
        {dinero(fila.last_price, simbolo)}
        <Procedencia fila={fila} />
      </Td>
      <Td numerica className={claseSigno(fila.unrealized_pnl)}>
        {dineroConSigno(fila.unrealized_pnl, simbolo)}
        <span className="ml-1 text-xs">
          {porcentaje(fila.unrealized_pnl_pct, { signo: true })}
        </span>
      </Td>
      <Td numerica>
        {dinero(fila.stop_price, simbolo)}
        {fila.stop_distance_pct !== null && fila.stop_distance_pct !== undefined && (
          <span className="ml-1 text-xs text-text-muted" title="Distancia al stop">
            {porcentaje(fila.stop_distance_pct)}
          </span>
        )}
      </Td>
      <Td numerica>{dinero(fila.target_price, simbolo)}</Td>
    </Fila>
  );
}

/**
 * One row of the closed-positions table, which shows exit price and reason
 * instead of the stop.
 *
 * @param props - Row props.
 * @param props.fila - The position.
 * @param props.simbolo - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function FilaCerrada({ fila, simbolo }: { fila: PositionRow; simbolo: string }) {
  return (
    <Fila>
      <Td>
        <span className="font-medium">{fila.symbol}</span>
      </Td>
      <Td className="whitespace-nowrap" title={fila.closed_at ?? undefined}>
        {fechaHora(fila.closed_at)}
      </Td>
      <Td numerica>{cantidad(fila.qty)}</Td>
      <Td numerica>{dinero(fila.entry_price, simbolo)}</Td>
      <Td numerica>{dinero(fila.exit_price, simbolo)}</Td>
      <Td numerica className={claseSigno(fila.realized_pnl)}>
        {dineroConSigno(fila.realized_pnl, simbolo)}
      </Td>
      <Td className="max-w-sm text-xs text-text-secondary">{fila.exit_reason ?? "—"}</Td>
    </Fila>
  );
}
