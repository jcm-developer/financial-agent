import { Link } from "react-router";

import { useCycles, usePositions } from "@/api/hooks";
import type { CycleRow, PositionRow, ProfileSummary } from "@/api/types";
import { CycleStatus } from "@/components/CycleStatus";
import { Alert, Figure, Loading, LINK_CLASSES, PageTitle } from "@/components/pieces";
import { PriceSource } from "@/components/PriceSource";
import { Section } from "@/components/Section";
import { TableHead, Row, Table, Td, Th, Empty } from "@/components/Table";
import {
  signClass,
  money,
  signedMoney,
  dateTime,
  percent,
} from "@/lib/format";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

/**
 * The experiment's summary (F4.7).
 *
 * The figures at the top come from `/api/profiles`, which already brings them
 * computed in `metrics`: asking for them again separately would risk the card
 * and the summary telling different stories about the same experiment.
 *
 * The equity curve and the rest of the charts arrive in stretch E (F4.6).
 *
 * @return The rendered screen.
 */
export function Summary() {
  const { profile, ref, loading, error } = useActiveProfile();
  useTitle("Resumen", profile?.name);
  const positions = usePositions(ref, { status: "open", limit: 100 });
  const cycles = useCycles(ref, { limit: 5 });

  if (loading) return <Loading />;
  if (error) return <Alert>{error.message}</Alert>;
  if (!profile) return null;

  return (
    <>
      <PageTitle aside={profile.risk_summary}>{profile.name}</PageTitle>

      <Figures profile={profile} />

      <Section title="Posiciones abiertas" query={positions}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>
              No hay ninguna posición abierta. Si el experimento acaba de empezar es lo
              normal: el agente abre solo cuando el analista propone y el Risk Manager
              aprueba.
            </Empty>
          ) : (
            <Table title="Posiciones abiertas del experimento">
              <TableHead>
                <Th>Símbolo</Th>
                <Th numeric>Cantidad</Th>
                <Th numeric>Entrada</Th>
                <Th numeric>Último</Th>
                <Th numeric>Valor</Th>
                <Th numeric>P&L</Th>
                <Th numeric>Stop</Th>
              </TableHead>
              <tbody>
                {page.items.map((row) => (
                  <OpenPositionTableRow
                    key={row.id}
                    row={row}
                    symbol={profile.currency_symbol}
                  />
                ))}
              </tbody>
            </Table>
          )
        }
      </Section>

      <Section title="Últimos ciclos" query={cycles}>
        {(page) =>
          page.items.length === 0 ? (
            <Empty>
              Todavía no ha corrido ningún ciclo. Se lanzan desde la pantalla de{" "}
              <Link
                className={LINK_CLASSES}
                to={`/p/${encodeURIComponent(profile.name)}/cycles`}
              >
                Ciclos
              </Link>{" "}
              o los programa el planificador.
            </Empty>
          ) : (
            <Table title="Últimos ciclos ejecutados">
              <TableHead>
                <Th>Inicio</Th>
                <Th>Estado</Th>
                <Th numeric>Decisiones</Th>
                <Th numeric>Órdenes</Th>
                <Th numeric>Δ capital</Th>
              </TableHead>
              <tbody>
                {page.items.map((cycle) => (
                  <CycleTableRow
                    key={cycle.id}
                    cycle={cycle}
                    profile={profile.name}
                    symbol={profile.currency_symbol}
                  />
                ))}
              </tbody>
            </Table>
          )
        }
      </Section>
    </>
  );
}

/**
 * The row of headline figures.
 *
 * @param props - Figures props.
 * @param props.profile - The profile, whose `metrics` already carry the figures
 *     computed, so this screen and the profile card cannot disagree.
 * @return The rendered row.
 */
function Figures({ profile }: { profile: ProfileSummary }) {
  const m = profile.metrics;
  const symbol = profile.currency_symbol;

  return (
    <div className="mb-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Figure label="Capital" value={money(m.equity, symbol)}>
        <span className="text-text-muted">
          de {money(m.initial_budget, symbol)} inicial
        </span>
      </Figure>
      <Figure
        label="Rentabilidad total"
        value={percent(m.total_return_pct, { sign: true })}
        className={signClass(m.total_return_pct)}
      >
        <span className="text-text-muted">
          contra el presupuesto asignado, no contra el primer día
        </span>
      </Figure>
      <Figure
        label="P&L del día"
        value={percent(m.day_pnl_pct, { sign: true })}
        className={signClass(m.day_pnl_pct)}
      />
      <Figure label="Posiciones abiertas" value={String(m.open_positions ?? 0)} />
      <Figure
        label="Operaciones cerradas"
        value={String(m.closed_trades ?? 0)}
      >
        {/* 30 is the minimum the README talks about before anything can be said
            about calibration; showing it stops conclusions being drawn from eight. */}
        <span className="text-text-muted">
          {(m.closed_trades ?? 0) < 30
            ? `faltan ${30 - (m.closed_trades ?? 0)} para 30, el mínimo para leer la calibración`
            : "suficientes para mirar la calibración"}
        </span>
      </Figure>
      <Figure
        label="Aciertos"
        value={percent(m.win_rate_pct)}
        className={
          m.win_rate_pct === null || m.win_rate_pct === undefined
            ? "text-text-muted"
            : undefined
        }
      />
      <Figure
        label="P&L realizado"
        value={signedMoney(m.realized_pnl, symbol)}
        className={signClass(m.realized_pnl)}
      />
      <Figure label="Último ciclo" value={dateTime(m.last_cycle_at)}>
        <span
          className={
            m.last_cycle_status === "failed"
              ? "font-semibold text-delta-bad"
              : "text-text-muted"
          }
        >
          {m.last_cycle_status ?? "ninguno"}
          {/* A 'failed' cycle can be F6.9: the analyst got no answer. The detail
              is on the Cycles screen. */}
        </span>
      </Figure>
    </div>
  );
}

/**
 * One row of the open-positions table on the summary, which carries fewer
 * columns than the one on the positions screen.
 *
 * @param props - Row props.
 * @param props.row - The position.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function OpenPositionTableRow({ row, symbol }: { row: PositionRow; symbol: string }) {
  return (
    <Row>
      <Td>
        <span className="font-medium">{row.symbol}</span>
      </Td>
      <Td numeric>{row.qty}</Td>
      <Td numeric>{money(row.entry_price, symbol)}</Td>
      <Td numeric>
        {money(row.last_price, symbol)}
        <PriceSource row={row} />
      </Td>
      <Td numeric>{money(row.market_value, symbol)}</Td>
      <Td numeric className={signClass(row.unrealized_pnl)}>
        {signedMoney(row.unrealized_pnl, symbol)}
        <span className="ml-1 text-caption">{percent(row.unrealized_pnl_pct, { sign: true })}</span>
      </Td>
      <Td numeric>
        {money(row.stop_price, symbol)}
        {row.stop_distance_pct !== null && row.stop_distance_pct !== undefined && (
          <span className="ml-1 text-caption text-text-muted">
            {percent(row.stop_distance_pct)}
          </span>
        )}
      </Td>
    </Row>
  );
}

/**
 * One row of the recent-cycles table on the summary.
 *
 * @param props - Row props.
 * @param props.cycle - The cycle.
 * @param props.profile - Profile name, needed to link to the cycles screen.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @return The rendered row.
 */
function CycleTableRow({
  cycle,
  profile,
  symbol,
}: {
  cycle: CycleRow;
  profile: string;
  symbol: string;
}) {
  return (
    <Row>
      <Td>
        <Link
          className={LINK_CLASSES}
          to={`/p/${encodeURIComponent(profile)}/cycles?cycle=${encodeURIComponent(cycle.id)}`}
        >
          {dateTime(cycle.started_at)}
        </Link>
      </Td>
      <Td>
        <CycleStatus cycle={cycle} />
      </Td>
      <Td numeric>{cycle.decisions ?? 0}</Td>
      <Td numeric>{cycle.orders ?? 0}</Td>
      <Td numeric className={signClass(cycle.equity_delta)}>
        {signedMoney(cycle.equity_delta, symbol)}
      </Td>
    </Row>
  );
}
