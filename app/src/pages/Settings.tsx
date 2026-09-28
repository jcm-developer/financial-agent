import { useState } from "react";
import { ChevronRight } from "lucide-react";

import {
  useCloseExperiment,
  useCycleControl,
  useLimitsPreview,
  useProfileSettings,
  useUpdateSettings,
} from "@/api/hooks";
import type { AgentSettings, DerivedLimits, SettingsUpdate } from "@/api/types";
import { DerivedLimitsPanel } from "@/components/DerivedLimitsPanel";
import { Checkbox } from "@/components/Checkbox";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  Alert,
  Button,
  Card,
  Input,
  PageTitle,
  SectionTitle,
  Slider,
} from "@/components/pieces";
import { Select } from "@/components/Select";
import { Section } from "@/components/Section";
import { useTitle } from "@/layout/useTitle";
import { cn } from "@/lib/utils";
import { useActiveProfile } from "@/profile/useActiveProfile";

/**
 * The experiment's parameters.
 *
 * **What defines the experiment is on screen, and the rest is folded away.**
 * The risk slider and the horizon decide the eleven hard limits —the panel beside
 * them shows those limits while the slider moves—, and next to them sit the few
 * other things that make one experiment different from the next: the model, the
 * cycle's clock and whether it reads news. Everything else lives under
 * "Configuración avanzada", closed by default.
 *
 * ⚠️ **One slider, not two.** The number of positions follows from the risk
 * level (`src/risk_presets.py`); the diversification column is still there and
 * the API still carries it, but this screen no longer shows it or sends it.
 *
 * ⚠️ **Five fields are gone because nothing reads them**: the benchmark, the
 * cash reserve, the excluded sectors, "allow shorts" and the analyst's persona.
 * A field that looks like it configures the experiment and does not is worse
 * than a missing one. They come back when something reads them.
 *
 * **Nothing is derived in the browser.** The limits come from the API, which
 * runs the same `resolve_limits` as the cycle; a TypeScript copy would disagree
 * the day an anchor is tweaked.
 *
 * ⚠️ **Only what changed is sent.** `agent_settings_history` records real
 * changes only, and sending every field on every save would fill it with rows
 * saying "5 → 5".
 *
 * @return The rendered screen.
 */
export function Settings() {
  const { profile, ref, loading, error } = useActiveProfile();
  useTitle("Ajustes", profile?.name);
  const bundle = useProfileSettings(ref);

  if (loading) return <Section query={{ isPending: true, error: null }}>{() => null}</Section>;
  if (error) return <Alert>{error.message}</Alert>;
  if (!profile) return null;

  return (
    <>
      <PageTitle aside={profile.risk_summary}>Ajustes de {profile.name}</PageTitle>
      <Section query={bundle}>
        {(data) => (
          <SettingsForm
            key={data.settings.updated_at}
            profileRef={profile.name}
            settings={data.settings}
            effective={data.limits}
            symbol={profile.currency_symbol}
          />
        )}
      </Section>
      <CloseExperiment profile={profile.name} />
    </>
  );
}

/**
 * «Cerrar experimento», at the foot of the screen and set apart (F5.8).
 *
 * **It used to sit beside «Lanzar ciclo»** on the cycles panel, on the argument
 * that it is an operation on the book and shares the cycle's lock. Both are
 * still true, and neither is a reason to put a red, irreversible button one
 * click from the everyday one. Here it is the last thing on the screen that
 * defines the experiment, under its own heading, which is where an ending
 * belongs.
 *
 * It asks the cycle control state whether anything is running, from the same
 * cache the stream writes, so it is disabled for exactly as long as it was on
 * the cycles panel. Its log still appears there: it writes the cycle's file.
 *
 * @param props - Props.
 * @param props.profile - Profile name, as it travels in the URL.
 * @return The rendered block, or nothing when the server has the controls off.
 */
function CloseExperiment({ profile }: { profile: string }) {
  const control = useCycleControl();
  const close = useCloseExperiment(profile);
  const [open, setOpen] = useState(false);

  const state = control.data;
  if (!state?.enabled) return null;
  const live = state.running || state.external;

  return (
    <section className="mt-12 border-t border-border pt-8">
      <SectionTitle className="mb-3">Terminar el experimento</SectionTitle>
      <Card padding="p-6" className="flex flex-wrap items-center justify-between gap-4 border-error/40">
        <div className="max-w-prose text-body-sm text-text-secondary">
          <p>
            Vende todas las posiciones abiertas y da el experimento por terminado. No se puede
            deshacer y requiere el mercado abierto.
          </p>
          {live && (
            <p className="mt-1 text-caption font-medium text-warning">
              Hay un ciclo en marcha: se podrá cerrar cuando termine.
            </p>
          )}
        </div>
        <Button
          variant="destructive"
          disabled={live || close.isPending}
          onClick={() => setOpen(true)}
        >
          Cerrar experimento
        </Button>
      </Card>

      <ConfirmDialog
        open={open}
        title={`Cerrar ${profile}`}
        confirmLabel="Vender todo y cerrar"
        danger
        busy={close.isPending}
        error={close.error?.message}
        onConfirm={async () => {
          try {
            await close.mutateAsync();
            setOpen(false);
          } catch {
            // The message is on the dialog; it stays open so the reason is read
            // where the decision was taken.
          }
        }}
        onCancel={() => setOpen(false)}
      >
        <p>
          Vende <strong className="font-semibold">todas las posiciones abiertas</strong> a la
          apertura de la barra siguiente y da el experimento por terminado.
        </p>
        <p className="text-text-muted">
          El registro de la operación se ve en Ciclos, como el de un ciclo.
        </p>
      </ConfirmDialog>
    </section>
  );
}

const ADVANCED_SUMMARY = "Configuración avanzada";

/** Reasoning effort options. The empty one is "not sent": the provider decides. */
const REASONING_OPTIONS: [string, string][] = [
  ["", "Por defecto del proveedor"],
  ["low", "Bajo"],
  ["medium", "Medio"],
  ["high", "Alto"],
];

/** The subset of settings this form edits as free values, keyed as they are sent. */
type Draft = Record<string, string | number | boolean>;

/**
 * The form itself, mounted fresh whenever the saved settings change.
 *
 * It is keyed on `updated_at` by the caller so that saving —or another tab
 * saving— reseeds the fields from the server instead of leaving the form holding
 * values that no longer match what is stored. A form that silently disagrees
 * with the database is worse than one that reloads.
 *
 * @param props - Form props.
 * @param props.profileRef - Profile name, as it travels in the URL.
 * @param props.settings - The saved settings, already typed.
 * @param props.effective - The limits **in force**, overrides included, as the
 *     API resolved them. Not the same thing as the slider's preview: see the
 *     comment where the panel is rendered.
 * @param props.symbol - Currency symbol of the profile's market.
 * @return The rendered form.
 */
function SettingsForm({
  profileRef,
  settings,
  effective,
  symbol,
}: {
  profileRef: string;
  settings: AgentSettings;
  effective: DerivedLimits;
  symbol: string;
}) {
  const save = useUpdateSettings();

  const [risk, setRisk] = useState(settings.risk_profile);
  const [advanced, setAdvanced] = useState(settings.advanced_overrides);
  const [draft, setDraft] = useState<Draft>({});
  const [saved, setSaved] = useState<string[] | null>(null);

  /**
   * Current value of a field: what the user typed, or what is stored.
   *
   * @param field - Field name, exactly as the column is called.
   * @return The value to show in the control.
   */
  function value<K extends keyof AgentSettings>(field: K): string {
    const current = draft[field as string] ?? settings[field];
    return current === null || current === undefined ? "" : String(current);
  }

  // The horizon being typed feeds the preview, so the stop moves with it before
  // saving. A half-typed or empty horizon falls back to the stored one instead
  // of asking the API about day zero.
  const typedHorizon = Number(value("horizon_days"));
  const horizon = typedHorizon >= 1 ? typedHorizon : settings.horizon_days;
  const preview = useLimitsPreview(risk, horizon);

  /**
   * Records a typed value without sending it.
   *
   * @param field - Field name, exactly as the column is called.
   * @param next - The new value.
   */
  function set(field: string, next: string | number | boolean) {
    setSaved(null);
    setDraft((previous) => ({ ...previous, [field]: next }));
  }

  /**
   * The patch with only what actually changed.
   *
   * It is computed on every render and not only on submit, because the save bar
   * needs it too: «cambios sin guardar» has to mean the same thing the save
   * would send, or moving a slider and moving it back would leave the bar up
   * over a form with nothing to save.
   *
   * @return The changed fields, keyed as they are sent.
   */
  function pendingChanges(): Record<string, unknown> {
    const changes: Record<string, unknown> = {};

    // The slider is compared against what is stored, like everything else:
    // moving it and moving it back has to send nothing.
    if (risk !== settings.risk_profile) changes.risk_profile = risk;
    if (advanced !== settings.advanced_overrides) changes.advanced_overrides = advanced;

    for (const [field, typed] of Object.entries(draft)) {
      const stored = settings[field as keyof AgentSettings];
      const parsed = coerce(field, typed, stored);
      // `null` is a value here and not "unset": on the hard limits it means
      // "derive it from the slider again" (F6.5), so it is compared and sent
      // like any other.
      if (parsed !== stored) changes[field] = parsed;
    }

    return changes;
  }

  const dirty = Object.keys(pendingChanges()).length > 0;

  /**
   * Sends the patch.
   *
   * @param event - The submit event, whose default reload is prevented.
   */
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaved(null);

    const changes = pendingChanges();

    if (Object.keys(changes).length === 0) {
      setSaved([]);
      return;
    }

    const applied = await save.mutateAsync({
      ref: profileRef,
      changes: changes as SettingsUpdate,
    });
    setSaved(applied.applied);
  }

  return (
    <form className="flex flex-col gap-8" onSubmit={submit}>
      <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
        <Card padding="p-6" className="flex flex-col gap-6">
          <SectionTitle>Riesgo</SectionTitle>
          <Slider
            label="Perfil de riesgo"
            value={risk}
            low="Conservador"
            high="Agresivo"
            onChange={(e) => {
              setSaved(null);
              setRisk(Number(e.target.value));
            }}
          />
          <NumberField
            label="Horizonte (días)"
            field="horizon_days"
            value={value}
            set={set}
            step="1"
          />
        </Card>

        {/* Which of the two, and not always the same one. With manual limits
            off the slider rules, so its preview is the right answer. With them
            on, the typed numbers rule and the preview answers another question:
            showing it would put two sets of limits on screen at once. `effective`
            is already resolved by the cycle's own `resolve_limits`. */}
        {advanced ? (
          <DerivedLimitsPanel limits={effective} symbol={symbol} source="effective" />
        ) : (
          preview.data && (
            <DerivedLimitsPanel
              limits={preview.data}
              symbol={symbol}
              stale={preview.isFetching}
              source="sliders"
            />
          )
        )}
      </div>

      <Group title="Experimento">
        <NumberField label="Capital inicial" field="initial_budget" value={value} set={set} />
        <Select
          label="Intervalo de barras"
          value={value("bar_interval")}
          onChange={(next) => set("bar_interval", next)}
          options={[
            ["1h", "1 hora"],
            ["1d", "1 día"],
          ]}
        />
        <Input
          label="Horas de ciclo"
          value={value("cycle_times")}
          placeholder="10:20"
          onChange={(e) => set("cycle_times", e.target.value)}
        />
        <Check
          label="Leer noticias"
          field="news_enabled"
          settings={settings}
          draft={draft}
          set={set}
        />
      </Group>

      <Group title="Modelo">
        <Select
          label="Proveedor"
          value={value("llm_provider")}
          onChange={(next) => set("llm_provider", next)}
          options={[
            ["nvidia", "NVIDIA NIM (capa gratuita)"],
            ["openai", "OpenAI"],
          ]}
        />
        <Input
          label="Modelo"
          value={value("llm_model")}
          onChange={(e) => set("llm_model", e.target.value)}
        />
        <Input
          label="Clave de API"
          type="password"
          autoComplete="off"
          placeholder={
            settings.llm_api_key
              ? "Clave guardada"
              : settings.llm_provider === "nvidia"
                ? "Clave del entorno"
                : "Sin clave"
          }
          value={(draft.llm_api_key as string) ?? ""}
          onChange={(e) => set("llm_api_key", e.target.value)}
        />
      </Group>

      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-h3 text-foreground [&::-webkit-details-marker]:hidden">
          <ChevronRight
            className="size-5 shrink-0 text-text-muted transition-transform duration-150 group-open:rotate-90"
            aria-hidden
          />
          {ADVANCED_SUMMARY}
        </summary>
        <div className="mt-6 flex flex-col gap-8">
          <Group title="Modelo, en detalle">
            <NumberField label="Temperatura" field="llm_temperature" value={value} set={set} step="0.1" />
            <NumberField
              label="Techo de tokens de la respuesta"
              field="llm_max_tokens"
              value={value}
              set={set}
              step="1"
            />
            <Select
              label="Esfuerzo de razonamiento"
              value={value("llm_reasoning_effort")}
              onChange={(next) => set("llm_reasoning_effort", next)}
              options={REASONING_OPTIONS}
            />
            <NumberField label="Tiempo de espera (s)" field="llm_timeout_seconds" value={value} set={set} />
            <NumberField label="Reintentos" field="llm_max_retries" value={value} set={set} step="1" />
          </Group>

          <Group title="Noticias">
            <NumberField
              label="Titulares por empresa y de mercado"
              field="news_max_items"
              value={value}
              set={set}
              step="1"
            />
            <NumberField
              label="Antigüedad máxima (días)"
              field="news_max_age_days"
              value={value}
              set={set}
              step="1"
            />
          </Group>

          <Group title="Screener">
            <Select
              label="Modo del screener"
              value={value("screener_mode")}
              onChange={(next) => set("screener_mode", next)}
              options={[
                ["score", "Por puntuación"],
                ["random", "Al azar (grupo de control)"],
              ]}
            />
            <NumberField label="Candidatos al modelo" field="screener_top_n" value={value} set={set} step="1" />
            <NumberField
              label={`Liquidez mínima (${symbol}/día)`}
              field="screener_min_turnover"
              value={value}
              set={set}
            />
            <NumberField label="Precio mínimo" field="screener_min_price" value={value} set={set} />
            <NumberField
              label="Volatilidad máxima (%)"
              field="screener_max_volatility_pct"
              value={value}
              set={set}
            />
            <Input
              label="Fichero de universo"
              value={value("universe_file")}
              onChange={(e) => set("universe_file", e.target.value)}
            />
          </Group>

          <Group title="Ejecución">
            <NumberField
              label="Máx. entradas nuevas por ciclo"
              field="max_new_positions_per_cycle"
              value={value}
              set={set}
              step="1"
            />
            <NumberField
              label="Días de histórico"
              field="lookback_days"
              value={value}
              set={set}
              step="1"
            />
            <Input
              label="Zona horaria del ciclo"
              value={value("cycle_tz")}
              onChange={(e) => set("cycle_tz", e.target.value)}
            />
            <NumberField label="Deslizamiento (pb)" field="sim_slippage_bps" value={value} set={set} />
            <NumberField
              label="Recargo de comisión por orden"
              field="sim_commission"
              value={value}
              set={set}
            />
            <Check
              label="Simulación: analiza sin enviar órdenes"
              field="dry_run"
              settings={settings}
              draft={draft}
              set={set}
            />
            <Check
              label="Saltar el ciclo con el mercado cerrado"
              field="skip_when_market_closed"
              settings={settings}
              draft={draft}
              set={set}
            />
          </Group>

          <Group title="Límites duros">
            <Checkbox
              className="sm:col-span-2 lg:col-span-3"
              checked={advanced}
              onChange={(e) => {
                setSaved(null);
                setAdvanced(e.target.checked);
              }}
              label="Fijar los límites a mano"
            />
            {advanced && (
              <>
                <NumberField label="Riesgo por operación (%)" field="risk_per_trade_pct" value={value} set={set} />
                <NumberField label="Máx. por posición (%)" field="max_position_pct" value={value} set={set} />
                <NumberField
                  label="Mín. por posición (%)"
                  field="min_position_pct"
                  value={value}
                  set={set}
                />
                <NumberField label="Exposición total (%)" field="max_total_exposure_pct" value={value} set={set} />
                <NumberField label="Máx. posiciones abiertas" field="max_open_positions" value={value} set={set} step="1" />
                <NumberField label="Pérdida diaria máxima (%)" field="max_daily_loss_pct" value={value} set={set} />
                <NumberField label="Convicción mínima" field="min_conviction" value={value} set={set} step="1" />
                <NumberField
                  label="Múltiplo de ATR del stop"
                  field="stop_atr_multiple"
                  value={value}
                  set={set}
                />
                <NumberField label="Beneficio/riesgo mínimo" field="min_reward_risk" value={value} set={set} />
                <NumberField
                  label="Objetivo mínimo (σ del horizonte)"
                  field="min_target_sigma"
                  value={value}
                  set={set}
                />
                <NumberField label="Orden mínima" field="min_order_notional" value={value} set={set} />
              </>
            )}
          </Group>
        </div>
      </details>

      {save.error && <Alert>{save.error.message}</Alert>}

      {/* The save bar sticks to the bottom of the window while there is
          something to save. The button used to be only here, at the end of a
          screen that is a scroll and a half long with the advanced block open,
          so a change near the top was made with the save out of sight — and
          leaving the screen lost it without a word. Pinned, it is in view from
          the first keystroke and says the form differs from what is stored;
          saved, it drops back into the page. */}
      <div
        className={cn(
          "flex flex-wrap items-center gap-4 border-t border-border pt-6",
          dirty &&
            "sticky bottom-0 z-20 -mx-4 rounded-t-lg bg-card px-4 pb-4 pt-4 shadow-xl md:-mx-6 md:px-6",
        )}
      >
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? "Guardando…" : "Guardar cambios"}
        </Button>
        {dirty && !save.isPending && (
          <>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setRisk(settings.risk_profile);
                setAdvanced(settings.advanced_overrides);
                setDraft({});
              }}
            >
              Descartar
            </Button>
            <p role="status" className="text-body-sm text-text-secondary">
              Hay cambios sin guardar.
            </p>
          </>
        )}
        {!dirty && saved !== null && !save.error && (
          <p role="status" className="text-body-sm text-text-secondary">
            {saved.length === 0
              ? "No había nada que cambiar."
              : saved.length === 1
                ? "1 cambio guardado."
                : `${saved.length} cambios guardados.`}
          </p>
        )}
      </div>
    </form>
  );
}

/**
 * One family of parameters.
 *
 * @param props - Group props.
 * @param props.title - The family's heading.
 * @param props.children - The controls.
 * @return The rendered group.
 */
function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset>
      <legend className="sr-only">{title}</legend>
      <SectionTitle className="mb-3">{title}</SectionTitle>
      <Card padding="p-6">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{children}</div>
      </Card>
    </fieldset>
  );
}

/**
 * A numeric field, which is most of them.
 *
 * It is `NumberField` and not `Number`: the latter shadows the global inside this
 * module, and the sliders parse their value with it.
 *
 * @param props - Field props.
 * @param props.label - Label, in the interface language.
 * @param props.field - Column name, used as the key of the patch.
 * @param props.value - Reads the current value.
 * @param props.set - Records a typed value.
 * @param props.step - Step of the numeric input.
 * @return The rendered field.
 */
function NumberField({
  label,
  field,
  value,
  set,
  step = "any",
  hint,
}: {
  label: string;
  field: keyof AgentSettings;
  value: (field: keyof AgentSettings) => string;
  set: (field: string, next: string) => void;
  step?: string;
  hint?: string;
}) {
  return (
    <Input
      label={label}
      type="number"
      step={step}
      value={value(field)}
      onChange={(e) => set(field as string, e.target.value)}
      hint={hint}
    />
  );
}

/**
 * A boolean field.
 *
 * @param props - Field props.
 * @param props.label - Label, in the interface language.
 * @param props.field - Column name, used as the key of the patch.
 * @param props.settings - The saved settings, for the value not yet touched.
 * @param props.draft - What has been typed but not sent.
 * @param props.set - Records a toggled value.
 * @return The rendered checkbox inside its label.
 */
function Check({
  label,
  field,
  settings,
  draft,
  set,
  hint,
}: {
  label: string;
  field: keyof AgentSettings;
  settings: AgentSettings;
  draft: Draft;
  set: (field: string, next: boolean) => void;
  hint?: string;
}) {
  const checked = Boolean(draft[field as string] ?? settings[field]);
  return (
    <Checkbox
      // `self-end` lines the box up with the labelled fields beside it; with a
      // hint the line underneath has to fit, so the alignment is dropped.
      className={hint ? undefined : "self-end pb-2.5"}
      checked={checked}
      onChange={(e) => set(field as string, e.target.checked)}
      label={label}
      hint={hint}
    />
  );
}

/**
 * Turns what was typed into what the API expects for that column.
 *
 * The stored value is what says which type the column is, so the mapping does
 * not need a table of its own that could fall out of step with the schema.
 *
 * @param field - Column name.
 * @param typed - What the control produced.
 * @param stored - The saved value, used to infer the type.
 * @return The value to send: a number, a boolean, a string, or null for empty.
 */
function coerce(
  field: string,
  typed: string | number | boolean,
  stored: unknown,
): unknown {
  if (typeof typed === "boolean") return typed;
  if (typed === "") {
    // An empty text field is null, and on the hard limits that is the datum:
    // "derive it from the sliders again" (F6.5).
    return null;
  }
  if (typeof stored === "number") return Number(typed);
  // A limit sitting at NULL is numeric even though there is nothing stored to
  // tell by: those are exactly the eleven of advanced mode. The two nullable
  // text columns the form edits are the exceptions.
  if (stored === null && field !== "universe_file" && field !== "llm_reasoning_effort") {
    const asNumber = Number(typed);
    if (!isNaN(asNumber)) return asNumber;
  }
  return typed;
}
