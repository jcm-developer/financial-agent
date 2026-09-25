import { useState } from "react";
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
 * Creating an experiment, in one form (F5.3).
 *
 * **In one screen and not two**, which is the whole point of the task: name,
 * market, capital, universe size and the strategy and model decisions are taken
 * together because together they are what an experiment *is*. Splitting them
 * would mean creating something and then deciding what it was.
 *
 * ⚠️ **It is two API calls even so, and that is deliberate.** The creation goes
 * through `create_market_profile`, which applies the market's rules —universe
 * file, `profile_universe`, benchmark, liquidity floor (FE.11)— and stuffing the
 * 41 settings fields into that call would duplicate the validation. So the
 * sequence is create → patch settings → activate, and the order matters: **the
 * profile is born a `draft` and is only activated once the patch has landed.**
 * If the patch fails, what is left is a visible, deletable draft rather than an
 * experiment running with parameters nobody chose.
 *
 * The market's own numbers —currency, hours, universe size, liquidity floor—
 * come from `/api/markets` and are not wired here: that registry is exactly what
 * D8 pulled them out of.
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
      <BlockTitle as="h2" className="text-h4">
        Nuevo experimento
      </BlockTitle>

      <form className="mt-4 flex flex-col gap-6" onSubmit={submit}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Input
            label="Nombre"
            value={name}
            required
            maxLength={80}
            autoFocus
            placeholder="europa-01"
            onChange={(e) => setName(e.target.value)}
            hint="Va en la URL del experimento, así que conviene que se lea."
          />
          <Input
            label="Descripción"
            value={description}
            maxLength={200}
            placeholder="Qué se quiere medir con este experimento"
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <fieldset className="flex flex-col gap-4 border-t border-border pt-4">
          <legend className="sr-only">Mercado y capital</legend>

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
                label="Capital inicial"
                type="number"
                min={1}
                step="any"
                required
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                hint={chosen && `En ${chosen.currency}: el proyecto no convierte divisa.`}
              />
              <Input
                label="Símbolos a seguir en vivo"
                type="number"
                min={0}
                max={500}
                value={watch}
                placeholder={chosen ? String(chosen.universe_size) : "0"}
                onChange={(e) => setWatch(e.target.value)}
                hint="Vacío = todo el universo. Son peticiones por minuto a Yahoo."
              />
            </div>
          )}

          {chosen && <MarketNote market={chosen} />}
        </fieldset>

        <fieldset className="flex flex-col gap-4 border-t border-border pt-4">
          <legend className="sr-only">Estrategia</legend>
          <div className="grid gap-6 sm:grid-cols-2">
            <Slider
              label="Perfil de riesgo"
              value={risk}
              low="1 · muy conservador"
              high="10 · muy agresivo"
              onChange={(e) => setRisk(Number(e.target.value))}
              hint="Decide tamaño y número de posiciones, exposición, convicción mínima, stop, objetivo y kill switch."
            />
            <Input
              label="Horizonte (días)"
              type="number"
              min={1}
              max={3650}
              required
              value={horizon}
              onChange={(e) => setHorizon(e.target.value)}
              hint="Plazo al que se juzga cada idea, en días naturales. Fija el tamaño del objetivo y la distancia del stop."
            />
          </div>
          <p className="text-caption text-text-muted">
            Los once límites duros salen del perfil de riesgo y del horizonte: el riesgo decide
            el tamaño, la concentración y cuánto margen tiene cada posición, y el horizonte cuánto
            vale ese margen. Se pueden fijar a mano después, en Ajustes.
          </p>
        </fieldset>

        <fieldset className="flex flex-col gap-4 border-t border-border pt-4">
          <legend className="sr-only">Modelo</legend>
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
              onChange={(e) => setApiKey(e.target.value)}
              hint={
                provider === "nvidia"
                  ? "Vacío = se usa NVIDIA_API_KEY del entorno."
                  : "Obligatoria: NVIDIA_API_KEY no vale para OpenAI."
              }
            />
          </div>
        </fieldset>

        {failure && (
          <Alert>
            {failure}
            {orphan && (
              <>
                <br />
                <span className="text-text-muted">
                  Ha quedado el borrador «{orphan}» en la lista. Se puede completar desde
                  sus Ajustes o borrarlo; no está corriendo.
                </span>
              </>
            )}
          </Alert>
        )}

        <div className="flex flex-wrap gap-2 border-t border-border pt-4">
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? "Creando…" : "Crear y activar"}
          </Button>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            Cancelar
          </Button>
        </div>
      </form>
    </Card>
  );
}

/**
 * What choosing this market decides, said before it is chosen.
 *
 * It is here because the market **cannot be changed afterwards** (decision of
 * 2026-08-08): hours, calendar, currency, benchmark and liquidity floor all come
 * from it, so changing it mid-experiment would reinterpret the history already
 * recorded. A decision that cannot be undone has to show its consequences while
 * it is still being taken.
 *
 * @param props - Note props.
 * @param props.market - The chosen market, straight from `/api/markets`.
 * @return The rendered note.
 */
function MarketNote({ market }: { market: MarketInfo }) {
  return (
    <p className="text-caption leading-relaxed text-text-muted">
      Sesión {market.session_open}–{market.session_close} ({market.timezone}), ventana
      operativa {market.operating_open}–{market.operating_close}. Divisa {market.currency}.
      Benchmark {market.benchmark}. Universo {market.universe_file} con{" "}
      {integer(market.universe_size)} símbolos y suelo de liquidez{" "}
      {money(market.min_turnover, market.currency_symbol)} al día.
      <br />
      <strong className="font-semibold text-text-secondary">
        El mercado no se puede cambiar después:
      </strong>{" "}
      de él salen el horario, el calendario, la divisa y el benchmark, así que cambiarlo a
      mitad de experimento reinterpretaría el histórico ya grabado.
    </p>
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
