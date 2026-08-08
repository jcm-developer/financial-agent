import { useMemo, useState } from "react";

import { useRiskEvents } from "@/api/hooks";
import type { RiskEventRow } from "@/api/types";
import { Seccion } from "@/components/Seccion";
import { Cabecera, Fila, Paginacion, Tabla, Td, Th, Vacio } from "@/components/Tabla";
import { cantidad, dinero, fechaHora } from "@/lib/formato";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";

const LIMITE = 50;

/**
 * Eventos del Risk Manager (F4.7).
 *
 * Los rechazos son la evidencia de que la barrera funciona, y **contra qué límite
 * choca el modelo más a menudo es una de las preguntas del experimento**, no un
 * detalle de una fila. Por eso arriba va el recuento por regla: si el 80 % de los
 * rechazos son del mismo límite, o el modelo insiste en algo que no cabe o ese
 * límite está mal puesto, y las dos cosas hay que saberlas.
 *
 * El recuento se calcula **sobre la página que se está viendo** y lo dice, porque
 * la API no ofrece un agregado por regla: presentarlo como si fuera el total del
 * experimento sería inventarse una estadística.
 */
export function Riesgo() {
  const { perfil, referencia } = usePerfilActivo();
  const [desplazamiento, setDesplazamiento] = useState(0);
  const [veredicto, setVeredicto] = useState("rejected");

  const consulta = useRiskEvents(referencia, {
    verdict: veredicto || undefined,
    limit: LIMITE,
    offset: desplazamiento,
  });

  const simbolo = perfil?.currency_symbol ?? "";
  const porRegla = useMemo(() => contarPorRegla(consulta.data?.items ?? []), [consulta.data]);

  return (
    <>
      <h1 className="mb-5 text-[17px] font-semibold tracking-tight">Eventos de riesgo</h1>

      <label className="mb-5 flex w-fit flex-col gap-1 text-[13px]">
        <span className="text-text-muted">Veredicto</span>
        <select
          value={veredicto}
          onChange={(evento) => {
            setVeredicto(evento.target.value);
            setDesplazamiento(0);
          }}
          className="min-h-8 rounded-md border border-border bg-card px-2 py-1"
        >
          <option value="rejected">Rechazados</option>
          <option value="approved">Aprobados</option>
          <option value="">Todos</option>
        </select>
      </label>

      {porRegla.length > 0 && (
        <section className="mb-6">
          <h2 className="mb-2 text-[13px] font-semibold tracking-wide text-text-secondary uppercase">
            Por regla
          </h2>
          <ul className="flex flex-wrap gap-2">
            {porRegla.map(([regla, veces]) => (
              <li
                key={regla}
                className="rounded-full border border-border bg-card px-3 py-1 text-[13px]"
              >
                {regla} <span className="tabular font-semibold">{veces}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-text-muted">
            Contado sobre las {consulta.data?.items.length ?? 0} filas de esta página, no
            sobre el histórico completo.
          </p>
        </section>
      )}

      <Seccion consulta={consulta}>
        {(pagina) => (
          <>
            {pagina.items.length === 0 ? (
              <Vacio>
                {veredicto === "rejected"
                  ? "El Risk Manager no ha rechazado nada todavía. Con pocas propuestas es lo esperable; si sigue así con muchas, conviene comprobar que los límites están donde se cree."
                  : "No hay eventos con este veredicto."}
              </Vacio>
            ) : (
              <Tabla titulo="Veredictos del Risk Manager">
                <Cabecera>
                  <Th>Fecha</Th>
                  <Th>Símbolo</Th>
                  <Th>Veredicto</Th>
                  <Th>Regla</Th>
                  <Th>Motivo</Th>
                  <Th numerica>Cantidad</Th>
                </Cabecera>
                <tbody>
                  {pagina.items.map((fila) => (
                    <FilaEvento key={fila.id} fila={fila} simbolo={simbolo} />
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

function contarPorRegla(filas: RiskEventRow[]): [string, number][] {
  const cuenta = new Map<string, number>();
  for (const fila of filas) {
    const regla = fila.rule ?? "sin regla";
    cuenta.set(regla, (cuenta.get(regla) ?? 0) + 1);
  }
  return [...cuenta.entries()].sort((a, b) => b[1] - a[1]);
}

function FilaEvento({ fila, simbolo }: { fila: RiskEventRow; simbolo: string }) {
  return (
    <Fila>
      <Td className="whitespace-nowrap" title={fila.created_at}>
        {fechaHora(fila.created_at)}
      </Td>
      <Td>
        {/* El kill switch no es de ningún símbolo: es de la cartera entera. */}
        {fila.symbol ? (
          <span className="font-medium">{fila.symbol}</span>
        ) : (
          <span className="text-text-muted">toda la cartera</span>
        )}
      </Td>
      <Td>
        <span
          className={
            fila.verdict === "approved"
              ? "font-medium text-delta-good"
              : "font-medium text-delta-bad"
          }
        >
          {fila.verdict === "approved" ? "aprobado" : "rechazado"}
        </span>
      </Td>
      <Td>
        <code className="text-xs">{fila.rule ?? "—"}</code>
      </Td>
      <Td className="max-w-md text-xs leading-snug">{fila.reason}</Td>
      <Td numerica>
        {cantidad(fila.approved_qty)}
        {fila.approved_notional !== null && fila.approved_notional !== undefined && (
          <p className="text-[11px] text-text-muted">
            {dinero(fila.approved_notional, simbolo)}
          </p>
        )}
      </Td>
    </Fila>
  );
}
