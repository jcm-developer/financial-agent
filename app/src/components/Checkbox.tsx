import { useEffect, useRef, type ComponentProps, type ReactNode } from "react";
import { Check, Minus } from "lucide-react";

import { FieldHint } from "@/components/pieces";
import { cn } from "@/lib/utils";

/**
 * Verdana's checkbox: 18 px square, 4 px radius, a slate hairline when empty and
 * a solid navy fill with a white mark when ticked.
 *
 * It is a **real `<input type="checkbox">`** with `appearance-none`, not a
 * `<div>` wearing `role="checkbox"`: that way the keyboard, the form, the label
 * association and the screen reader's announcement are the browser's job and not
 * ours, and the only thing left to write is the paint.
 *
 * The mark sits on top as an absolutely positioned icon rather than a
 * background-image data URI, so it inherits the icon set the rest of the
 * interface uses instead of being a second, hand-drawn tick.
 */
interface Props extends Omit<ComponentProps<"input">, "type" | "children"> {
  /** Label text, in the interface language. */
  label: ReactNode;
  /** The line under the control explaining what it decides. */
  hint?: ReactNode;
  /**
   * Neither ticked nor empty: the state a "select all" takes when only some of
   * its children are. It cannot be set from HTML, only from the DOM property,
   * which is why it needs the effect below.
   */
  indeterminate?: boolean;
}

/**
 * A checkbox with its label to the right.
 *
 * @param props - Checkbox props, on top of everything an `<input>` accepts.
 * @param props.label - Label text, in the interface language.
 * @param props.hint - The line under the control.
 * @param props.indeterminate - Neither ticked nor empty.
 * @param props.className - Extra classes for the wrapping `<label>`.
 * @return The rendered checkbox inside its label.
 */
export function Checkbox({
  label,
  hint,
  indeterminate = false,
  className,
  disabled,
  ...rest
}: Props) {
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <label
      className={cn(
        "flex items-start gap-2",
        disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer",
        className,
      )}
    >
      <span className="relative mt-0.5 inline-flex shrink-0">
        <input
          ref={ref}
          type="checkbox"
          disabled={disabled}
          className={cn(
            "peer size-[18px] appearance-none rounded-sm border-[1.5px] border-border-strong bg-card",
            "transition-colors duration-150",
            "checked:border-primary checked:bg-primary",
            "indeterminate:border-primary indeterminate:bg-primary",
            "disabled:cursor-not-allowed",
          )}
          {...rest}
        />
        <Check
          className="pointer-events-none absolute inset-0 m-auto size-3 text-primary-foreground opacity-0 peer-checked:opacity-100 peer-indeterminate:opacity-0"
          strokeWidth={3}
          aria-hidden
        />
        <Minus
          className="pointer-events-none absolute inset-0 m-auto size-3 text-primary-foreground opacity-0 peer-indeterminate:opacity-100"
          strokeWidth={3}
          aria-hidden
        />
      </span>

      <span className="flex flex-col gap-1">
        <span className="text-body-sm">{label}</span>
        {hint && <FieldHint>{hint}</FieldHint>}
      </span>
    </label>
  );
}
