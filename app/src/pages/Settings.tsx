import { useState } from "react";

import {
  useLimitsPreview,
  useProfileSettings,
  useUpdateSettings,
} from "@/api/hooks";
import type { AgentSettings, DerivedLimits, SettingsUpdate } from "@/api/types";
import { DerivedLimitsPanel } from "@/components/DerivedLimitsPanel";
import { Checkbox } from "@/components/Checkbox";
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
import { useActiveProfile } from "@/profile/useActiveProfile";

/**
 * The experiment's parameters (F6.8).
 *
 * **The two sliders are the screen, and the 45 fields are the small print.**
 * That is the shape F6.5 asks for: risk profile and diversification decide the
 * ten hard limits, and the panel beside them says what those limits are while
 * the slider moves. Everything else is grouped underneath in the four families
 * the parameters actually have —model, strategy, execution and hard limits— and
 * the last of those only opens in advanced mode.
 *
 * **Nothing is derived in the browser.** The limits come from the API, which
 * runs the same `resolve_limits` as the cycle. A copy of that arithmetic in
 * TypeScript would disagree the day an anchor is tweaked and the screen would
 * promise limits the agent does not apply. ⚠️ Which endpoint, though, depends on
 * advanced mode, and getting that wrong was a real bug until 2026-08-11: see the
 * comment where `DerivedLimitsPanel` is rendered.
 *
 * ⚠️ **Only what changed is sent.** `update_settings` ignores a field arriving
 * with the value it already had, and `agent_settings_history` records real
 * changes only (F6.2). Sending the 45 fields on every save would not corrupt
 * anything, but it would fill the history with rows saying "5 → 5" and the
 * history is what makes an experiment readable afterwards.
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
    </>
  );
}

/**
 * Why the commission field says "surcharge".
 *
 * The bank's tariff is not a parameter of the experiment: it depends on the
 * exchange of each symbol (`src/fees.py`) and applies on its own. This field is
 * what gets added on top, and it is worth saying so, because a zero here used to
 * mean "no commission" and now means "the standard one and nothing more".
 *
 * The amounts are deliberately left out: repeating them here would be a second
 * copy that drifts the day the bank changes its rates, and the screen would
 * promise a friction the simulator does not apply.
 */
const SURCHARGE_HINT =
  "Se suma a la tarifa del banco, que ya se aplica sola y depende de la bolsa de cada símbolo. 0 = solo la tarifa.";

/**
 * The four columns that are stored, editable, and **read by nobody**.
 *
 * Audited on 2026-08-11 after a screen-by-screen read of this form: `grep` finds
 * none of them in `src/`. They are not removed from the screen —they are real
 * columns, and three of the four are things the project intends to do— but a field
 * that looks like it configures the experiment and does not is worse than a
 * missing one: it invites a decision that has no effect and cannot be seen not
 * working.
 *
 * It is the same treatment `sector_cap` already gets in `DerivedLimitsPanel`, and
 * for the same reason: the absence of these limits is invisible. Nothing fails.
 *
 * `horizon_days` was the fifth until F9.17, and it was the one that cost real
 * money: nobody had told the model the plan, so it aimed at one sigma of the two
 * weeks it invented. That is the precedent for marking the rest.
 */
const NOT_APPLIED = {
  benchmark:
    "No se usa todavía: la Analítica no compara contra el índice. El que sale en Mercado es el del mercado del perfil, no este.",
  cash_reserve_pct:
    "No se aplica todavía: el ciclo gasta hasta la exposición máxima. Para no comprometer la caja de golpe, usa «Máx. entradas nuevas por ciclo».",
  excluded_sectors_json:
    "No se aplica: no hay dato de sector por símbolo en tiempo de ejecución (F6.5, FE.12), el mismo motivo por el que el tope por sector solo se calcula.",
  allow_shorts:
    "No se aplica, y es del diseño: el analista solo propone compra o mantener, y el Risk Manager rechaza cualquier otra acción.",
} as const;

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
 *     API resolved them. Not the same thing as the sliders' preview: see the
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
  const [diversification, setDiversification] = useState(settings.diversification);
  const [advanced, setAdvanced] = useState(settings.advanced_overrides);
  const [draft, setDraft] = useState<Draft>({});
  const [saved, setSaved] = useState<string[] | null>(null);

  const preview = useLimitsPreview(risk, diversification);

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
   * Builds the patch with only what actually changed and sends it.
   *
   * @param event - The submit event, whose default reload is prevented.
   */
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaved(null);

    const changes: Record<string, unknown> = {};

    // The sliders are compared against what is stored, like everything else:
    // moving one and moving it back has to send nothing.
    if (risk !== settings.risk_profile) changes.risk_profile = risk;
    if (diversification !== settings.diversification) {
      changes.diversification = diversification;
    }
    if (advanced !== settings.advanced_overrides) changes.advanced_overrides = advanced;

    for (const [field, typed] of Object.entries(draft)) {
      const stored = settings[field as keyof AgentSettings];
      const parsed = coerce(field, typed, stored);
      // `null` is a value here and not "unset": on the hard limits it means
      // "derive it from the sliders again" (F6.5), so it is compared and sent
      // like any other.
      if (parsed !== stored) changes[field] = parsed;
    }

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
      <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr] lg:items-start">
        <Card padding="p-6" className="flex flex-col gap-6">
          <SectionTitle>Los dos deslizadores</SectionTitle>
          <Slider
            label="Perfil de riesgo"
            value={risk}
            low="1 · muy conservador"
            high="10 · muy agresivo"
            onChange={(e) => {
              setSaved(null);
              setRisk(Number(e.target.value));
            }}
          />
          <Slider
            label="Diversificación"
            value={diversification}
            low="1 · concentrado"
            high="10 · repartido"
            onChange={(e) => {
              setSaved(null);
              setDiversification(Number(e.target.value));
            }}
          />
          <Checkbox
            className="border-t border-border pt-6"
            checked={advanced}
            onChange={(e) => {
              setSaved(null);
              setAdvanced(e.target.checked);
            }}
            label="Modo avanzado: fijar los diez límites a mano"
            /* This is the master switch of F6.5, and its wording matters: with
               it off the sliders win *even if the columns still hold numbers
               from a previous session*. Without saying so, turning it off looks
               like it did nothing. */
            hint="Con esto apagado mandan los deslizadores, aunque las columnas conserven números de antes. Encendido, mandan los números de abajo."
          />
        </Card>

        {/* ⚠️ **Cuál de los dos, y no siempre el mismo.** Con el modo avanzado
            apagado mandan los deslizadores, así que la vista previa por posición
            del deslizador es la respuesta correcta y es lo que F6.8 pedía.
            Encendido, mandan los números escritos a mano y la vista previa
            contesta a otra pregunta: enseñarla ahí era poner en pantalla dos
            juegos de límites a la vez, con el rótulo «Con estos ajustes» sobre el
            que no se aplica. `effective` ya viene resuelto por la misma
            `resolve_limits` del ciclo. */}
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
              ? "Hay una clave guardada"
              : settings.llm_provider === "nvidia"
                ? "Se usa NVIDIA_API_KEY del entorno"
                : "Sin clave"
          }
          value={(draft.llm_api_key as string) ?? ""}
          onChange={(e) => set("llm_api_key", e.target.value)}
          hint="Vacío no la borra: deja la que hay."
        />
        <NumberField label="Temperatura" field="llm_temperature" value={value} set={set} step="0.1" />
        <NumberField label="Timeout (s)" field="llm_timeout_seconds" value={value} set={set} />
        <NumberField label="Reintentos" field="llm_max_retries" value={value} set={set} />
        <Input
          label="Instrucciones al analista"
          value={value("analyst_persona")}
          placeholder="p. ej. value investor, momentum…"
          onChange={(e) => set("analyst_persona", e.target.value)}
          fieldClass="sm:col-span-2"
        />
      </Group>

      <Group title="Estrategia">
        <NumberField label="Horizonte objetivo (días)" field="horizon_days" value={value} set={set} />
        <NumberField
          label="Máx. entradas nuevas por ciclo"
          field="max_new_positions_per_cycle"
          value={value}
          set={set}
        />
        <Select
          label="Modo del screener"
          value={value("screener_mode")}
          onChange={(next) => set("screener_mode", next)}
          options={[
            ["score", "score — puntuación por tendencia y volumen"],
            ["random", "random — grupo de control"],
          ]}
        />
        <NumberField label="Candidatos al modelo" field="screener_top_n" value={value} set={set} />
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
        <Input
          label="Benchmark"
          value={value("benchmark")}
          onChange={(e) => set("benchmark", e.target.value)}
          hint={NOT_APPLIED.benchmark}
        />
        <NumberField
          label="Reserva de caja (%)"
          field="cash_reserve_pct"
          value={value}
          set={set}
          hint={NOT_APPLIED.cash_reserve_pct}
        />
        <Input
          label="Sectores excluidos (JSON)"
          value={value("excluded_sectors_json")}
          onChange={(e) => set("excluded_sectors_json", e.target.value)}
          hint={NOT_APPLIED.excluded_sectors_json}
        />
        <Check
          label="Permitir cortos"
          field="allow_shorts"
          settings={settings}
          draft={draft}
          set={set}
          hint={NOT_APPLIED.allow_shorts}
        />
      </Group>

      <Group title="Ejecución">
        <NumberField label="Capital inicial" field="initial_budget" value={value} set={set} />
        <Select
          label="Intervalo de barras"
          value={value("bar_interval")}
          onChange={(next) => set("bar_interval", next)}
          options={[
            ["1d", "1d — un ciclo tras el cierre"],
            ["1h", "1h — varios ciclos por sesión"],
          ]}
        />
        <NumberField label="Barras de histórico" field="lookback_days" value={value} set={set} />
        <Input
          label="Horas de ciclo"
          value={value("cycle_times")}
          placeholder="17:40 o 11:20,14:20,17:40"
          onChange={(e) => set("cycle_times", e.target.value)}
          hint="HH:MM separadas por comas. El planificador lo recoge en menos de un minuto, sin reiniciar nada."
        />
        <Input
          label="Zona horaria del ciclo"
          value={value("cycle_tz")}
          onChange={(e) => set("cycle_tz", e.target.value)}
          hint="Nombre IANA, p. ej. Europe/Madrid."
        />
        <NumberField label="Deslizamiento (pb)" field="sim_slippage_bps" value={value} set={set} />
        <NumberField
          label="Recargo de comisión por orden"
          field="sim_commission"
          value={value}
          set={set}
          hint={SURCHARGE_HINT}
        />
        <Check
          label="Dry run: analiza y registra pero no ordena"
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

      {advanced && (
        <Group title="Límites duros (modo avanzado)">
          <NumberField label="Riesgo por operación (%)" field="risk_per_trade_pct" value={value} set={set} />
          <NumberField label="Máx. por posición (%)" field="max_position_pct" value={value} set={set} />
          <NumberField label="Exposición total (%)" field="max_total_exposure_pct" value={value} set={set} />
          <NumberField label="Máx. posiciones abiertas" field="max_open_positions" value={value} set={set} />
          <NumberField label="Pérdida diaria máxima (%)" field="max_daily_loss_pct" value={value} set={set} />
          <NumberField label="Convicción mínima" field="min_conviction" value={value} set={set} />
          <NumberField label="Múltiplo de ATR del stop" field="stop_atr_multiple" value={value} set={set} />
          <NumberField label="Reward/risk mínimo" field="min_reward_risk" value={value} set={set} />
          <NumberField
            label="Objetivo mínimo (σ del horizonte)"
            field="min_target_sigma"
            value={value}
            set={set}
          />
          <NumberField label="Notional mínimo" field="min_order_notional" value={value} set={set} />
          <p className="text-caption leading-relaxed text-text-muted sm:col-span-2 lg:col-span-3">
            Un campo vacío vuelve a NULL, que significa «derívalo de los deslizadores». No es
            lo mismo que un cero: el cero es un límite que se ha elegido.
          </p>
        </Group>
      )}

      {save.error && <Alert>{save.error.message}</Alert>}

      {saved !== null && !save.error && (
        <p role="status" className="text-body-sm text-text-secondary">
          {saved.length === 0
            ? "No había nada que cambiar."
            : `Guardado: ${saved.join(", ")}.`}
        </p>
      )}

      <div className="flex flex-wrap gap-2 border-t border-border pt-4">
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? "Guardando…" : "Guardar cambios"}
        </Button>
        <span className="self-center text-caption text-text-muted">
          Los cambios se aplican al siguiente ciclo: el que esté corriendo leyó sus
          parámetros al arrancar y no los recarga (R6).
        </span>
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
      // `self-end` sube la caja a la altura de los campos de al lado, y con
      // `hint` eso ya no vale: la linea de debajo tiene que caber. Sin nota se
      // mantiene el ajuste de antes, porque en la fila de «Dry run» la casilla
      // sigue alineandose contra un campo con etiqueta.
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
  // tell by: those are exactly the ten of advanced mode.
  if (stored === null && field !== "universe_file" && field !== "analyst_persona") {
    const asNumber = Number(typed);
    if (!isNaN(asNumber)) return asNumber;
  }
  return typed;
}
