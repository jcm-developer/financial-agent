import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import {
  useCycle,
  useCycleControl,
  useCycles,
  useLanzarCiclo,
  usePararCiclo,
} from "@/api/hooks";
import type { CycleControl, CycleRow } from "@/api/types";
import { EstadoCiclo } from "@/components/EstadoCiclo";
import {
  Aviso,
  Bloque,
  Boton,
  BotonEnlace,
  Tarjeta,
  TituloPagina,
  TituloSeccion,
} from "@/components/piezas";
import { Seccion } from "@/components/Seccion";
import { Cabecera, Fila, Paginacion, Tabla, Td, Th, Vacio } from "@/components/Tabla";
import { claseSigno, dineroConSigno, duracion, fechaHora } from "@/lib/formato";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";
import { useTitulo } from "@/layout/useTitulo";

const LIMITE = 30;

/**
 * Ciclos ejecutados, con el log del que está corriendo (F4.7).
 *
 * El log no lo pide esta pantalla: llega por el stream que abre el Layout y se lee
 * de la caché con `useCycleControl`. Por eso se sigue moviendo aunque se cambie de
 * pestaña y se vuelva.
 *
 * @return The rendered screen.
 */
export function Ciclos() {
  const { perfil, referencia } = usePerfilActivo();
  useTitulo("Ciclos", perfil?.name);
  const [desplazamiento, setDesplazamiento] = useState(0);
  const [parametros, setParametros] = useSearchParams();
  const seleccionado = parametros.get("ciclo");

  const control = useCycleControl();
  const ciclos = useCycles(referencia, { limit: LIMITE, offset: desplazamiento });
  const simbolo = perfil?.currency_symbol ?? "";

  return (
    <>
      <TituloPagina>Ciclos</TituloPagina>

      {control.data && <Control estado={control.data} perfil={referencia} />}

      {seleccionado && (
        <Detalle
          id={seleccionado}
          onCerrar={() => {
            parametros.delete("ciclo");
            setParametros(parametros, { replace: true });
          }}
        />
      )}

      <Seccion titulo="Histórico" consulta={ciclos}>
        {(pagina) => (
          <>
            {pagina.items.length === 0 ? (
              <Vacio>
                Todavía no ha corrido ningún ciclo para este experimento.
              </Vacio>
            ) : (
              <Tabla titulo="Ciclos ejecutados">
                <Cabecera>
                  <Th>Inicio</Th>
                  <Th numerica>Duración</Th>
                  <Th>Estado</Th>
                  <Th>Mercado</Th>
                  <Th numerica>Decisiones</Th>
                  <Th numerica>Aprob.</Th>
                  <Th numerica>Rechaz.</Th>
                  <Th numerica>Órdenes</Th>
                  <Th numerica>Δ capital</Th>
                </Cabecera>
                <tbody>
                  {pagina.items.map((ciclo) => (
                    <FilaCiclo
                      key={ciclo.id}
                      ciclo={ciclo}
                      simbolo={simbolo}
                      seleccionado={ciclo.id === seleccionado}
                      onElegir={() => {
                        parametros.set("ciclo", ciclo.id);
                        setParametros(parametros, { replace: true });
                      }}
                    />
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
 * Panel de control del ciclo (F3.4).
 *
 * Se comprueba `enabled` porque los controles se pueden apagar con
 * `API_CONTROLS=false` (F3.8). Cuando están apagados **se dice**, en lugar de
 * enseñar botones que devolverían un error: un botón que siempre falla es peor que
 * ningún botón.
 *
 * @param props - Control props.
 * @param props.estado - Cycle control state, including whether it is enabled.
 * @param props.perfil - Profile the cycle would run against.
 * @return The rendered panel, or the notice that the controls are switched off.
 */
function Control({
  estado,
  perfil,
}: {
  estado: CycleControl;
  perfil: string | undefined;
}) {
  const lanzar = useLanzarCiclo(perfil);
  const parar = usePararCiclo();
  const fallo = lanzar.error ?? parar.error;

  if (!estado.enabled) {
    return (
      <Tarjeta className="mb-6 text-[13px] text-text-secondary">
        Los controles de ciclo están apagados en el servidor
        (<code>API_CONTROLS=false</code>). Los ciclos los lanza el planificador.
      </Tarjeta>
    );
  }

  return (
    <Tarjeta className="mb-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[13px]">
            <span className={estado.running ? "font-semibold text-warning" : "text-text-secondary"}>
              {estado.running ? "Ciclo en marcha" : "Sin ciclo en marcha"}
            </span>
            {" — "}
            {estado.stage}
            {estado.running && estado.elapsed_seconds !== null && estado.elapsed_seconds !== undefined
              ? ` · ${duracion(estado.elapsed_seconds)}`
              : ""}
          </p>
          {estado.profile && (
            <p className="mt-0.5 text-xs text-text-muted">
              perfil {estado.profile}
              {estado.dry_run ? " · en seco" : ""}
              {estado.returncode !== null && estado.returncode !== undefined
                ? ` · terminó con código ${estado.returncode}`
                : ""}
            </p>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <Boton
            disabled={estado.running || lanzar.isPending || !perfil}
            onClick={() => lanzar.mutate({})}
          >
            Lanzar ciclo
          </Boton>
          {/* En seco: analiza y decide pero no ejecuta. Es la forma de ver qué
              haría el modelo sin mover la cartera del experimento. */}
          <Boton
            variante="sutil"
            disabled={estado.running || lanzar.isPending || !perfil}
            onClick={() => lanzar.mutate({ dry_run: true })}
          >
            Lanzar en seco
          </Boton>
          <Boton
            variante="peligro"
            disabled={!estado.running || parar.isPending}
            onClick={() => parar.mutate()}
          >
            Parar
          </Boton>
        </div>
      </div>

      {fallo && <Aviso className="mt-3">{fallo.message}</Aviso>}

      <Log lineas={estado.lines ?? []} />
    </Tarjeta>
  );
}

/**
 * El log del ciclo en curso.
 *
 * **Solo baja sola si ya estabas abajo.** Un log que se autodesplaza siempre es
 * imposible de leer: en cuanto subes a mirar una línea, la siguiente te devuelve
 * al final. Con el umbral, quien sube se queda donde quiso y quien está al final
 * sigue viendo lo último.
 *
 * @param props - Log props.
 * @param props.lineas - Lines emitted so far, in order.
 * @return The rendered log.
 */
function Log({ lineas }: { lineas: string[] }) {
  const caja = useRef<HTMLPreElement>(null);
  const [abierto, setAbierto] = useState(true);

  useEffect(() => {
    const elemento = caja.current;
    if (!elemento) return;
    const alFinal =
      elemento.scrollHeight - elemento.scrollTop - elemento.clientHeight < 40;
    if (alFinal) elemento.scrollTop = elemento.scrollHeight;
  }, [lineas]);

  if (!lineas.length) return null;

  return (
    <div className="mt-3">
      <BotonEnlace onClick={() => setAbierto((valor) => !valor)} aria-expanded={abierto}>
        {abierto ? "Ocultar" : "Ver"} el log ({lineas.length} líneas)
      </BotonEnlace>
      {abierto && (
        <Bloque
          ref={caja}
          // `aria-live` en polite y no assertive: son cientos de líneas y un
          // lector de pantalla las anunciaría todas.
          aria-live="polite"
          className="mt-2 max-h-64 leading-relaxed whitespace-pre-wrap"
        >
          {lineas.join("\n")}
        </Bloque>
      )}
    </div>
  );
}

/**
 * One cycle, with the copy of the settings it ran under.
 *
 * Un ciclo con la copia de los parámetros con los que corrió (F6.3).
 *
 * @param props - Detail props.
 * @param props.id - Cycle id.
 * @param props.onCerrar - Called when the panel is dismissed.
 * @return The rendered panel.
 */
function Detalle({ id, onCerrar }: { id: string; onCerrar: () => void }) {
  const consulta = useCycle(id);

  return (
    <Tarjeta etiqueta="section" className="mb-6">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <TituloSeccion>Detalle del ciclo</TituloSeccion>
        <BotonEnlace onClick={onCerrar}>Cerrar</BotonEnlace>
      </div>

      <Seccion consulta={consulta}>
        {(ciclo) => (
          <>
            <dl className="grid gap-x-6 gap-y-1 text-[13px] sm:grid-cols-3">
              <Dato etiqueta="Inicio" valor={fechaHora(ciclo.started_at)} />
              <Dato etiqueta="Fin" valor={fechaHora(ciclo.finished_at)} />
              <Dato etiqueta="Modelo" valor={ciclo.llm_model ?? "—"} />
              <Dato
                etiqueta="Llamadas al analista"
                valor={
                  (ciclo.analyst_calls ?? 0) === 0
                    ? "ninguna"
                    : `${ciclo.analyst_calls} (${ciclo.analyst_failures ?? 0} sin respuesta)`
                }
              />
              <Dato
                etiqueta="Símbolos analizados"
                valor={String(ciclo.symbols_scanned?.length ?? 0)}
              />
              <Dato etiqueta="Mercado" valor={ciclo.market_open ? "abierto" : "cerrado"} />
            </dl>

            {ciclo.error && <Aviso className="mt-3">{ciclo.error}</Aviso>}

            {/* Los ajustes anteriores a F6.3 vienen a null. Es información que
                falta, no un cero: quien compare experimentos necesita distinguir
                "corrió con estos ajustes" de "no se sabe con qué ajustes corrió". */}
            {ciclo.settings === null || ciclo.settings === undefined ? (
              <p className="mt-3 text-[13px] text-text-muted">
                Sin copia de los parámetros: es un ciclo anterior a que se guardaran (F6.3).
              </p>
            ) : (
              <details className="mt-3">
                <summary className="cursor-pointer text-[13px] text-text-secondary">
                  Parámetros con los que corrió
                </summary>
                <Bloque className="mt-2 max-h-64">
                  {JSON.stringify(ciclo.settings, null, 2)}
                </Bloque>
              </details>
            )}
          </>
        )}
      </Seccion>
    </Tarjeta>
  );
}

/**
 * A label and its value inside the detail panel's definition list.
 *
 * @param props - Item props.
 * @param props.etiqueta - Label, in the interface language.
 * @param props.valor - Value, already formatted.
 * @return The rendered pair.
 */
function Dato({ etiqueta, valor }: { etiqueta: string; valor: string }) {
  return (
    <div>
      <dt className="text-text-muted">{etiqueta}</dt>
      <dd className="tabular">{valor}</dd>
    </div>
  );
}

/**
 * One row of the cycles table.
 *
 * @param props - Row props.
 * @param props.ciclo - The cycle.
 * @param props.simbolo - Currency symbol of the profile's market, never assumed.
 * @param props.seleccionado - Whether its detail panel is open.
 * @param props.onElegir - Called when the row is chosen.
 * @return The rendered row.
 */
function FilaCiclo({
  ciclo,
  simbolo,
  seleccionado,
  onElegir,
}: {
  ciclo: CycleRow;
  simbolo: string;
  seleccionado: boolean;
  onElegir: () => void;
}) {
  return (
    <Fila>
      <Td>
        <BotonEnlace
          variante="neutro"
          onClick={onElegir}
          aria-current={seleccionado ? "true" : undefined}
          className={seleccionado ? "font-semibold decoration-current" : undefined}
        >
          {fechaHora(ciclo.started_at)}
        </BotonEnlace>
      </Td>
      <Td numerica>
        {ciclo.finished_at
          ? duracion(
              (new Date(ciclo.finished_at).getTime() -
                new Date(ciclo.started_at).getTime()) /
                1000,
            )
          : "—"}
      </Td>
      <Td>
        <EstadoCiclo ciclo={ciclo} />
      </Td>
      <Td>
        <span className={ciclo.market_open ? "text-text-secondary" : "text-text-muted"}>
          {ciclo.market_open ? "abierto" : "cerrado"}
        </span>
      </Td>
      <Td numerica>{ciclo.decisions ?? 0}</Td>
      <Td numerica>{ciclo.approved ?? 0}</Td>
      <Td numerica>{ciclo.rejected ?? 0}</Td>
      <Td numerica>{ciclo.orders ?? 0}</Td>
      <Td numerica className={claseSigno(ciclo.equity_delta)}>
        {dineroConSigno(ciclo.equity_delta, simbolo)}
      </Td>
    </Fila>
  );
}
