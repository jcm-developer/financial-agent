import { useState } from "react";

import { useDecisions } from "@/api/hooks";
import type { DecisionRow } from "@/api/types";
import { Entrada, Etiqueta, Select, TituloPagina } from "@/components/piezas";
import { Seccion } from "@/components/Seccion";
import { Cabecera, Fila, Paginacion, Tabla, Td, Th, Vacio } from "@/components/Tabla";
import { dinero, fechaHora } from "@/lib/formato";
import { cn } from "@/lib/utils";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";
import { useTitulo } from "@/layout/useTitulo";

const LIMITE = 50;

/**
 * Lo que propuso el analista y qué dijo el Risk Manager (F4.7).
 *
 * Es la pantalla central del experimento: aquí se ve si el modelo discrimina entre
 * oportunidades o reparte la misma convicción a todo. Por eso la tesis y los
 * riesgos se enseñan en la propia fila y no detrás de un clic — un texto que hay
 * que ir a buscar no se lee, y entonces la pantalla mide otra cosa.
 *
 * @return The rendered screen.
 */
export function Decisiones() {
  const { perfil, referencia } = usePerfilActivo();
  useTitulo("Decisiones", perfil?.name);
  const [desplazamiento, setDesplazamiento] = useState(0);
  const [simboloFiltro, setSimboloFiltro] = useState("");
  const [accion, setAccion] = useState("");
  const [veredicto, setVeredicto] = useState("");

  const consulta = useDecisions(referencia, {
    symbol: simboloFiltro || undefined,
    action: accion || undefined,
    verdict: veredicto || undefined,
    limit: LIMITE,
    offset: desplazamiento,
  });

  const simbolo = perfil?.currency_symbol ?? "";

  function cambiarFiltro(aplicar: () => void) {
    // Al cambiar de filtro se vuelve a la primera página: quedarse en el
    // desplazamiento anterior da una tabla vacía con datos detrás, y eso se lee
    // como «no hay nada que cumpla el filtro», que no es verdad.
    aplicar();
    setDesplazamiento(0);
  }

  return (
    <>
      <TituloPagina>Decisiones</TituloPagina>

      <div className="mb-5 flex flex-wrap items-end gap-3">
        <Entrada
          etiqueta="Símbolo"
          type="search"
          value={simboloFiltro}
          placeholder="SAN.MC"
          onChange={(evento) => cambiarFiltro(() => setSimboloFiltro(evento.target.value))}
          className="w-32"
        />
        <Select
          etiqueta="Acción"
          value={accion}
          opciones={[
            ["", "Todas"],
            ["buy", "Compra"],
            ["sell", "Venta"],
            ["hold", "Mantener"],
          ]}
          onChange={(evento) => cambiarFiltro(() => setAccion(evento.target.value))}
        />
        <Select
          etiqueta="Veredicto"
          value={veredicto}
          opciones={[
            ["", "Todos"],
            ["approved", "Aprobadas"],
            ["rejected", "Rechazadas"],
          ]}
          onChange={(evento) => cambiarFiltro(() => setVeredicto(evento.target.value))}
        />
      </div>

      <Seccion consulta={consulta}>
        {(pagina) => (
          <>
            {pagina.items.length === 0 ? (
              <Vacio>
                {simboloFiltro || accion || veredicto
                  ? "Ninguna decisión cumple estos filtros."
                  : "El analista no ha registrado ninguna decisión todavía. Cada ciclo guarda una por candidato evaluado, incluidas las de mantener."}
              </Vacio>
            ) : (
              <Tabla titulo="Decisiones del analista con el veredicto de riesgo">
                <Cabecera>
                  <Th>Fecha</Th>
                  <Th>Símbolo</Th>
                  <Th>Acción</Th>
                  <Th numerica>Convicción</Th>
                  <Th>Tesis y riesgos</Th>
                  <Th>Veredicto</Th>
                </Cabecera>
                <tbody>
                  {pagina.items.map((fila) => (
                    <FilaDecision key={fila.id} fila={fila} simbolo={simbolo} />
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
 * One row of the decisions table, thesis and risks included.
 *
 * @param props - Row props.
 * @param props.fila - The decision and the risk manager's verdict on it.
 * @param props.simbolo - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function FilaDecision({ fila, simbolo }: { fila: DecisionRow; simbolo: string }) {
  return (
    <Fila>
      <Td className="whitespace-nowrap" title={fila.created_at}>
        {fechaHora(fila.created_at)}
      </Td>
      <Td>
        <span className="font-medium">{fila.symbol}</span>
        {/* 'entry' o 'exit': la misma acción significa cosas distintas según si
            se estaba evaluando entrar o revisar una posición abierta. */}
        <Etiqueta
          tono="neutro"
          title={
            fila.kind === "entry"
              ? "Se evaluaba entrar en el activo"
              : "Se revisaba una posición ya abierta"
          }
        >
          {fila.kind}
        </Etiqueta>
      </Td>
      <Td>
        <span
          className={cn(
            "font-medium",
            fila.action === "buy" && "text-positive-ink",
            fila.action === "sell" && "text-negative-ink",
            fila.action === "hold" && "text-text-muted",
          )}
        >
          {fila.action}
        </span>
      </Td>
      <Td numerica>{fila.conviction}</Td>
      <Td className="max-w-lg">
        {fila.thesis ? (
          <p className="text-xs leading-snug">{fila.thesis}</p>
        ) : (
          <p className="text-xs text-text-muted">Sin tesis.</p>
        )}
        {fila.risks && (
          <p className="mt-1 text-xs leading-snug text-text-muted">
            <span className="font-medium">Riesgos:</span> {fila.risks}
          </p>
        )}
        <p className="mt-1 text-[11px] text-text-muted">
          {fila.reference_price !== null && fila.reference_price !== undefined && (
            <>ref. {dinero(fila.reference_price, simbolo)} · </>
          )}
          {fila.horizon_days ? `${fila.horizon_days} d · ` : ""}
          {fila.llm_model ?? "modelo desconocido"}
        </p>
      </Td>
      <Td>
        {fila.verdict ? (
          <>
            <span
              className={
                fila.verdict === "approved"
                  ? "font-medium text-delta-good"
                  : "font-medium text-delta-bad"
              }
            >
              {fila.verdict === "approved" ? "aprobada" : "rechazada"}
            </span>
            {fila.rule && (
              <p className="mt-0.5 text-[11px] text-text-muted">{fila.rule}</p>
            )}
            {fila.risk_reason && (
              <p className="mt-0.5 max-w-xs text-xs leading-snug text-text-secondary">
                {fila.risk_reason}
              </p>
            )}
            {fila.approved_notional !== null && fila.approved_notional !== undefined && (
              <p className="tabular mt-0.5 text-[11px] text-text-muted">
                {dinero(fila.approved_notional, simbolo)}
              </p>
            )}
          </>
        ) : (
          // Una decisión de mantener no pasa por el Risk Manager: no hay nada que
          // dimensionar. Decirlo evita que parezca un hueco.
          <span className="text-xs text-text-muted">
            {fila.action === "hold" ? "no aplica" : "sin veredicto"}
          </span>
        )}
        {fila.order_status && (
          <p className="mt-0.5 text-[11px] text-text-muted">
            orden: {fila.order_status}
            {fila.filled_avg_price !== null && fila.filled_avg_price !== undefined
              ? ` a ${dinero(fila.filled_avg_price, simbolo)}`
              : ""}
          </p>
        )}
      </Td>
    </Fila>
  );
}
