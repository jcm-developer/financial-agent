import { createElement, type ComponentProps, type ComponentType, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The shared interface pieces.
 *
 * It exists because the alternative —copying the class string into every
 * screen— had already begun to diverge, and in a way you cannot see by looking
 * at one screen but at two: the same button carried `px-3 py-1` in the
 * pagination, `px-2.5 py-1` in the theme switch and `px-3 py-1.5` when it was a
 * link; of the four dropdowns, only one had the `hover`; and the same error
 * notice came out with `p-3` and a background on one screen and with `p-2` and
 * none on another.
 *
 * The rule DESIGN.md imposes is that none of these recipes is ever written by
 * hand again. If something needs a variant, it is added here.
 *
 * **It carries no colours of its own**: everything comes from the tokens in
 * `index.css`, which is what lets the theme switch touch no component at all.
 */

/* -------------------------------------------------------------------------- */
/* Buttons                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * The three buttons that exist. There is deliberately no fourth solid brand
 * button: on a data screen colour fill is reserved for the figures, and a blue
 * button would compete with the chart series for the same attention.
 */
type ButtonVariant = "neutral" | "subtle" | "danger";

const BUTTON_BASE =
  "inline-flex min-h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-card px-3 py-1 text-[13px] transition-colors hover:bg-surface-sunken disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-card";

const BUTTON_TONE: Record<ButtonVariant, string> = {
  neutral: "",
  subtle: "text-text-secondary",
  danger: "text-delta-bad",
};

/**
 * For when the thing wearing the button's appearance is a router `<Link>`.
 *
 * A button that navigates has to be a real link —open in another tab, copy the
 * address, see it in the status bar— so in those places the appearance is
 * shared and the element is not.
 *
 * @param variant - Which of the three button tones to use.
 * @param className - Extra classes, merged so they win over the recipe.
 * @return The class string a `<Link>` needs to look like a button.
 */
export function buttonClasses(variant: ButtonVariant = "neutral", className?: string) {
  return cn(BUTTON_BASE, BUTTON_TONE[variant], className);
}

/**
 * The application's button.
 *
 * @param props - Button props, on top of everything a `<button>` accepts.
 * @param props.variant - Which of the three button tones to use.
 * @param props.icon - Decorative icon rendered before the label.
 * @return The rendered button.
 */
export function Button({
  variant = "neutral",
  icon: Icon,
  className,
  children,
  ...rest
}: ComponentProps<"button"> & {
  variant?: ButtonVariant;
  /** Icon to the left of the text. Always decorative: the button's text is what it says. */
  icon?: ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}) {
  return (
    <button type="button" className={buttonClasses(variant, className)} {...rest}>
      {Icon && <Icon className="size-3.5 shrink-0" aria-hidden />}
      {children}
    </button>
  );
}

/**
 * The faint underline links carry inside tables and text.
 *
 * `decoration-border` at rest and `decoration-current` on hover: the underline
 * is always there —removing it would leave the link distinguishable by colour
 * alone, which is exactly what F4.9 forbids— but it does not compete with the
 * figure next to it until you point at it.
 */
export const LINK_CLASSES =
  "underline decoration-border transition-colors hover:decoration-current";

/**
 * A `<button>` that reads as a link.
 *
 * Used when the action does not navigate —opening a detail, folding a log,
 * switching to the table view— but visually belongs to the text. It is a button
 * and not an `<a>` without `href` because the keyboard and screen readers have
 * to announce it as what it does.
 *
 * @param props - Button props, on top of everything a `<button>` accepts.
 * @param props.variant - Whether the text inherits the colour or is muted.
 * @return The rendered button.
 */
export function LinkButton({
  variant = "subtle",
  className,
  children,
  ...rest
}: ComponentProps<"button"> & { variant?: "neutral" | "subtle" }) {
  return (
    <button
      type="button"
      className={cn(
        LINK_CLASSES,
        "text-[13px]",
        variant === "subtle" && "text-text-secondary",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Surfaces                                                                   */
/* -------------------------------------------------------------------------- */

/**
 * The card: thin border, surface background and a shadow that is `none` in dark.
 *
 * The shadow lives here and not in each place because two of the seven cards
 * used to carry it, and in the light theme that made some float and others not
 * with no criterion at all.
 */
const CARD_BASE = "rounded-lg border border-border bg-card shadow-[var(--shadow-card)]";

/**
 * The card recipe as a class string, for when the card is a `<Link>`: the
 * experiment list is made of cards that navigate.
 *
 * @param padding - Padding utility. `p-0` for a card that wraps a table.
 * @param className - Extra classes, merged so they win over the recipe.
 * @return The class string.
 */
export function cardClasses(padding = "p-4", className?: string) {
  return cn(CARD_BASE, padding, className);
}

/**
 * The card: thin border, surface background, and a shadow that is `none` in dark.
 *
 * @param props - Card props, on top of everything a `<div>` accepts.
 * @param props.as - Element to render, so a card can carry the right semantics
 *     without changing how it looks.
 * @param props.padding - Padding utility.
 * @param props.dashed - Dashed border, for the gap left by something that does
 *     not exist yet.
 * @return The rendered card.
 */
export function Card({
  as = "div",
  padding = "p-4",
  dashed = false,
  className,
  children,
  ...rest
}: ComponentProps<"div"> & {
  as?: "div" | "section" | "article";
  /**
   * The padding, as a prop and not as something overridden from `className`.
   *
   * `p-4` and `px-4 py-6` are not the same utility group, so which one wins is
   * decided by the order of the stylesheet and not the order of the classes:
   * passing it here is what makes a card with no padding (`p-0`, the one that
   * wraps a table) predictable.
   */
  padding?: string;
  /** Dashed border: the gap left by something that is not there yet (empties, pending screens). */
  dashed?: boolean;
}) {
  return createElement(
    as,
    {
      className: cardClasses(
        padding,
        cn(dashed && "border-dashed shadow-none", className),
      ),
      ...rest,
    },
    children,
  );
}

/**
 * Preformatted text: logs, parameter JSON and console commands.
 *
 * @param props - Everything a `<pre>` accepts.
 * @return The rendered block.
 */
export function Block({ className, children, ...rest }: ComponentProps<"pre">) {
  return (
    <pre
      className={cn("overflow-auto rounded-md bg-surface-sunken p-3 text-xs", className)}
      {...rest}
    >
      {children}
    </pre>
  );
}

/* -------------------------------------------------------------------------- */
/* Titles                                                                     */
/* -------------------------------------------------------------------------- */

/**
 * A screen's `<h1>`, with its bottom gap included.
 *
 * `aside` is for whatever accompanies the title on the same line —the profile's
 * risk summary, say— and is placed to the right with the baselines aligned,
 * which is what stops 13 px text next to 17 px text from looking dropped.
 *
 * @param props - Title props.
 * @param props.children - The title itself.
 * @param props.aside - What sits beside it on the same baseline.
 * @return The rendered heading, with its bottom margin.
 */
export function PageTitle({
  children,
  aside,
}: {
  children: ReactNode;
  aside?: ReactNode;
}) {
  const title = <h1 className="text-[17px] font-semibold tracking-tight">{children}</h1>;

  if (!aside) return <div className="mb-5">{title}</div>;

  return (
    <div className="mb-5 flex flex-wrap items-baseline justify-between gap-3">
      {title}
      <p className="text-[13px] text-text-secondary">{aside}</p>
    </div>
  );
}

/**
 * The `<h2>` heading a block inside a screen.
 *
 * @param props - Heading props.
 * @param props.children - The heading text.
 * @param props.className - Extra classes.
 * @return The rendered heading.
 */
export function SectionTitle({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <h2
      className={cn(
        "text-[13px] font-semibold tracking-wide text-text-secondary uppercase",
        className,
      )}
    >
      {children}
    </h2>
  );
}

/**
 * The title of a card or a chart.
 *
 * `as` exists because the heading level depends on where the block hangs —a
 * chart inside a section is an `h3`, and a notice that fills the whole screen is
 * that screen's `h1`— while the appearance is the same.
 *
 * @param props - Heading props.
 * @param props.children - The heading text.
 * @param props.as - Heading level, chosen by where the block hangs.
 * @param props.className - Extra classes.
 * @return The rendered heading.
 */
export function BlockTitle({
  children,
  as = "h3",
  className,
}: {
  children: ReactNode;
  as?: "h1" | "h2" | "h3";
  className?: string;
}) {
  return createElement(
    as,
    { className: cn("text-[13px] font-semibold", className) },
    children,
  );
}

/**
 * A label with its figure underneath, as a `<dt>`/`<dd>` pair.
 *
 * It is the shape a figure takes when it belongs to a set describing one thing
 * —the cycle's detail, an experiment's metrics— which is why it is a description
 * list and not two `<div>`s: read aloud, "Capital: 10.240,00 €" is a pair, and
 * without the pairing a screen reader just reads eight labels and then eight
 * numbers.
 *
 * It lives here because it already existed twice, written by hand, and a third
 * copy is exactly the drift this module was created to stop. It has to sit
 * inside a `<dl>`.
 *
 * @param props - Stat props.
 * @param props.label - The label, in the interface language.
 * @param props.value - The figure, already formatted.
 * @param props.valueClass - Extra classes for the figure, for the P&L colour.
 *     It always comes from `signClass()`, never written by hand.
 * @param props.title - The whole sentence, when the figure needs one to be read.
 * @param props.children - What goes under the figure, for the line that explains it.
 * @return The rendered pair.
 */
export function Stat({
  label,
  value,
  valueClass,
  title,
  children,
}: {
  label: string;
  value: string;
  valueClass?: string;
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div>
      <dt className="text-text-muted">{label}</dt>
      <dd className={cn("tabular", valueClass)} title={title}>
        {value}
      </dd>
      {children && <dd className="text-xs leading-snug text-text-muted">{children}</dd>}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* States and notices                                                         */
/* -------------------------------------------------------------------------- */

/**
 * An inline error.
 *
 * It carries `role="alert"` because it almost always appears after an action
 * —launching a cycle, stopping it, a query that fails on refresh— and without it
 * a screen reader says nothing: the focus is still on the button that was just
 * pressed and the new text is elsewhere in the document.
 *
 * @param props - Everything a `<div>` accepts.
 * @return The rendered alert, already carrying `role="alert"`.
 */
export function Alert({
  className,
  children,
  ...rest
}: ComponentProps<"div">) {
  return (
    <div
      role="alert"
      className={cn(
        "rounded-md border border-negative/40 bg-card p-3 text-[13px] text-negative-ink",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

/**
 * "Cargando…".
 *
 * `role="status"` so the wait is announced without stealing focus. It is the
 * only loading indicator there is: no skeletons and no spinners, because this
 * application's queries run against a local SQLite and finish before a
 * placeholder would paint — except the analytics, which warns with its own text.
 *
 * @param props - Loading props.
 * @param props.text - What to announce. Worth overriding when the wait is long
 *     enough that the generic wording would not say what is being waited for.
 * @param props.className - Extra classes.
 * @return The rendered notice, already carrying `role="status"`.
 */
export function Loading({
  text = "Cargando…",
  className,
}: {
  text?: string;
  className?: string;
}) {
  return (
    <p role="status" className={cn("text-[13px] text-text-muted", className)}>
      {text}
    </p>
  );
}

/* -------------------------------------------------------------------------- */
/* Badges                                                                     */
/* -------------------------------------------------------------------------- */

/**
 * Bordered pill: counts, header states.
 *
 * @param props - Badge props, on top of everything a `<span>` accepts.
 * @param props.compact - Tighter variant, for when the pill sits inside a row.
 * @return The rendered badge.
 */
export function Badge({
  compact = false,
  className,
  children,
  ...rest
}: ComponentProps<"span"> & { compact?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-[13px]",
        compact && "px-[9px] py-0.5 text-xs font-semibold",
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}

/**
 * The tiny uppercase tag stuck to a figure: `VIVO`, `CICLO`, `SIN PRECIO`,
 * `SIN MODELO`.
 *
 * **It is never the only carrier of meaning** (F4.9): it always comes with a
 * `title` holding the whole sentence, because four uppercase letters explain
 * nothing on their own and the colour explains even less.
 */
type TagTone = "inherit" | "neutral" | "good" | "warning" | "bad";

const TAG_TONE: Record<TagTone, string> = {
  inherit: "",
  neutral: "text-text-muted",
  good: "text-delta-good",
  warning: "text-warning",
  bad: "text-delta-bad",
};

/**
 * The tiny uppercase tag stuck to a figure.
 *
 * @param props - Tag props, on top of everything a `<span>` accepts. Callers
 *     must pass `title` with the full sentence: the colour and four uppercase
 *     letters never carry the meaning on their own (F4.9).
 * @param props.tone - Which token colours the tag, or `inherit` to inherit.
 * @return The rendered tag.
 */
export function Tag({
  tone = "inherit",
  className,
  children,
  ...rest
}: ComponentProps<"span"> & { tone?: TagTone }) {
  return (
    <span
      className={cn(
        "ml-1.5 align-middle text-[10px] font-semibold uppercase",
        TAG_TONE[tone],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Form controls                                                              */
/* -------------------------------------------------------------------------- */

const CONTROL_CLASSES =
  "min-h-8 rounded-md border border-border bg-card px-2 py-1 text-[13px] transition-colors hover:bg-surface-sunken";

/**
 * A control's label, wrapping it.
 *
 * It is a `<label>` containing the control and not an `htmlFor` with a made-up
 * `id`: that way the association cannot break when the block is copied, and
 * clicking the text focuses the field without writing anything else.
 *
 * @param props - Field props.
 * @param props.label - Label text, in the interface language.
 * @param props.row - Label to the left instead of above.
 * @param props.className - Extra classes for the `<label>`.
 * @param props.children - The control being wrapped.
 * @return The rendered label with its control inside.
 */
export function Field({
  label,
  row = false,
  className,
  children,
}: {
  label: string;
  /** Label to the left instead of above. For the header, where there is no height to spend. */
  row?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label
      className={cn(
        "text-[13px]",
        row ? "flex items-center gap-2" : "flex flex-col gap-1",
        className,
      )}
    >
      <span className="text-text-muted">{label}</span>
      {children}
    </label>
  );
}

/**
 * Native dropdown.
 *
 * It is still the browser's `<select>` on purpose: this application's lists have
 * three or four entries and the native one is already keyboard-accessible
 * without pulling in a popover. It will change when something has to be shown
 * inside each option.
 *
 * @param props - Select props, on top of everything a `<select>` accepts.
 * @param props.label - Label text, in the interface language.
 * @param props.row - Label to the left instead of above.
 * @param props.fieldClass - Extra classes for the wrapping `<label>`.
 * @param props.options - Options as `[value, text]` pairs, text already in the
 *     interface language.
 * @return The rendered select inside its label.
 */
export function Select({
  label,
  row,
  fieldClass,
  className,
  options,
  ...rest
}: Omit<ComponentProps<"select">, "children"> & {
  label: string;
  row?: boolean;
  /** Classes for the wrapping `<label>`, not for the dropdown. */
  fieldClass?: string;
  options: readonly (readonly [value: string, text: string])[];
}) {
  return (
    <Field label={label} row={row} className={fieldClass}>
      <select className={cn(CONTROL_CLASSES, className)} {...rest}>
        {options.map(([value, text]) => (
          <option key={value} value={value}>
            {text}
          </option>
        ))}
      </select>
    </Field>
  );
}

/**
 * Text field.
 *
 * @param props - Input props, on top of everything an `<input>` accepts.
 * @param props.label - Label text, in the interface language.
 * @param props.fieldClass - Extra classes for the wrapping `<label>`.
 * @return The rendered input inside its label.
 */
export function Input({
  label,
  fieldClass,
  className,
  ...rest
}: ComponentProps<"input"> & {
  label: string;
  /** Classes for the wrapping `<label>`, not for the box. */
  fieldClass?: string;
}) {
  return (
    <Field label={label} className={fieldClass}>
      <input className={cn(CONTROL_CLASSES, className)} {...rest} />
    </Field>
  );
}
