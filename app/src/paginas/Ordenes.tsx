import { useState } from "react";

import { useOrders } from "@/api/hooks";
import type { OrderRow } from "@/api/types";
import { Seccion } from "@/components/Seccion";
import { Cabecera, Fila, Paginacion, Tabla, Td, Th, Vacio } from "@/components/Tabla";
import { cantidad, dinero, fechaHora } from "@/lib/formato";
import { cn } from "@/lib/utils";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";

const LIMITE = 50;

/**
 * Órdenes enviadas, y también las que NO se enviaron (F4.7).
 *
 * Las no ejecutadas son la mitad interesante: una orden en `canceled` o `dry_run`
 * significa que el agente decidió operar y no pudo —mercado cerrado, o el
 * simulador en seco— y sin verlas parece que el analista no propuso nada. La
 * columna de motivo lleva el `error` que dejó el ciclo.
 */
export function Ordenes() {
  const { perfil, referencia } = usePerfilActivo();
  const [desplazamiento, setDesplazamiento] = useState(0);
  const [simboloFiltro, setSimboloFiltro] = useState("");

  const consulta = useOrders(referencia, {
    symbol: simboloFiltro || undefined,
    limit: LIMITE,
    offset: desplazamiento,
  });

  const simbolo = perfil?.currency_symbol ?? "";

  return (
    <>
      <h1 className="mb-5 text-[17px] font-semibold tracking-tight">Órdenes</h1>

      <label className="mb-5 flex w-fit flex-col gap-1 text-[13px]">
        <span className="text-text-muted">Símbolo</span>
        <input
          type="search"
          value={simboloFiltro}
          placeholder="SAN.MC"
          onChange={(evento) => {
            setSimboloFiltro(evento.target.value);
            setDesplazamiento(0);
          }}
          className="min-h-8 w-32 rounded-md border border-border bg-card px-2 py-1"
        />
      </label>

      <Seccion consulta={consulta}>
        {(pagina) => (
          <>
            {pagina.items.length === 0 ? (
              <Vacio>
                {simboloFiltro
                  ? `Ninguna orden de ${simboloFiltro}.`
                  : "No se ha enviado ninguna orden todavía. Aquí aparecerán también las que el agente aprobó pero no pudo ejecutar."}
              </Vacio>
            ) : (
              <Tabla titulo="Órdenes enviadas y no enviadas">
                <Cabecera>
                  <Th>Enviada</Th>
                  <Th>Símbolo</Th>
                  <Th>Lado</Th>
                  <Th numerica>Cantidad</Th>
                  <Th numerica>Ejecutada</Th>
                  <Th numerica>Precio</Th>
                  <Th>Estado</Th>
                </Cabecera>
                <tbody>
                  {pagina.items.map((fila) => (
                    <FilaOrden key={fila.id} fila={fila} simbolo={simbolo} />
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

/** `filled` es lo normal; el resto merece color porque significa que algo pasó. */
function claseEstado(estado: string): string {
  if (estado === "filled") return "text-delta-good";
  if (estado === "failed") return "text-delta-bad";
  if (estado === "canceled" || estado === "dry_run") return "text-warning";
  return "text-text-secondary";
}

function FilaOrden({ fila, simbolo }: { fila: OrderRow; simbolo: string }) {
  return (
    <Fila>
      <Td className="whitespace-nowrap" title={fila.submitted_at}>
        {fechaHora(fila.submitted_at)}
      </Td>
      <Td>
        <span className="font-medium">{fila.symbol}</span>
      </Td>
      <Td>
        <span className={fila.side === "buy" ? "text-positive" : "text-negative"}>
          {fila.side === "buy" ? "compra" : "venta"}
        </span>
      </Td>
      <Td numerica>{cantidad(fila.qty)}</Td>
      <Td numerica>{cantidad(fila.filled_qty)}</Td>
      <Td numerica>{dinero(fila.filled_avg_price, simbolo)}</Td>
      <Td>
        <span className={cn("font-medium", claseEstado(fila.status))}>{fila.status}</span>
        {fila.error && (
          <p className="mt-0.5 max-w-sm text-xs leading-snug text-text-secondary">
            {fila.error}
          </p>
        )}
      </Td>
    </Fila>
  );
}
