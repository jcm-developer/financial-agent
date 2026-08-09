import { useState } from "react";
import { useNavigate } from "react-router";

import {
  useDeleteProfile,
  useDuplicateProfile,
  useUpdateProfile,
  useUpdateSettings,
} from "@/api/hooks";
import type { ProfileSummary } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button, Input } from "@/components/pieces";
import { statusMeaning } from "@/components/ProfileStatus";

/**
 * What can be done to an experiment (F5.4).
 *
 * **Which actions each state offers, and why not all of them always:** a button
 * that is always there and sometimes fails teaches people to ignore the row.
 * Activating a running experiment does nothing, pausing a draft that never ran
 * does nothing, and archiving something that is running is two decisions at once.
 * So the row shows the transitions that mean something from where the profile is.
 *
 * **Duplicating is the central gesture and is always available**, including on
 * archived profiles: cloning and changing one parameter is what makes this an
 * experiment and not a bot, and the most interesting thing to clone is often the
 * one that already finished.
 */
interface Props {
  profile: ProfileSummary;
}

/** Which dialog is open, if any. */
type Pending = "pause" | "archive" | "duplicate" | "delete" | null;

/**
 * The action row of one experiment.
 *
 * @param props - Action props.
 * @param props.profile - The profile the actions apply to.
 * @return The rendered buttons and whichever dialog is open.
 */
export function ProfileActions({ profile }: Props) {
  const navigate = useNavigate();
  const patch = useUpdateProfile();
  const duplicate = useDuplicateProfile();
  const patchSettings = useUpdateSettings();
  const remove = useDeleteProfile();

  const [pending, setPending] = useState<Pending>(null);
  const [copyName, setCopyName] = useState("");
  const [asControl, setAsControl] = useState(false);
  const [typedName, setTypedName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const running = profile.status === "active";

  /**
   * Closes whichever dialog is open and forgets what was typed into it.
   *
   * The fields are cleared here and not on opening so a cancelled deletion does
   * not leave the name already retyped for the next attempt, which would undo
   * the whole point of retyping it.
   */
  function close() {
    setPending(null);
    setCopyName("");
    setAsControl(false);
    setTypedName("");
    setError(null);
  }

  /**
   * Moves the profile to another state.
   *
   * @param status - The state to move to.
   */
  async function setStatus(status: ProfileSummary["status"]) {
    setError(null);
    try {
      await patch.mutateAsync({ ref: profile.name, patch: { status } });
      close();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No se pudo cambiar el estado.");
    }
  }

  return (
    <>
      {profile.status !== "active" && (
        <Button onClick={() => setStatus("active")} disabled={patch.isPending}>
          Activar
        </Button>
      )}

      {running && <Button onClick={() => setPending("pause")}>Pausar</Button>}

      {(profile.status === "paused" || profile.status === "draft") && (
        <Button onClick={() => setPending("archive")}>Archivar</Button>
      )}

      <Button
        onClick={() => {
          setCopyName(`${profile.name}-copia`);
          setPending("duplicate");
        }}
      >
        Duplicar
      </Button>

      <Button variant="danger" onClick={() => setPending("delete")}>
        Borrar
      </Button>

      <ConfirmDialog
        open={pending === "pause"}
        title={`Pausar ${profile.name}`}
        confirmLabel="Pausar"
        busy={patch.isPending}
        error={error}
        onConfirm={() => setStatus("paused")}
        onCancel={close}
      >
        <p>
          Deja de ejecutar ciclos. El histórico, las posiciones abiertas y el capital se
          conservan enteros, y se puede volver a activar cuando quieras.
        </p>
        <p>
          {/* This is the part nobody expects: pausing does not close anything.
              A paused experiment holding four positions is exposed to the market
              with nobody watching the stops, because stop and target are only
              checked when a cycle runs. */}
          <strong className="font-semibold text-warning">
            No cierra las posiciones abiertas.
          </strong>{" "}
          {profile.metrics.open_positions
            ? `Quedan ${profile.metrics.open_positions} abiertas, y sus stops y objetivos dejarán de comprobarse: eso solo pasa dentro de un ciclo.`
            : "Ahora mismo no hay ninguna abierta."}
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={pending === "archive"}
        title={`Archivar ${profile.name}`}
        confirmLabel="Archivar"
        busy={patch.isPending}
        error={error}
        onConfirm={() => setStatus("archived")}
        onCancel={close}
      >
        <p>{statusMeaning("archived")}</p>
        <p>
          Desaparece de este listado salvo que marques «ver archivados». No se borra nada:
          sus ciclos, decisiones y operaciones siguen ahí y sirven para comparar.
        </p>
      </ConfirmDialog>

      <ConfirmDialog
        open={pending === "duplicate"}
        title={`Duplicar ${profile.name}`}
        confirmLabel="Duplicar"
        busy={duplicate.isPending || patchSettings.isPending}
        confirmDisabled={copyName.trim().length === 0}
        error={error}
        onConfirm={async () => {
          setError(null);
          const created = copyName.trim();
          try {
            await duplicate.mutateAsync({ ref: profile.name, body: { name: created } });
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : "No se pudo duplicar.");
            return;
          }
          if (asControl) {
            try {
              await patchSettings.mutateAsync({
                ref: created,
                changes: { screener_mode: "random" },
              });
            } catch (cause) {
              // The copy exists; only the one change that makes it a control
              // failed. Saying so is the difference between fixing one field and
              // starting over — and worse, between knowing and not knowing that
              // the "control" on screen is not one.
              setError(
                `La copia «${created}» se creó, pero no se pudo ponerla en modo control: ${
                  cause instanceof Error ? cause.message : "error desconocido"
                }. Cámbialo en sus Ajustes antes de usarla para comparar.`,
              );
              return;
            }
          }
          close();
          navigate(`/p/${encodeURIComponent(created)}/settings`);
        }}
        onCancel={close}
      >
        <p>
          Copia los parámetros y el universo, <strong className="font-semibold">no el
          histórico</strong>: heredar los ciclos del original es justo lo que haría que los
          dos dejaran de ser comparables.
        </p>
        <p>
          Nace como borrador. Al terminar se abre en sus Ajustes, que es donde se cambia el
          parámetro que se quiere medir.
        </p>
        <Input
          label="Nombre de la copia"
          value={copyName}
          maxLength={80}
          onChange={(e) => setCopyName(e.target.value)}
        />

        {/* F5.7. It goes here and not in a button of its own because a control
            profile IS a duplicate with one parameter changed — the same gesture,
            with the parameter already decided. */}
        <label className="flex items-start gap-2">
          <input
            type="checkbox"
            checked={asControl}
            onChange={(e) => {
              setAsControl(e.target.checked);
              if (e.target.checked && copyName === `${profile.name}-copia`) {
                setCopyName(`${profile.name}-control`);
              }
            }}
            className="mt-0.5 size-4 accent-primary"
          />
          <span>
            Hacerlo <strong className="font-semibold">grupo de control</strong>: el screener
            elige al azar en vez de puntuar.
            <span className="mt-1 block text-xs text-text-muted">
              Es contra lo que se mide si el criterio del modelo aporta algo (R7). Mismo
              universo, mismo riesgo, mismos descartes duros: lo único que cambia es que los
              candidatos no están elegidos. Si el agente rinde igual, el filtro no estaba
              aportando nada.
            </span>
          </span>
        </label>
      </ConfirmDialog>

      <ConfirmDialog
        open={pending === "delete"}
        title={`Borrar ${profile.name}`}
        confirmLabel="Borrar el experimento y su histórico"
        danger
        busy={remove.isPending}
        confirmDisabled={typedName !== profile.name}
        error={error}
        onConfirm={async () => {
          setError(null);
          try {
            await remove.mutateAsync({ ref: profile.name, confirm: typedName });
            close();
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : "No se pudo borrar.");
          }
        }}
        onCancel={close}
      >
        <p>
          Se borra el experimento entero y con él{" "}
          {profile.metrics.cycles} ciclos, {profile.metrics.decisions} decisiones y sus
          posiciones y órdenes. <strong className="font-semibold">No se puede deshacer.</strong>
        </p>
        <p className="text-text-muted">
          {/* The API demands the name in `?confirm=`, so this field is not the
              screen being cautious on its own: it is the only way the call
              succeeds. Explaining that stops it reading as a hurdle. */}
          Repite el nombre exacto para confirmar. Lo exige la API, no esta pantalla: es la
          única llamada que destruye datos que costaron semanas.
        </p>
        <Input
          label="Nombre del experimento"
          value={typedName}
          autoComplete="off"
          placeholder={profile.name}
          onChange={(e) => setTypedName(e.target.value)}
        />
      </ConfirmDialog>
    </>
  );
}
