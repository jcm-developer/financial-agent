import { useId, type ReactNode } from "react";

import { FieldHint } from "@/components/pieces";
import { cn } from "@/lib/utils";

/**
 * Verdana's radio button: an 18 px circle, a slate hairline when empty, and when
 * chosen a 2 px navy ring with an 8 px navy dot inside it.
 *
 * Radios come in groups or they do not mean anything, so the exported piece is
 * the **group** and not the single control: a lone radio cannot be unchecked and
 * is therefore a checkbox that lies. The group carries the `<fieldset>` and the
 * `<legend>`, which is what a screen reader needs to say what the choice is
 * about before reading the four options.
 *
 * Like the checkbox, these are real `<input type="radio">` with
 * `appearance-none`: arrow-key navigation between the options of a group is
 * behaviour the browser already has and that a `role="radio"` reimplementation
 * would have to earn back.
 */
export interface RadioOption {
  value: string;
  label: ReactNode;
  /** What this option means, when the label alone does not say it. */
  hint?: ReactNode;
  disabled?: boolean;
}

interface Props {
  /** What the choice is about, in the interface language. */
  legend: string;
  /** The chosen value. */
  value: string;
  options: readonly RadioOption[];
  /** Called with the chosen value. */
  onChange: (value: string) => void;
  /** Lays the options out in a row instead of a column. */
  row?: boolean;
  className?: string;
}

/**
 * A group of radio buttons with its legend.
 *
 * @param props - Group props.
 * @param props.legend - What the choice is about.
 * @param props.value - The chosen value.
 * @param props.options - The options.
 * @param props.onChange - Called with the chosen value.
 * @param props.row - Lays the options out in a row.
 * @param props.className - Extra classes for the `<fieldset>`.
 * @return The rendered group.
 */
export function RadioGroup({
  legend,
  value,
  options,
  onChange,
  row = false,
  className,
}: Props) {
  // One name per mounted group: two groups sharing a name would behave as a
  // single choice, which is the classic way this breaks silently.
  const name = useId();

  return (
    <fieldset className={cn("flex flex-col gap-2", className)}>
      <legend className="mb-1.5 text-body-sm font-medium text-foreground">{legend}</legend>

      <div className={cn("flex gap-x-6 gap-y-2", row ? "flex-wrap" : "flex-col")}>
        {options.map((option) => (
          <label
            key={option.value}
            className={cn(
              "flex items-start gap-2",
              option.disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer",
            )}
          >
            <span className="relative mt-0.5 inline-flex shrink-0">
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={option.value === value}
                disabled={option.disabled}
                onChange={() => onChange(option.value)}
                className={cn(
                  "peer size-[18px] appearance-none rounded-full border-[1.5px] border-border-strong bg-card",
                  "transition-colors duration-150",
                  "checked:border-2 checked:border-primary",
                  "disabled:cursor-not-allowed",
                )}
              />
              <span
                aria-hidden
                className="pointer-events-none absolute inset-0 m-auto size-2 rounded-full bg-primary opacity-0 peer-checked:opacity-100"
              />
            </span>

            <span className="flex flex-col gap-1">
              <span className="text-body-sm">{option.label}</span>
              {option.hint && <FieldHint>{option.hint}</FieldHint>}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
