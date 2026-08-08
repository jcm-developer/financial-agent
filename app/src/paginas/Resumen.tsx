import { Link } from "react-router";

import { useCycles, usePositions } from "@/api/hooks";
import type { CycleRow, PositionRow, ProfileSummary } from "@/api/types";
import { EstadoCiclo } from "@/components/EstadoCiclo";
import { Aviso, Cargando, CLASES_ENLACE, Tarjeta, TituloPagina } from "@/components/piezas";
import { Procedencia } from "@/components/Procedencia";
import { Seccion } from "@/components/Seccion";
import { Cabecera, Fila, Tabla, Td, Th, Vacio } from "@/components/Tabla";
import {
  claseSigno,
  dinero,
  dineroConSigno,
  fechaHora,
  porcentaje,
} from "@/lib/formato";
import { cn } from "@/lib/utils";
import { usePerfilActivo } from "@/perfil/usePerfilActivo";
import { useTitulo } from "@/layout/useTitulo";

/**
 * Resumen del experimento (F4.7).
 *
 * Las cifras de arriba salen de `/api/profiles`, que ya las trae calculadas en
 * `metrics`: pedirlas otra vez por separado seria arriesgarse a que la tarjeta y
 * el resumen contaran cosas distintas del mismo experimento.
 *
 * La curva de capital y el resto de graficas llegan en el tramo E (F4.6).
 *
 * @return The rendered screen.
 */
export function Resumen() {
  const { perfil, referencia, cargando, error } = usePerfilActivo();
  useTitulo("Resumen", perfil?.name);
  const posiciones = usePositions(referencia, { status: "open", limit: 100 });
  const ciclos = useCycles(referencia, { limit: 5 });

  if (cargando) return <Cargando />;
  if (error) return <Aviso>{error.message}</Aviso>;
  if (!perfil) return null;

  return (
    <>
      <TituloPagina secundario={perfil.risk_summary}>{perfil.name}</TituloPagina>

      <Cifras perfil={perfil} />

      <Seccion titulo="Posiciones abiertas" consulta={posiciones}>
        {(pagina) =>
          pagina.items.length === 0 ? (
            <Vacio>
              No hay ninguna posición abierta. Si el experimento acaba de empezar es lo
              normal: el agente abre solo cuando el analista propone y el Risk Manager
              aprueba.
            </Vacio>
          ) : (
            <Tabla titulo="Posiciones abiertas del experimento">
              <Cabecera>
                <Th>Símbolo</Th>
                <Th numerica>Cantidad</Th>
                <Th numerica>Entrada</Th>
                <Th numerica>Último</Th>
                <Th numerica>Valor</Th>
                <Th numerica>P&L</Th>
                <Th numerica>Stop</Th>
              </Cabecera>
              <tbody>
                {pagina.items.map((fila) => (
                  <FilaAbierta
                    key={fila.id}
                    fila={fila}
                    simbolo={perfil.currency_symbol}
                  />
                ))}
              </tbody>
            </Tabla>
          )
        }
      </Seccion>

      <Seccion titulo="Últimos ciclos" consulta={ciclos}>
        {(pagina) =>
          pagina.items.length === 0 ? (
            <Vacio>
              Todavía no ha corrido ningún ciclo. Se lanzan desde la pantalla de{" "}
              <Link
                className={CLASES_ENLACE}
                to={`/p/${encodeURIComponent(perfil.name)}/ciclos`}
              >
                Ciclos
              </Link>{" "}
              o los programa el planificador.
            </Vacio>
          ) : (
            <Tabla titulo="Últimos ciclos ejecutados">
              <Cabecera>
                <Th>Inicio</Th>
                <Th>Estado</Th>
                <Th numerica>Decisiones</Th>
                <Th numerica>Órdenes</Th>
                <Th numerica>Δ capital</Th>
              </Cabecera>
              <tbody>
                {pagina.items.map((ciclo) => (
                  <FilaCiclo
                    key={ciclo.id}
                    ciclo={ciclo}
                    perfil={perfil.name}
                    simbolo={perfil.currency_symbol}
                  />
                ))}
              </tbody>
            </Tabla>
          )
        }
      </Seccion>
    </>
  );
}

/**
 * The row of headline figures.
 *
 * @param props - Figures props.
 * @param props.perfil - The profile, whose `metrics` already carry the figures
 *     computed, so this screen and the profile card cannot disagree.
 * @return The rendered row.
 */
function Cifras({ perfil }: { perfil: ProfileSummary }) {
  const m = perfil.metrics;
  const simbolo = perfil.currency_symbol;

  return (
    <div className="mb-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Cifra etiqueta="Capital" valor={dinero(m.equity, simbolo)}>
        <span className="text-text-muted">
          de {dinero(m.initial_budget, simbolo)} inicial
        </span>
      </Cifra>
      <Cifra
        etiqueta="Rentabilidad total"
        valor={porcentaje(m.total_return_pct, { signo: true })}
        clase={claseSigno(m.total_return_pct)}
      >
        <span className="text-text-muted">
          contra el presupuesto asignado, no contra el primer día
        </span>
      </Cifra>
      <Cifra
        etiqueta="P&L del día"
        valor={porcentaje(m.day_pnl_pct, { signo: true })}
        clase={claseSigno(m.day_pnl_pct)}
      />
      <Cifra etiqueta="Posiciones abiertas" valor={String(m.open_positions ?? 0)} />
      <Cifra
        etiqueta="Operaciones cerradas"
        valor={String(m.closed_trades ?? 0)}
      >
        {/* 30 es el mínimo del que habla el README para poder decir algo sobre la
            calibración; enseñarlo evita sacar conclusiones con ocho. */}
        <span className="text-text-muted">
          {(m.closed_trades ?? 0) < 30
            ? `faltan ${30 - (m.closed_trades ?? 0)} para 30, el mínimo para leer la calibración`
            : "suficientes para mirar la calibración"}
        </span>
      </Cifra>
      <Cifra
        etiqueta="Aciertos"
        valor={porcentaje(m.win_rate_pct)}
        clase={
          m.win_rate_pct === null || m.win_rate_pct === undefined
            ? "text-text-muted"
            : undefined
        }
      />
      <Cifra
        etiqueta="P&L realizado"
        valor={dineroConSigno(m.realized_pnl, simbolo)}
        clase={claseSigno(m.realized_pnl)}
      />
      <Cifra etiqueta="Último ciclo" valor={fechaHora(m.last_cycle_at)}>
        <span
          className={
            m.last_cycle_status === "failed"
              ? "font-semibold text-delta-bad"
              : "text-text-muted"
          }
        >
          {m.last_cycle_status ?? "ninguno"}
          {/* Un ciclo 'failed' puede ser F6.9: el analista se quedó sin
              respuesta. El detalle está en la pantalla de Ciclos. */}
        </span>
      </Cifra>
    </div>
  );
}

/**
 * One headline figure, as a card.
 *
 * @param props - Figure props.
 * @param props.etiqueta - Label, in the interface language.
 * @param props.valor - The figure, already formatted with its currency symbol.
 * @param props.clase - Extra classes for the figure, to colour it by sign.
 * @param props.children - The footnote below it, when the figure needs one.
 * @return The rendered card.
 */
function Cifra({
  etiqueta,
  valor,
  clase,
  children,
}: {
  etiqueta: string;
  valor: string;
  clase?: string;
  children?: React.ReactNode;
}) {
  return (
    <Tarjeta relleno="p-3">
      <p className="text-xs text-text-muted">{etiqueta}</p>
      <p className={cn("tabular mt-0.5 text-[17px] font-semibold", clase)}>{valor}</p>
      {children && <p className="mt-1 text-xs leading-snug">{children}</p>}
    </Tarjeta>
  );
}

/**
 * One row of the open-positions table on the summary, which carries fewer
 * columns than the one on the positions screen.
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
      </Td>
      <Td numerica>{fila.qty}</Td>
      <Td numerica>{dinero(fila.entry_price, simbolo)}</Td>
      <Td numerica>
        {dinero(fila.last_price, simbolo)}
        <Procedencia fila={fila} />
      </Td>
      <Td numerica>{dinero(fila.market_value, simbolo)}</Td>
      <Td numerica className={claseSigno(fila.unrealized_pnl)}>
        {dineroConSigno(fila.unrealized_pnl, simbolo)}
        <span className="ml-1 text-xs">{porcentaje(fila.unrealized_pnl_pct, { signo: true })}</span>
      </Td>
      <Td numerica>
        {dinero(fila.stop_price, simbolo)}
        {fila.stop_distance_pct !== null && fila.stop_distance_pct !== undefined && (
          <span className="ml-1 text-xs text-text-muted">
            {porcentaje(fila.stop_distance_pct)}
          </span>
        )}
      </Td>
    </Fila>
  );
}

/**
 * One row of the recent-cycles table on the summary.
 *
 * @param props - Row props.
 * @param props.ciclo - The cycle.
 * @param props.perfil - Profile name, needed to link to the cycles screen.
 * @param props.simbolo - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function FilaCiclo({
  ciclo,
  perfil,
  simbolo,
}: {
  ciclo: CycleRow;
  perfil: string;
  simbolo: string;
}) {
  return (
    <Fila>
      <Td>
        <Link
          className={CLASES_ENLACE}
          to={`/p/${encodeURIComponent(perfil)}/ciclos?ciclo=${encodeURIComponent(ciclo.id)}`}
        >
          {fechaHora(ciclo.started_at)}
        </Link>
      </Td>
      <Td>
        <EstadoCiclo ciclo={ciclo} />
      </Td>
      <Td numerica>{ciclo.decisions ?? 0}</Td>
      <Td numerica>{ciclo.orders ?? 0}</Td>
      <Td numerica className={claseSigno(ciclo.equity_delta)}>
        {dineroConSigno(ciclo.equity_delta, simbolo)}
      </Td>
    </Fila>
  );
}

