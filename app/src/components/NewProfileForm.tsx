import { useId, useState } from "react";
import { useNavigate } from "react-router";

import { ApiError } from "@/api/client";
import { useCreateProfile, useMarkets, useUpdateProfile, useUpdateSettings } from "@/api/hooks";
import type { MarketInfo, ProfileCreate, SettingsUpdate } from "@/api/types";
import {
  Alert,
  BlockTitle,
  Button,
  Card,
  Input,
  Loading,
  Slider,
} from "@/components/pieces";
import { Select } from "@/components/Select";
import { integer, money } from "@/lib/format";

/**
 * Creating an experiment, in one form.
 *
 * Name, market, capital, risk and model are decided together because together
 * they are what an experiment *is*.
 *
 * ⚠️ **It is three API calls even so, and that is deliberate.** Creation goes
 * through `create_market_profile`, which applies the market's rules (universe,
 * benchmark, liquidity floor), and stuffing the settings into that call would
 * duplicate its validation. So the sequence is create → patch settings →
 * activate: **the profile is born a `draft` and is only activated once the patch
 * has landed.** If the patch fails, what is left is a visible, deletable draft
 * rather than an experiment running with parameters nobody chose.
 */
interface Props {
  /** Called when the user gives up, so the caller can fold the form away. */
  onCancel: () => void;
}

/** Providers `src/llm.py` actually implements, plus the one it refuses on purpose. */
const PROVIDERS = [
  ["nvidia", "NVIDIA NIM (capa gratuita)"],
  ["openai", "OpenAI"],
] as const;

/**
 * What each provider's model field should say when nobody has typed one.
 *
 * NVIDIA's is not from the `meta/llama` range because that range has no served
 * version left: `llama-3.3-70b` hung on 2026-08-12 (F9.22) and `llama-3.1-70b`
 * was retired on 2026-08-26 (410 Gone). Nor is it the `minimax-m3` F9.23 picked:
 * a day later it answered 429 to every call while the same key was served by
 * other models, so what it lacks is a turn, not a quota. Of what was measured
 * with the real prompt, `nemotron-3-super` is the one that returned JSON three
 * times out of three (F9.23.1). It is the same default as `schema.sql`, and the
 * two are meant to move together.
 */
const DEFAULT_MODEL: Record<string, string> = {
  nvidia: "nvidia/nemotron-3-super-120b-a12b",
  openai: "gpt-4o-mini",
};

/**
 * The form that creates an experiment.
 *
 * @param props - Form props.
 * @param props.onCancel - Called when the user gives up.
 * @return The rendered form.
 */
export function NewProfileForm({ onCancel }: Props) {
  const markets = useMarkets();
  const navigate = useNavigate();

  const create = useCreateProfile();
  const patchSettings = useUpdateSettings();
  const patchProfile = useUpdateProfile();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [market, setMarket] = useState("eu");
  const [budget, setBudget] = useState("10000");
  const [watch, setWatch] = useState("");
  const [risk, setRisk] = useState(5);
  const [horizon, setHorizon] = useState("180");
  const [provider, setProvider] = useState("nvidia");
  const [model, setModel] = useState(DEFAULT_MODEL.nvidia ?? "");
  const [apiKey, setApiKey] = useState("");

  /** What went wrong, and at which of the three steps. */
  const [failure, setFailure] = useState<string | null>(null);
  /** The draft that survived a half-failed creation, so the message can name it. */
  const [orphan, setOrphan] = useState<string | null>(null);

  const chosen = markets.data?.find((m) => m.code === market);
  const busy = create.isPending || patchSettings.isPending || patchProfile.isPending;

  /**
   * Runs the three calls in order, stopping at the first failure.
   *
   * @param event - The submit event, whose default reload is prevented.
   */
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);
    setOrphan(null);

    const watched = watch.trim() === "" ? 0 : Number(watch);

    try {
      await create.mutateAsync({
        name: name.trim(),
        description: description.trim(),
        // The cast is to the generated type and not to a hand-written union:
        // if a third market ever joins the registry, this is one of the places
        // that has to stop compiling.
        market: market as NonNullable<ProfileCreate["market"]>,
        budget: Number(budget),
        watch: watched,
      });
    } catch (error) {
      setFailure(message(error, "No se pudo crear el experimento."));
      return;
    }

    // From here on the profile exists. Every failure below leaves a draft, and
    // the message has to say so: a form that reports "error" after having
    // created something invites pressing it again, and the second attempt fails
    // with "ya existe un perfil llamado…" for a reason nobody can guess.
    try {
      await patchSettings.mutateAsync({
        ref: name.trim(),
        changes: {
          risk_profile: risk,
          horizon_days: Number(horizon),
          llm_provider: provider as NonNullable<SettingsUpdate["llm_provider"]>,
          llm_model: model.trim(),
          // An empty key is not "no key": with NIM it means NVIDIA_API_KEY from
          // the environment (F6.7). Sending "" would write an empty string where
          // NULL is what carries that meaning.
          ...(apiKey.trim() ? { llm_api_key: apiKey.trim() } : {}),
        },
      });
    } catch (error) {
      setOrphan(name.trim());
      setFailure(message(error, "El experimento se creó pero sus parámetros no."));
      return;
    }

    try {
      await patchProfile.mutateAsync({ ref: name.trim(), patch: { status: "active" } });
    } catch (error) {
      setOrphan(name.trim());
      setFailure(message(error, "El experimento se creó con sus parámetros pero no se activó."));
      return;
    }

    navigate(`/p/${encodeURIComponent(name.trim())}/summary`);
  }

  return (
    <Card as="section" padding="p-6">
      <BlockTitle as="h2" className="text-h3">
        Nuevo experimento
      </BlockTitle>

      <form className="mt-6 flex flex-col gap-6" onSubmit={submit}>
        <FormGroup title="Identidad">
          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Nombre"
              value={name}
              required
              maxLength={80}
              autoFocus
              placeholder="europa-01"
              onChange={(e) => setName(e.target.value)}
            />
            <Input
              label="Descripción"
              value={description}
              maxLength={200}
              placeholder="Opcional"
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
        </FormGroup>

        <FormGroup title="Mercado y capital">
          {markets.isPending && <Loading text="Cargando mercados…" />}
          {markets.error && <Alert>{markets.error.message}</Alert>}

          {markets.data && (
            <div className="grid gap-4 sm:grid-cols-3">
              <Select
                label="Mercado"
                value={market}
                onChange={(next) => setMarket(next)}
                options={markets.data.map((m) => [m.code, m.label] as const)}
              />
              <Input
                label={chosen ? `Capital inicial (${chosen.currency})` : "Capital inicial"}
                type="number"
                min={1}
                step="any"
                required
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
              />
              <Input
                label="Símbolos en vivo"
                type="number"
                min={0}
                max={500}
                value={watch}
                placeholder={chosen ? String(chosen.universe_size) : "0"}
                onChange={(e) => setWatch(e.target.value)}
                hint="Vacío: todo el universo."
              />
            </div>
          )}

          {chosen && <MarketFacts market={chosen} />}
        </FormGroup>

        <FormGroup title="Estrategia">
          <div className="grid gap-6 sm:grid-cols-2">
            <Slider
              label="Perfil de riesgo"
              value={risk}
              low="Conservador"
              high="Agresivo"
              onChange={(e) => setRisk(Number(e.target.value))}
            />
            <Input
              label="Horizonte (días)"
              type="number"
              min={1}
              max={3650}
              required
              value={horizon}
              onChange={(e) => setHorizon(e.target.value)}
            />
          </div>
        </FormGroup>

        <FormGroup title="Modelo">
          <div className="grid gap-4 sm:grid-cols-3">
            <Select
              label="Proveedor"
              value={provider}
              onChange={(next) => {
                setProvider(next);
                setModel(DEFAULT_MODEL[next] ?? "");
              }}
              options={PROVIDERS}
            />
            <Input
              label="Modelo"
              value={model}
              required
              onChange={(e) => setModel(e.target.value)}
            />
            <Input
              label="Clave de API"
              type="password"
              value={apiKey}
              autoComplete="off"
              placeholder={provider === "nvidia" ? "Clave del entorno" : "Obligatoria"}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </div>
        </FormGroup>

        {failure && (
          <Alert>
            {failure}
            {orphan && (
              <>
                <br />
                <span className="text-text-muted">
                  Queda el borrador «{orphan}» en la lista, sin activar.
                </span>
              </>
            )}
          </Alert>
        )}

        <div className="flex flex-wrap justify-end gap-3 border-t border-border pt-6">
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            Cancelar
          </Button>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? "Creando…" : "Crear y activar"}
          </Button>
        </div>
      </form>
    </Card>
  );
}

/**
 * One block of the form: its heading on the left, its fields on the right.
 *
 * A `role="group"` labelled by the heading rather than a `<fieldset>` with a
 * `<legend>`: legends do not take part in a grid, and the two-column layout is
 * the point of the block.
 *
 * @param props - Group props.
 * @param props.title - The block's heading.
 * @param props.children - The fields.
 * @return The rendered block.
 */
function FormGroup({ title, children }: { title: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <div
      role="group"
      aria-labelledby={id}
      className="grid gap-4 border-t border-border pt-6 md:grid-cols-[10rem_1fr] md:gap-8"
    >
      <h3 id={id} className="text-h4 text-foreground">
        {title}
      </h3>
      <div className="flex min-w-0 flex-col gap-4">{children}</div>
    </div>
  );
}

/**
 * The chosen market's fixed facts, shown while it is still being chosen.
 *
 * The market cannot be changed afterwards (decision of 2026-08-08): hours,
 * calendar, currency and benchmark all come from it, so the one sentence kept
 * here is that warning.
 *
 * @param props - Facts props.
 * @param props.market - The chosen market, straight from `/api/markets`.
 * @return The rendered facts.
 */
function MarketFacts({ market }: { market: MarketInfo }) {
  const facts: [string, string][] = [
    ["Sesión", `${market.session_open}–${market.session_close}`],
    ["Divisa", market.currency],
    ["Benchmark", market.benchmark],
    ["Universo", `${integer(market.universe_size)} símbolos`],
    ["Liquidez mínima", `${money(market.min_turnover, market.currency_symbol)}/día`],
  ];
  return (
    <div className="flex flex-col gap-2">
      <dl className="flex flex-wrap gap-x-6 gap-y-1 text-body-sm">
        {facts.map(([label, value]) => (
          <div key={label} className="flex gap-1.5">
            <dt className="text-text-muted">{label}</dt>
            <dd className="tabular text-text-secondary">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-caption text-warning">El mercado no se puede cambiar después.</p>
    </div>
  );
}

/**
 * The message to show for a failed step.
 *
 * @param error - Whatever the mutation threw.
 * @param fallback - What to say when the error carries no readable message.
 * @return A sentence for the screen, with the step's context in front.
 */
function message(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return `${fallback} ${error.message}`;
  if (error instanceof Error && error.message) return `${fallback} ${error.message}`;
  return fallback;
}
