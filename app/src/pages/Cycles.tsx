import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import {
  useCloseExperiment,
  useCycle,
  useCycleControl,
  useCycles,
  useRunCycle,
  useStopCycle,
} from "@/api/hooks";
import type { CycleControl, CycleRow } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CycleStatus } from "@/components/CycleStatus";
import {
  Alert,
  Block,
  Button,
  LinkButton,
  Card,
  PageTitle,
  SectionTitle,
  Stat,
} from "@/components/pieces";
import { Section } from "@/components/Section";
import { TableHead, Row, Pagination, Table, Td, Th, Empty } from "@/components/Table";
import { signClass, signedMoney, duration, dateTime } from "@/lib/format";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

const LIMIT = 30;

/**
 * Cycles that have run, with the log of the one running (F4.7).
 *
 * This screen does not request the log: it arrives over the stream the Layout
 * opens and is read from the cache with `useCycleControl`. That is why it keeps
 * moving even after switching tabs and coming back.
 *
 * @return The rendered screen.
 */
export function Cycles() {
  const { profile, ref } = useActiveProfile();
  useTitle("Ciclos", profile?.name);
  const [offset, setOffset] = useState(0);
  const [params, setParams] = useSearchParams();
  const selected = params.get("cycle");

  const control = useCycleControl();
  const cycles = useCycles(ref, { limit: LIMIT, offset });
  const symbol = profile?.currency_symbol ?? "";

  return (
    <>
      <PageTitle>Ciclos</PageTitle>

      {control.data && <Control state={control.data} profile={ref} />}

      {selected && (
        <Detail
          id={selected}
          onClose={() => {
            params.delete("cycle");
            setParams(params, { replace: true });
          }}
        />
      )}

      <Section title="Histórico" query={cycles}>
        {(page) => (
          <>
            {page.items.length === 0 ? (
              <Empty>
                Todavía no ha corrido ningún ciclo para este experimento.
              </Empty>
            ) : (
              <Table title="Ciclos ejecutados">
                <TableHead>
                  <Th>Inicio</Th>
                  <Th numeric>Duración</Th>
                  <Th>Estado</Th>
                  <Th>Mercado</Th>
                  <Th numeric>Decisiones</Th>
                  <Th numeric>Aprob.</Th>
                  <Th numeric>Rechaz.</Th>
                  <Th numeric>Órdenes</Th>
                  <Th numeric>Δ capital</Th>
                </TableHead>
                <tbody>
                  {page.items.map((cycle) => (
                    <CycleTableRow
                      key={cycle.id}
                      cycle={cycle}
                      symbol={symbol}
                      selected={cycle.id === selected}
                      onSelect={() => {
                        params.set("cycle", cycle.id);
                        setParams(params, { replace: true });
                      }}
                    />
                  ))}
                </tbody>
              </Table>
            )}
            <Pagination
              total={page.total}
              limit={page.limit}
              offset={page.offset}
              onChange={setOffset}
            />
          </>
        )}
      </Section>
    </>
  );
}

/**
 * The cycle control panel (F3.4).
 *
 * `enabled` is checked because the controls can be switched off with
 * `API_CONTROLS=false` (F3.8). When they are off **it says so**, instead of
 * showing buttons that would return an error: a button that always fails is
 * worse than no button.
 *
 * @param props - Control props.
 * @param props.state - Cycle control state, including whether it is enabled.
 * @param props.profile - Profile the cycle would run against.
 * @return The rendered panel, or the notice that the controls are switched off.
 */
function Control({
  state,
  profile,
}: {
  state: CycleControl;
  profile: string | undefined;
}) {
  const run = useRunCycle(profile);
  const stop = useStopCycle();
  const close = useCloseExperiment(profile);
  const [closing, setClosing] = useState(false);
  const failure = run.error ?? stop.error ?? close.error;

  if (!state.enabled) {
    return (
      <Card className="mb-6 text-[13px] text-text-secondary">
        Los controles de ciclo están apagados en el servidor
        (<code>API_CONTROLS=false</code>). Los ciclos los lanza el planificador.
      </Card>
    );
  }

  return (
    <Card className="mb-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[13px]">
            <span className={state.running ? "font-semibold text-warning" : "text-text-secondary"}>
              {state.running ? "Ciclo en marcha" : "Sin ciclo en marcha"}
            </span>
            {" — "}
            {state.stage}
            {state.running && state.elapsed_seconds !== null && state.elapsed_seconds !== undefined
              ? ` · ${duration(state.elapsed_seconds)}`
              : ""}
          </p>
          {state.profile && (
            <p className="mt-0.5 text-xs text-text-muted">
              perfil {state.profile}
              {state.dry_run ? " · en seco" : ""}
              {state.returncode !== null && state.returncode !== undefined
                ? ` · terminó con código ${state.returncode}`
                : ""}
            </p>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            disabled={state.running || run.isPending || !profile}
            onClick={() => run.mutate({})}
          >
            Lanzar ciclo
          </Button>
          {/* Dry run: it analyses and decides but does not execute. It is how to
              see what the model would do without moving the experiment's book. */}
          <Button
            variant="subtle"
            disabled={state.running || run.isPending || !profile}
            onClick={() => run.mutate({ dry_run: true })}
          >
            Lanzar en seco
          </Button>
          <Button
            variant="danger"
            disabled={!state.running || stop.isPending}
            onClick={() => stop.mutate()}
          >
            Parar
          </Button>
          {/* F5.8. It lives beside the cycle controls and not in the profile
              actions because it IS an operation on the book —it sells— and it
              shares the log and the lock with the cycle: only one thing at a
              time may touch a book. */}
          <Button
            variant="danger"
            disabled={state.running || close.isPending || !profile}
            onClick={() => setClosing(true)}
          >
            Cerrar experimento
          </Button>
        </div>
      </div>

      {failure && <Alert className="mt-3">{failure.message}</Alert>}

      <Log lines={state.lines ?? []} />

      <ConfirmDialog
        open={closing}
        title={`Cerrar ${profile ?? "el experimento"}`}
        confirmLabel="Vender todo y cerrar"
        danger
        busy={close.isPending}
        onConfirm={async () => {
          try {
            await close.mutateAsync();
            setClosing(false);
          } catch {
            // The message is already on the alert above; the dialog stays open
            // so the reason is read where the decision was taken.
          }
        }}
        onCancel={() => setClosing(false)}
      >
        <p>
          Vende <strong className="font-semibold">todas las posiciones abiertas</strong> por
          el broker, a la apertura de la barra siguiente y con el mismo deslizamiento que
          cualquier otra venta. A partir de ahí el resultado es <strong
          className="font-semibold">realizado</strong>, no una cartera valorada a mercado.
        </p>
        <p>
          {/* Said out loud because it is the question anyone asks here: no, the
              model does not get to weigh in. The experiment is over. */}
          No se consulta al modelo: no es una decisión de mercado, es el final del
          experimento. Queda registrado como un ciclo más, con la regla
          <code> experiment_closed</code>.
        </p>
        <p className="text-text-muted">
          No se puede deshacer, y con el mercado cerrado no se puede hacer: no habría precio
          al que vender.
        </p>
      </ConfirmDialog>
    </Card>
  );
}

/**
 * The log of the running cycle.
 *
 * **It only scrolls down on its own if you were already at the bottom.** A log
 * that always autoscrolls is impossible to read: the moment you scroll up to
 * look at a line, the next one drags you back to the end. With the threshold,
 * whoever scrolls up stays where they wanted and whoever is at the end keeps
 * seeing the latest.
 *
 * @param props - Log props.
 * @param props.lines - Lines emitted so far, in order.
 * @return The rendered log.
 */
function Log({ lines }: { lines: string[] }) {
  const box = useRef<HTMLPreElement>(null);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const atBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight < 40;
    if (atBottom) element.scrollTop = element.scrollHeight;
  }, [lines]);

  if (!lines.length) return null;

  return (
    <div className="mt-3">
      <LinkButton onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        {open ? "Ocultar" : "Ver"} el log ({lines.length} líneas)
      </LinkButton>
      {open && (
        <Block
          ref={box}
          // `aria-live` polite and not assertive: there are hundreds of lines and
          // a screen reader would announce them all.
          aria-live="polite"
          className="mt-2 max-h-64 leading-relaxed whitespace-pre-wrap"
        >
          {lines.join("\n")}
        </Block>
      )}
    </div>
  );
}

/**
 * One cycle, with the copy of the settings it ran under (F6.3).
 *
 * @param props - Detail props.
 * @param props.id - Cycle id.
 * @param props.onClose - Called when the panel is dismissed.
 * @return The rendered panel.
 */
function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const query = useCycle(id);

  return (
    <Card as="section" className="mb-6">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <SectionTitle>Detalle del ciclo</SectionTitle>
        <LinkButton onClick={onClose}>Cerrar</LinkButton>
      </div>

      <Section query={query}>
        {(cycle) => (
          <>
            <dl className="grid gap-x-6 gap-y-1 text-[13px] sm:grid-cols-3">
              <Stat label="Inicio" value={dateTime(cycle.started_at)} />
              <Stat label="Fin" value={dateTime(cycle.finished_at)} />
              <Stat label="Modelo" value={cycle.llm_model ?? "—"} />
              <Stat
                label="Llamadas al analista"
                value={
                  (cycle.analyst_calls ?? 0) === 0
                    ? "ninguna"
                    : `${cycle.analyst_calls} (${cycle.analyst_failures ?? 0} sin respuesta)`
                }
              />
              <Stat
                label="Símbolos analizados"
                value={String(cycle.symbols_scanned?.length ?? 0)}
              />
              <Stat label="Mercado" value={cycle.market_open ? "abierto" : "cerrado"} />
            </dl>

            {cycle.error && <Alert className="mt-3">{cycle.error}</Alert>}

            {/* Settings from before F6.3 come back null. That is missing
                information, not a zero: whoever compares experiments needs to
                tell "it ran with these settings" from "we do not know which
                settings it ran with". */}
            {cycle.settings === null || cycle.settings === undefined ? (
              <p className="mt-3 text-[13px] text-text-muted">
                Sin copia de los parámetros: es un ciclo anterior a que se guardaran (F6.3).
              </p>
            ) : (
              <details className="mt-3">
                <summary className="cursor-pointer text-[13px] text-text-secondary">
                  Parámetros con los que corrió
                </summary>
                <Block className="mt-2 max-h-64">
                  {JSON.stringify(cycle.settings, null, 2)}
                </Block>
              </details>
            )}
          </>
        )}
      </Section>
    </Card>
  );
}

/**
 * One row of the cycles table.
 *
 * @param props - Row props.
 * @param props.cycle - The cycle.
 * @param props.symbol - Currency symbol of the profile's market, never assumed.
 * @param props.selected - Whether its detail panel is open.
 * @param props.onSelect - Called when the row is chosen.
 * @return The rendered row.
 */
function CycleTableRow({
  cycle,
  symbol,
  selected,
  onSelect,
}: {
  cycle: CycleRow;
  symbol: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <Row>
      <Td>
        <LinkButton
          variant="neutral"
          onClick={onSelect}
          aria-current={selected ? "true" : undefined}
          className={selected ? "font-semibold decoration-current" : undefined}
        >
          {dateTime(cycle.started_at)}
        </LinkButton>
      </Td>
      <Td numeric>
        {cycle.finished_at
          ? duration(
              (new Date(cycle.finished_at).getTime() -
                new Date(cycle.started_at).getTime()) /
                1000,
            )
          : "—"}
      </Td>
      <Td>
        <CycleStatus cycle={cycle} />
      </Td>
      <Td>
        <span className={cycle.market_open ? "text-text-secondary" : "text-text-muted"}>
          {cycle.market_open ? "abierto" : "cerrado"}
        </span>
      </Td>
      <Td numeric>{cycle.decisions ?? 0}</Td>
      <Td numeric>{cycle.approved ?? 0}</Td>
      <Td numeric>{cycle.rejected ?? 0}</Td>
      <Td numeric>{cycle.orders ?? 0}</Td>
      <Td numeric className={signClass(cycle.equity_delta)}>
        {signedMoney(cycle.equity_delta, symbol)}
      </Td>
    </Row>
  );
}
