import { useEffect, useRef, type ReactNode } from "react";

import { Alert, BlockTitle, Button, cardClasses } from "@/components/pieces";

/**
 * The confirmation dialog (F5.4).
 *
 * ⚠️ **It is the browser's `<dialog>` and not shadcn's, which is a change of
 * plan and is worth writing down.** F4.7 left shadcn out with the note that it
 * would be brought in "when something needs Radix underneath: the confirmation
 * dialog of F5.4". Looking at it with the dialog in hand, what Radix supplies
 * here —focus trap, Esc to dismiss, the rest of the page made inert, stacking
 * above everything— is what `showModal()` already does natively, in the top
 * layer, in every browser this application runs in. So bringing it in would have
 * added a dependency tree to reimplement `::backdrop`.
 *
 * There is one thing Radix does give that this does not: animated entrances. The
 * project does not have those on purpose (DESIGN.md, "Lo que no hay"), so it is
 * not a loss.
 *
 * The rule of F4.7 still stands as written —shadcn comes in when something needs
 * Radix— it just turned out this was not it.
 */
interface Props {
  open: boolean;
  title: string;
  /** What the action does, and what it cannot undo. */
  children: ReactNode;
  /** Label of the button that goes ahead. Says the action, never "Aceptar". */
  confirmLabel: string;
  /** Danger tone, for the one action that destroys history. */
  danger?: boolean;
  /** Blocks confirming while a required check is unmet, e.g. retyping the name. */
  confirmDisabled?: boolean;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * A modal that asks before doing something that cannot be undone.
 *
 * @param props - Dialog props.
 * @param props.open - Whether the dialog is showing.
 * @param props.title - The dialog's heading.
 * @param props.children - What the action does and what it cannot undo.
 * @param props.confirmLabel - Label of the confirming button.
 * @param props.danger - Danger tone for the confirming button.
 * @param props.confirmDisabled - Blocks confirming while a check is unmet.
 * @param props.busy - Whether the action is in flight.
 * @param props.error - What went wrong, if anything.
 * @param props.onConfirm - Called when the user goes ahead.
 * @param props.onCancel - Called on cancel, Esc, or a click on the backdrop.
 * @return The rendered dialog.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  danger = false,
  confirmDisabled = false,
  busy = false,
  error,
  onConfirm,
  onCancel,
}: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    // `showModal` and not the `open` attribute: only the former puts it in the
    // top layer, traps focus and makes the rest of the page inert. Setting
    // `open` renders a dialog that looks modal and is not.
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    // Esc closes it without going through our button, so the state outside has
    // to be told or the dialog would refuse to reopen: React would still think
    // it was open and the effect above would do nothing.
    const onClose = () => onCancel();
    dialog.addEventListener("close", onClose);
    return () => dialog.removeEventListener("close", onClose);
  }, [onCancel]);

  return (
    <dialog
      ref={ref}
      // `aria-label` and not `aria-labelledby`: the action row mounts its four
      // dialogs at once, so a fixed id on the heading would be four duplicate
      // ids in the document and the reference would resolve to whichever came
      // first — which is not the open one.
      aria-label={title}
      // The card recipe comes from `cardClasses`, never written out here: that
      // is the rule pieces.tsx exists for. What is added is only what belongs to
      // being a dialog — the width, the centring, the 12 px radius Verdana gives
      // modals and dropdown panels, its lg elevation, and the backdrop. The
      // scrim is the same navy wash whatever is behind it: it darkens the page,
      // and a scrim that follows the surface stops doing that.
      className={cardClasses(
        "p-6",
        "m-auto w-[min(32rem,calc(100vw-2rem))] rounded-lg text-foreground shadow-xl backdrop:bg-primary/40",
      )}
      onCancel={(event) => {
        // While the request is in flight Esc would leave the dialog closed and
        // the action running, with nowhere to show its error.
        if (busy) event.preventDefault();
      }}
      onClick={(event) => {
        // A click on the backdrop lands on the dialog itself, never on its
        // children, which is what tells the two apart without an overlay div.
        if (!busy && event.target === ref.current) onCancel();
      }}
    >
      <BlockTitle as="h2" className="text-h4">
        {title}
      </BlockTitle>

      <div className="mt-4 flex flex-col gap-4 text-body-sm text-text-secondary">
        {children}
      </div>

      {error && <Alert className="mt-4">{error}</Alert>}

      <div className="mt-6 flex flex-wrap justify-end gap-3">
        <Button variant="ghost" onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
        <Button
          variant={danger ? "destructive" : "primary"}
          onClick={onConfirm}
          disabled={busy || confirmDisabled}
        >
          {busy ? "…" : confirmLabel}
        </Button>
      </div>
    </dialog>
  );
}
