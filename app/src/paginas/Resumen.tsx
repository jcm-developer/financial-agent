import { Link } from "react-router";

import { useCycles, usePositions } from "@/api/hooks";
import type { CycleRow, PositionRow, ProfileSummary } from "@/api/types";
import { EstadoCiclo } from "@/components/EstadoCiclo";
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
 */
export function Resumen() {
  const { perfil, referencia, cargando, error } = usePerfilActivo();
  useTitulo("Resumen", perfil?.name);
  const posiciones = usePositions(referencia, { status: "open", limit: 100 });
  const ciclos = useCycles(referencia, { limit: 5 });

  if (cargando) return <p className="text-[13px] text-text-muted">Cargando…</p>;
  if (error) return <p className="text-[13px] text-negative-ink">{error.message}</p>;
  if (!perfil) return null;

  return (
    <>
      <div className="mb-5 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-[17px] font-semibold tracking-tight">{perfil.name}</h1>
        <p className="text-[13px] text-text-secondary">{perfil.risk_summary}</p>
      </div>

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
              <Link className="underline" to={`/p/${encodeURIComponent(perfil.name)}/ciclos`}>
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
    <div className="rounded-lg border border-border bg-card p-3 shadow-[var(--shadow-card)]">
      <p className="text-xs text-text-muted">{etiqueta}</p>
      <p className={cn("tabular mt-0.5 text-[17px] font-semibold", clase)}>{valor}</p>
      {children && <p className="mt-1 text-xs leading-snug">{children}</p>}
    </div>
  );
}

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
        {/* De dónde sale el precio (F3.2). Sumar una posición valorada con el
            cierre de anteayer y otra con el precio de hace un minuto da un P&L
            que no significa nada, así que se dice cuál es cuál. */}
        {fila.price_source && (
          <span
            className={cn(
              "ml-1.5 align-middle text-[10px] font-semibold",
              fila.price_source === "live" ? "text-delta-good" : "text-warning",
            )}
            title={
              fila.price_source === "live"
                ? "Cotización del ingestor, de hace minutos"
                : "El precio que vio el analista en su último ciclo, no en vivo"
            }
          >
            {fila.price_source === "live" ? "VIVO" : "CICLO"}
          </span>
        )}
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
          className="underline decoration-border hover:decoration-current"
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

