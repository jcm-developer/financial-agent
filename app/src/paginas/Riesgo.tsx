import { useMemo, useState } from "react";

import { useRiskEvents } from "@/api/hooks";
import type { RiskEventRow } from "@/api/types";
import { Insignia, Select, TituloPagina, TituloSeccion } from "@/components/piezas";
import { Seccion } from "@/components/Seccion";
import { Cabecera, Fila, Paginacion, Tabla, Td, Th, Vacio } from "@/components/Tabla";
import { cantidad, dinero, fechaHora } from "@/lib/formato";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";
import { useTitulo } from "@/layout/useTitulo";

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
 *
 * @return The rendered screen, with the per-rule tally above the table.
 */
export function Riesgo() {
  const { perfil, referencia } = usePerfilActivo();
  useTitulo("Riesgo", perfil?.name);
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
      <TituloPagina>Eventos de riesgo</TituloPagina>

      <Select
        etiqueta="Veredicto"
        value={veredicto}
        opciones={[
          ["rejected", "Rechazados"],
          ["approved", "Aprobados"],
          ["", "Todos"],
        ]}
        onChange={(evento) => {
          setVeredicto(evento.target.value);
          setDesplazamiento(0);
        }}
        claseCampo="mb-5 w-fit"
      />

      {porRegla.length > 0 && (
        <section className="mb-6">
          <TituloSeccion className="mb-2">Por regla</TituloSeccion>
          <ul className="flex flex-wrap gap-2">
            {porRegla.map(([regla, veces]) => (
              <li key={regla}>
                <Insignia>
                  {regla} <span className="tabular font-semibold">{veces}</span>
                </Insignia>
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

/**
 * Tallies the events by rule.
 *
 * @param filas - Events on the page being viewed, not the whole experiment.
 * @return `[rule, count]` pairs, most frequent first. Events with no rule are
 *     counted under `sin regla` rather than dropped, so the total still adds up.
 */
function contarPorRegla(filas: RiskEventRow[]): [string, number][] {
  const cuenta = new Map<string, number>();
  for (const fila of filas) {
    const regla = fila.rule ?? "sin regla";
    cuenta.set(regla, (cuenta.get(regla) ?? 0) + 1);
  }
  return [...cuenta.entries()].sort((a, b) => b[1] - a[1]);
}

/**
 * One row of the risk-events table.
 *
 * @param props - Row props.
 * @param props.fila - The event, with the rule it tripped.
 * @param props.simbolo - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
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
