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
import { Seccion } from "@/components/Seccion";
import { Cabecera, Fila, Paginacion, Tabla, Td, Th, Vacio } from "@/components/Tabla";
import { claseSigno, dineroConSigno, duracion, fechaHora } from "@/lib/formato";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";

const LIMITE = 30;

/**
 * Ciclos ejecutados, con el log del que está corriendo (F4.7).
 *
 * El log no lo pide esta pantalla: llega por el stream que abre el Layout y se lee
 * de la caché con `useCycleControl`. Por eso se sigue moviendo aunque se cambie de
 * pestaña y se vuelva.
 */
export function Ciclos() {
  const { perfil, referencia } = usePerfilActivo();
  const [desplazamiento, setDesplazamiento] = useState(0);
  const [parametros, setParametros] = useSearchParams();
  const seleccionado = parametros.get("ciclo");

  const control = useCycleControl();
  const ciclos = useCycles(referencia, { limit: LIMITE, offset: desplazamiento });
  const simbolo = perfil?.currency_symbol ?? "";

  return (
    <>
      <h1 className="mb-5 text-[17px] font-semibold tracking-tight">Ciclos</h1>

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
      <div className="mb-6 rounded-lg border border-border bg-card p-4 text-[13px] text-text-secondary">
        Los controles de ciclo están apagados en el servidor
        (<code>API_CONTROLS=false</code>). Los ciclos los lanza el planificador.
      </div>
    );
  }

  return (
    <div className="mb-6 rounded-lg border border-border bg-card p-4">
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
          <button
            type="button"
            disabled={estado.running || lanzar.isPending || !perfil}
            onClick={() => lanzar.mutate({})}
            className="min-h-8 rounded-md border border-border bg-card px-3 py-1 text-[13px] hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-card"
          >
            Lanzar ciclo
          </button>
          {/* En seco: analiza y decide pero no ejecuta. Es la forma de ver qué
              haría el modelo sin mover la cartera del experimento. */}
          <button
            type="button"
            disabled={estado.running || lanzar.isPending || !perfil}
            onClick={() => lanzar.mutate({ dry_run: true })}
            className="min-h-8 rounded-md border border-border bg-card px-3 py-1 text-[13px] text-text-secondary hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-card"
          >
            Lanzar en seco
          </button>
          <button
            type="button"
            disabled={!estado.running || parar.isPending}
            onClick={() => parar.mutate()}
            className="min-h-8 rounded-md border border-border bg-card px-3 py-1 text-[13px] text-delta-bad hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-card"
          >
            Parar
          </button>
        </div>
      </div>

      {fallo && (
        <p className="mt-3 rounded-md border border-negative/40 p-2 text-[13px] text-negative">
          {fallo.message}
        </p>
      )}

      <Log lineas={estado.lines ?? []} />
    </div>
  );
}

/**
 * El log del ciclo en curso.
 *
 * **Solo baja sola si ya estabas abajo.** Un log que se autodesplaza siempre es
 * imposible de leer: en cuanto subes a mirar una línea, la siguiente te devuelve
 * al final. Con el umbral, quien sube se queda donde quiso y quien está al final
 * sigue viendo lo último.
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
      <button
        type="button"
        onClick={() => setAbierto((valor) => !valor)}
        aria-expanded={abierto}
        className="text-[13px] text-text-secondary underline decoration-border hover:decoration-current"
      >
        {abierto ? "Ocultar" : "Ver"} el log ({lineas.length} líneas)
      </button>
      {abierto && (
        <pre
          ref={caja}
          // `aria-live` en polite y no assertive: son cientos de líneas y un
          // lector de pantalla las anunciaría todas.
          aria-live="polite"
          className="mt-2 max-h-64 overflow-auto rounded-md bg-surface-sunken p-3 text-xs leading-relaxed whitespace-pre-wrap"
        >
          {lineas.join("\n")}
        </pre>
      )}
    </div>
  );
}

/** Un ciclo con la copia de los parámetros con los que corrió (F6.3). */
function Detalle({ id, onCerrar }: { id: string; onCerrar: () => void }) {
  const consulta = useCycle(id);

  return (
    <section className="mb-6 rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-[13px] font-semibold tracking-wide text-text-secondary uppercase">
          Detalle del ciclo
        </h2>
        <button
          type="button"
          onClick={onCerrar}
          className="text-[13px] text-text-secondary underline decoration-border hover:decoration-current"
        >
          Cerrar
        </button>
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

            {ciclo.error && (
              <p className="mt-3 rounded-md border border-negative/40 p-2 text-[13px] text-negative">
                {ciclo.error}
              </p>
            )}

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
                <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-surface-sunken p-3 text-xs">
                  {JSON.stringify(ciclo.settings, null, 2)}
                </pre>
              </details>
            )}
          </>
        )}
      </Seccion>
    </section>
  );
}

function Dato({ etiqueta, valor }: { etiqueta: string; valor: string }) {
  return (
    <div>
      <dt className="text-text-muted">{etiqueta}</dt>
      <dd className="tabular">{valor}</dd>
    </div>
  );
}

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
        <button
          type="button"
          onClick={onElegir}
          aria-current={seleccionado ? "true" : undefined}
          className={
            seleccionado
              ? "font-semibold underline"
              : "underline decoration-border hover:decoration-current"
          }
        >
          {fechaHora(ciclo.started_at)}
        </button>
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
