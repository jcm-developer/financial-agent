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
import { signClass, signedMoney, duration, dateTime, sentence } from "@/lib/format";
import { useActiveProfile } from "@/profile/useActiveProfile";
import { useTitle } from "@/layout/useTitle";

const LIMIT = 30;

/**
 * How far from the end of the log still counts as "at the end", in pixels.
 *
 * It is about one line at `text-code`: enough to absorb the sub-pixel rounding
 * of `scrollHeight`, and little enough that one notch of the wheel is already a
 * decision to stop being carried along.
 */
const LOG_BOTTOM_SLACK = 24;

/**
 * Cycles that have run, with the log of the one running (F4.7).
 *
 * This screen does not request the log: it arrives over the stream the Layout
 * opens and is read from the cache with `useCycleControl`. That is why it keeps
 * moving even after switching tabs and coming back.
 *
 * Since F4.22 the log is the one of **whatever** cycle is running and not only of
 * the one the API launched: the server reads it from a file in the shared volume,
 * so a cycle from the scheduler's container is no longer a blank panel.
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
  // `running` is "the cycle is mine" and `external` is "it is the scheduler's",
  // and the two flags stay separate because they answer different questions
  // (F4.19). For everything the panel says out loud there is only one question —
  // is a cycle running? — so it is asked once and answered here.
  const live = state.running || state.external;

  if (!state.enabled) {
    return (
      <Card className="mb-6 text-body-sm text-text-secondary">
        Los controles de ciclo están apagados en el servidor
        (<code>API_CONTROLS=false</code>). Los ciclos los lanza el planificador.
      </Card>
    );
  }

  return (
    <Card className="mb-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-body-sm">
            {/* The label says whether a cycle is running and the stage says what
                it is doing, and who launched it belongs to the second: the
                parenthesis said "(del planificador)" right before a stage that
                repeated "lanzado por el planificador" word for word. */}
            <span
              className={live ? "font-semibold text-warning" : "text-text-secondary"}
            >
              {live ? "Ciclo en marcha" : "Sin ciclo en marcha"}
            </span>
            {" — "}
            {sentence(state.stage)}
            {live && state.elapsed_seconds !== null && state.elapsed_seconds !== undefined
              ? ` · ${duration(state.elapsed_seconds)}`
              : ""}
          </p>
          {state.profile && (
            <p className="mt-0.5 text-caption text-text-muted">
              perfil {state.profile}
              {state.dry_run ? " · en seco" : ""}
              {state.returncode !== null && state.returncode !== undefined
                ? ` · terminó con código ${state.returncode}`
                : ""}
            </p>
          )}
          {/* The request leaves no line in the log —it is a file the cycle has
              not read yet— so without saying it here the panel would look
              untouched after pressing Parar, and a button that seems to have done
              nothing gets pressed again. */}
          {state.stop_requested && (
            <p className="mt-0.5 text-caption font-semibold text-warning">
              Parada pedida: el ciclo se detendrá en su siguiente punto de control.
            </p>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={live || run.isPending || !profile}
            onClick={() => run.mutate({})}
          >
            Lanzar ciclo
          </Button>
          {/* Dry run: it analyses and decides but does not execute. It is how to
              see what the model would do without moving the experiment's book. */}
          <Button
            variant="ghost"
            disabled={live || run.isPending || !profile}
            onClick={() => run.mutate({ dry_run: true })}
          >
            Lanzar en seco
          </Button>
          {/* Enabled for the scheduler's cycle too since F4.21: the request
              travels through the database and not through a signal, so it reaches
              the other container. What it cannot promise is that it is instant,
              and that is what the title says instead of leaving it to be guessed. */}
          <Button
            variant="destructive"
            disabled={!live || state.stop_requested || stop.isPending}
            title={
              state.stop_requested
                ? "La parada ya está pedida: el ciclo se detendrá en su siguiente punto de control"
                : !live
                  ? "No hay ningún ciclo en marcha que parar"
                  : "El ciclo se detiene en su siguiente punto de control, antes de la próxima consulta al modelo, y cierra su registro con el motivo"
            }
            onClick={() => stop.mutate()}
          >
            Parar
          </Button>
          {/* F5.8. It lives beside the cycle controls and not in the profile
              actions because it IS an operation on the book —it sells— and it
              shares the log and the lock with the cycle: only one thing at a
              time may touch a book. */}
          <Button
            variant="destructive"
            disabled={live || close.isPending || !profile}
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
 * ⚠️ **Whether you were at the bottom has to be remembered, not measured after
 * the fact**, and the first version measured it after the fact: it read the
 * distance to the end inside the effect, when the new text was already in the
 * DOM and had already pushed the end away. One poll brings several lines and
 * each of them wraps into two or three at this width, so the distance was
 * comfortably past any sane threshold and the log let go on the first refresh —
 * which is the same as never scrolling on its own at all. Now `stick` is
 * written by the scroll handler, that is, by the reader, and the effect only
 * obeys it. Setting `scrollTop` fires a scroll of its own that puts `stick`
 * back to true, which is exactly right.
 *
 * @param props - Log props.
 * @param props.lines - Lines emitted so far, in order.
 * @return The rendered log.
 */
function Log({ lines }: { lines: string[] }) {
  const box = useRef<HTMLPreElement>(null);
  /** Whether the reader is parked at the end and wants to be carried along. */
  const stick = useRef(true);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    const element = box.current;
    if (!element || !stick.current) return;
    element.scrollTop = element.scrollHeight;
  }, [lines, open]);

  if (!lines.length) return null;

  return (
    <div className="mt-3">
      <LinkButton
        onClick={() => {
          // Reopening the log means wanting to see it, and the box comes back
          // scrolled to the top because it was unmounted: without this it opens
          // showing the beginning of a cycle that is halfway through.
          if (!open) stick.current = true;
          setOpen((value) => !value);
        }}
        aria-expanded={open}
      >
        {open ? "Ocultar" : "Ver"} el log ({lines.length} líneas)
      </LinkButton>
      {open && (
        <Block
          ref={box}
          onScroll={(event) => {
            const element = event.currentTarget;
            stick.current =
              element.scrollHeight - element.scrollTop - element.clientHeight <
              LOG_BOTTOM_SLACK;
          }}
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
            <dl className="grid gap-x-6 gap-y-1 text-body-sm sm:grid-cols-3">
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
              <p className="mt-3 text-body-sm text-text-muted">
                Sin copia de los parámetros: es un ciclo anterior a que se guardaran (F6.3).
              </p>
            ) : (
              <details className="mt-3">
                <summary className="cursor-pointer text-body-sm text-text-secondary">
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
          variant="subtle"
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
