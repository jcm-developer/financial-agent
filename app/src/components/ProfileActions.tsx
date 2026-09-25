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
import { Checkbox } from "@/components/Checkbox";
import { Button, Input } from "@/components/pieces";

/**
 * What can be done to an experiment.
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

      {/* Quiet in the row, pushed to the far end: a red button on every card
          would shout louder than the figures. The dialog is the destructive one. */}
      <Button variant="ghost" className="ml-auto" onClick={() => setPending("delete")}>
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
        <p>Deja de ejecutar ciclos. El histórico y el capital se conservan.</p>
        {/* Pausing does not close anything, and stops are only checked inside
            a cycle: that is the one consequence worth a warning. */}
        {(profile.metrics.open_positions ?? 0) > 0 && (
          <p className="font-medium text-warning">
            No cierra las {profile.metrics.open_positions} posiciones abiertas: sus stops
            dejan de vigilarse.
          </p>
        )}
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
        <p>Sale de este listado. No se borra nada.</p>
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
              // The copy exists; only the change that makes it a control
              // failed, and the "control" on screen would not be one.
              setError(
                `La copia «${created}» se creó, pero no quedó como grupo de control: ${
                  cause instanceof Error ? cause.message : "error desconocido"
                }.`,
              );
              return;
            }
          }
          close();
          navigate(`/p/${encodeURIComponent(created)}/settings`);
        }}
        onCancel={close}
      >
        <p>Copia los parámetros y el universo, no el histórico. Nace como borrador.</p>
        <Input
          label="Nombre de la copia"
          value={copyName}
          maxLength={80}
          onChange={(e) => setCopyName(e.target.value)}
        />
        {/* A control profile IS a duplicate with one parameter changed, so it is
            the same gesture with the parameter already decided. */}
        <Checkbox
          checked={asControl}
          onChange={(e) => {
            setAsControl(e.target.checked);
            if (e.target.checked && copyName === `${profile.name}-copia`) {
              setCopyName(`${profile.name}-control`);
            }
          }}
          label="Grupo de control: candidatos elegidos al azar"
        />
      </ConfirmDialog>

      <ConfirmDialog
        open={pending === "delete"}
        title={`Borrar ${profile.name}`}
        confirmLabel="Borrar definitivamente"
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
          Se borran {profile.metrics.cycles} ciclos, {profile.metrics.decisions} decisiones y
          sus posiciones y órdenes.{" "}
          <strong className="font-semibold text-foreground">No se puede deshacer.</strong>
        </p>
        {/* The API demands the name in `?confirm=`: without it the call fails. */}
        <Input
          label={`Escribe «${profile.name}» para confirmar`}
          value={typedName}
          autoComplete="off"
          placeholder={profile.name}
          onChange={(e) => setTypedName(e.target.value)}
        />
      </ConfirmDialog>
    </>
  );
}
