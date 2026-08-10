import { createElement, type ComponentProps, type ComponentType, type ReactNode } from "react";

import { Tooltip } from "@/components/Tooltip";
import { cn } from "@/lib/utils";

/**
 * The shared interface pieces, built to the Verdana Health design system.
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
 * `index.css`, so the palette is defined in exactly one place.
 */

/* -------------------------------------------------------------------------- */
/* Buttons                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * The four Verdana button variants.
 *
 * `primary` is the navy fill and is the page's one committing action; `secondary`
 * is the bordered navy outline and is what most actions are; `ghost` is the slate
 * text with no border, for what accompanies something else; `destructive` is the
 * red fill, for what cannot be undone.
 */
export type ButtonVariant = "primary" | "secondary" | "ghost" | "destructive";

/** sm 32 px · md 42 px · lg 48 px, with the padding and type size each carries. */
export type ButtonSize = "sm" | "md" | "lg";

const BUTTON_BASE =
  "inline-flex select-none items-center justify-center gap-2 rounded-md font-medium " +
  "transition-colors duration-150 ease-calm " +
  // Verdana's disabled state: 0.4 opacity, the disabled cursor, and every hover
  // and focus state suppressed — which is what the `disabled:hover:` resets do.
  "disabled:cursor-not-allowed disabled:opacity-40";

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary:
    "bg-primary text-primary-foreground shadow-sm hover:bg-primary-hover disabled:hover:bg-primary",
  secondary:
    "border border-primary bg-transparent text-primary hover:bg-primary/4 disabled:hover:bg-transparent",
  ghost:
    "bg-transparent text-text-secondary hover:bg-surface-sunken disabled:hover:bg-transparent",
  destructive:
    "bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive-hover disabled:hover:bg-destructive",
};

const BUTTON_SIZE: Record<ButtonSize, string> = {
  sm: "h-8 px-3.5 text-body-sm",
  md: "h-[2.625rem] px-5.5 text-body-sm",
  lg: "h-12 px-7 text-body",
};

/** The icon size that pairs with each button size. */
const BUTTON_ICON: Record<ButtonSize, string> = {
  sm: "size-4",
  md: "size-4",
  lg: "size-5",
};

/**
 * For when the thing wearing the button's appearance is a router `<Link>`.
 *
 * A button that navigates has to be a real link —open in another tab, copy the
 * address, see it in the status bar— so in those places the appearance is
 * shared and the element is not.
 *
 * @param variant - Which of the four button variants to use.
 * @param className - Extra classes, merged so they win over the recipe.
 * @param size - Which of the three heights to use.
 * @return The class string a `<Link>` needs to look like a button.
 */
export function buttonClasses(
  variant: ButtonVariant = "secondary",
  className?: string,
  size: ButtonSize = "md",
) {
  return cn(BUTTON_BASE, BUTTON_VARIANT[variant], BUTTON_SIZE[size], className);
}

/**
 * The application's button.
 *
 * @param props - Button props, on top of everything a `<button>` accepts.
 * @param props.variant - Which of the four button variants to use.
 * @param props.size - Which of the three heights to use.
 * @param props.icon - Decorative icon rendered before the label. Verdana asks
 *     for iconography alongside text labels, never instead of them.
 * @return The rendered button.
 */
export function Button({
  variant = "secondary",
  size = "md",
  icon: Icon,
  className,
  children,
  ...rest
}: ComponentProps<"button"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Icon to the left of the text. Always decorative: the button's text is what it says. */
  icon?: ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}) {
  return (
    <button type="button" className={buttonClasses(variant, className, size)} {...rest}>
      {Icon && <Icon className={cn(BUTTON_ICON[size], "shrink-0")} aria-hidden />}
      {children}
    </button>
  );
}

/**
 * Links, in the sage that Verdana reserves for interactive elements.
 *
 * The underline is **always** there: sage against navy body text is a hue
 * difference, and a hue difference is exactly what colour blindness takes away.
 * It is faint at rest so it does not compete with the figure beside it, and
 * becomes solid on hover.
 */
export const LINK_CLASSES =
  "text-accent underline decoration-accent/30 underline-offset-2 transition-colors " +
  "duration-150 hover:text-accent-hover hover:decoration-current";

/**
 * A `<button>` that reads as a link.
 *
 * Used when the action does not navigate —opening a detail, folding a log,
 * switching to the table view— but visually belongs to the text. It is a button
 * and not an `<a>` without `href` because the keyboard and screen readers have
 * to announce it as what it does.
 *
 * @param props - Button props, on top of everything a `<button>` accepts.
 * @param props.variant - Whether the text is the sage of a link or muted slate.
 * @return The rendered button.
 */
export function LinkButton({
  variant = "accent",
  className,
  children,
  ...rest
}: ComponentProps<"button"> & { variant?: "accent" | "subtle" }) {
  return (
    <button
      type="button"
      className={cn(
        "text-body-sm underline underline-offset-2 transition-colors duration-150",
        variant === "accent"
          ? "text-accent decoration-accent/30 hover:text-accent-hover hover:decoration-current"
          : "text-text-secondary decoration-border hover:text-foreground hover:decoration-current",
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
 * Verdana gives the card two forms and they are alternatives, not a scale:
 * **default** is bordered and flat, **elevated** drops the border and takes the
 * md shadow instead. A card with both would read as two levels at once.
 */
const CARD_DEFAULT = "rounded-md border border-border bg-card";
const CARD_ELEVATED = "rounded-md bg-card shadow-lg";

/**
 * The card recipe as a class string, for when the card is a `<Link>`: the
 * experiment list is made of cards that navigate.
 *
 * @param padding - Padding utility. Verdana's card padding is 24 px (`p-6`).
 *     `p-0` for a card that wraps a table.
 * @param className - Extra classes, merged so they win over the recipe.
 * @param elevated - Shadow instead of border.
 * @return The class string.
 */
export function cardClasses(padding = "p-6", className?: string, elevated = false) {
  return cn(elevated ? CARD_ELEVATED : CARD_DEFAULT, padding, className);
}

/**
 * The card: white surface, hairline slate border, 8 px radius, 24 px of padding.
 *
 * @param props - Card props, on top of everything a `<div>` accepts.
 * @param props.as - Element to render, so a card can carry the right semantics
 *     without changing how it looks.
 * @param props.padding - Padding utility.
 * @param props.elevated - Drops the border and takes the md shadow instead.
 * @param props.dashed - Dashed border, for the gap left by something that does
 *     not exist yet.
 * @return The rendered card.
 */
export function Card({
  as = "div",
  padding = "p-6",
  elevated = false,
  dashed = false,
  className,
  children,
  ...rest
}: ComponentProps<"div"> & {
  as?: "div" | "section" | "article";
  /**
   * The padding, as a prop and not as something overridden from `className`.
   *
   * `p-6` and `px-6 py-8` are not the same utility group, so which one wins is
   * decided by the order of the stylesheet and not the order of the classes:
   * passing it here is what makes a card with no padding (`p-0`, the one that
   * wraps a table) predictable.
   */
  padding?: string;
  /** Verdana's elevated card: no border, md shadow. */
  elevated?: boolean;
  /** Dashed border: the gap left by something that is not there yet (empties, pending screens). */
  dashed?: boolean;
}) {
  return createElement(
    as,
    {
      className: cardClasses(
        padding,
        cn(dashed && "border-dashed bg-transparent shadow-none", className),
        elevated && !dashed,
      ),
      ...rest,
    },
    children,
  );
}

/**
 * Verdana's tinted header strip: navy ground, white text, sitting flush inside
 * the top of a card. It is the category label of the block underneath.
 *
 * @param props - Everything a `<div>` accepts.
 * @return The rendered strip, already squaring its bottom corners.
 */
export function CardHeaderStrip({ className, children, ...rest }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "rounded-t-md bg-primary px-6 py-3 text-caption tracking-[0.5px] text-primary-foreground uppercase",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

/**
 * One headline figure, as a card: the label small above, the number large below
 * and, when it needs one, the line that explains it.
 *
 * **It lives here and not in a screen** because it now appears on two —the row
 * of eight on Resumen and the row of four over the open book on Posiciones— and
 * the project already learned this with `PriceSource`: two copies of the same
 * card drift, and the one read more often ends up being the one that informs
 * worse. Anything that changes about a figure changes here for both.
 *
 * It is deliberately **not** `Stat`. `Stat` is a `<dt>`/`<dd>` pair for a set
 * describing one thing —a cycle's detail— and belongs inside a `<dl>`; this is
 * a card that stands on its own in a grid.
 *
 * @param props - Figure props.
 * @param props.label - Label, in the interface language.
 * @param props.value - The figure, already formatted with its currency symbol.
 * @param props.className - Extra classes for the figure, to colour it by sign.
 *     It comes from `signClass()`, never written by hand.
 * @param props.title - The whole sentence, when the figure needs one to be read.
 * @param props.children - The footnote below it, when the figure needs one.
 * @return The rendered card.
 */
export function Figure({
  label,
  value,
  className,
  title,
  children,
}: {
  label: string;
  value: string;
  className?: string;
  title?: string;
  children?: ReactNode;
}) {
  return (
    <Card padding="p-6">
      <p className="text-caption text-text-muted">{label}</p>
      <p className={cn("tabular mt-0.5 text-h3 font-semibold", className)} title={title}>
        {value}
      </p>
      {children && <p className="mt-1 text-caption leading-snug">{children}</p>}
    </Card>
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
      className={cn(
        "overflow-auto rounded-md bg-surface-sunken p-4 text-code text-text-secondary",
        className,
      )}
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
 * risk summary, say— and is placed to the right with the baselines aligned.
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
  const title = <h1 className="text-h1">{children}</h1>;

  if (!aside) return <div className="mb-8">{title}</div>;

  return (
    <div className="mb-8 flex flex-wrap items-baseline justify-between gap-4">
      {title}
      <p className="text-body-sm text-text-secondary">{aside}</p>
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
  return <h2 className={cn("text-h3", className)}>{children}</h2>;
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
  return createElement(as, { className: cn("text-h4", className) }, children);
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
 * The figure goes in Fira Code through `.tabular`, which is Verdana's rule for
 * results. It has to sit inside a `<dl>`.
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
      <dt className="text-caption text-text-secondary">{label}</dt>
      <dd className={cn("tabular mt-1 text-body font-medium", valueClass)} title={title}>
        {value}
      </dd>
      {children && (
        <dd className="mt-1 text-caption font-normal text-text-secondary">{children}</dd>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* States and notices                                                         */
/* -------------------------------------------------------------------------- */

/**
 * An inline notice, in the four state tones.
 *
 * It carries `role="alert"` because it almost always appears after an action
 * —launching a cycle, stopping it, a query that fails on refresh— and without it
 * a screen reader says nothing: the focus is still on the button that was just
 * pressed and the new text is elsewhere in the document.
 *
 * @param props - Everything a `<div>` accepts.
 * @param props.tone - Which state hue tints it. Errors by default, which is what
 *     the overwhelming majority of these are.
 * @return The rendered alert, already carrying `role="alert"`.
 */
export function Alert({
  tone = "error",
  className,
  children,
  ...rest
}: ComponentProps<"div"> & { tone?: "error" | "warning" | "success" | "info" }) {
  const TONE = {
    error: "bg-error/8 text-error-ink",
    warning: "bg-warning-mark/8 text-warning",
    success: "bg-success/8 text-success-ink",
    info: "bg-info/8 text-info-ink",
  } as const;

  return (
    <div
      role="alert"
      className={cn("rounded-md p-4 text-body-sm", TONE[tone], className)}
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
    <p role="status" className={cn("text-body-sm text-text-secondary", className)}>
      {text}
    </p>
  );
}

/* -------------------------------------------------------------------------- */
/* Chips                                                                      */
/* -------------------------------------------------------------------------- */

/**
 * Verdana's chip: 12 px, medium, uppercase, half a pixel of tracking, 4 px
 * radius. Its variants are two different jobs and mixing them is the easy
 * mistake — `filter`/`filterActive` are a **control** you can press, and the
 * three status tones are a **read-only state**.
 */
export type ChipVariant =
  | "filter"
  | "filterActive"
  | "success"
  | "warning"
  | "error"
  | "info"
  | "neutral";

const CHIP_VARIANT: Record<ChipVariant, string> = {
  filter: "border border-border bg-background text-foreground",
  filterActive: "bg-primary text-primary-foreground",
  success: "bg-success/8 text-success-ink",
  warning: "bg-warning-mark/8 text-warning",
  error: "bg-error/8 text-error-ink",
  info: "bg-info/8 text-info-ink",
  neutral: "bg-surface-sunken text-text-secondary",
};

/**
 * The chip.
 *
 * @param props - Chip props, on top of everything a `<span>` accepts.
 * @param props.variant - Which of the seven tones to use.
 * @return The rendered chip.
 */
export function Chip({
  variant = "neutral",
  className,
  children,
  ...rest
}: ComponentProps<"span"> & { variant?: ChipVariant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm px-3 py-1 text-caption tracking-[0.5px] whitespace-nowrap uppercase",
        CHIP_VARIANT[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}

/**
 * The state tones a `<Tag>` can take, kept as the domain's own vocabulary.
 *
 * `<Tag>` and `<Badge>` are what the screens already call these, and they are
 * **thin aliases over `<Chip>`** rather than components of their own: renaming
 * thirty call sites would have been a change to the screens, and the point of
 * this file is that the recipe lives in one place regardless of what the caller
 * asks for it by.
 */
type TagTone = "inherit" | "neutral" | "good" | "warning" | "bad" | "info";

const TAG_TONE: Record<TagTone, ChipVariant> = {
  inherit: "neutral",
  neutral: "neutral",
  good: "success",
  warning: "warning",
  bad: "error",
  info: "info",
};

/**
 * Bordered chip for counts and header states.
 *
 * @param props - Badge props, on top of everything a `<span>` accepts.
 * @param props.compact - Kept for the call sites that pass it. Verdana's chip is
 *     one size, so it no longer changes anything and is accepted and ignored.
 * @return The rendered chip.
 */
export function Badge({
  compact: _compact = false,
  className,
  children,
  ...rest
}: ComponentProps<"span"> & { compact?: boolean }) {
  return (
    <Chip variant="filter" className={className} {...rest}>
      {children}
    </Chip>
  );
}

/**
 * The status chip stuck to a figure: `VIVO`, `CICLO`, `SIN PRECIO`, `SIN MODELO`.
 *
 * **It is never the only carrier of meaning**: it always comes with a `title`
 * holding the whole sentence, because four uppercase letters explain nothing on
 * their own and the colour explains even less.
 *
 * @param props - Tag props, on top of everything a `<span>` accepts. Callers
 *     must pass `title` with the full sentence.
 * @param props.tone - Which state tone colours it.
 * @return The rendered chip.
 */
export function Tag({
  tone = "neutral",
  title,
  className,
  children,
  ...rest
}: ComponentProps<"span"> & { tone?: TagTone }) {
  const chip = (
    <Chip variant={TAG_TONE[tone]} className={cn("align-middle", className)} {...rest}>
      {children}
    </Chip>
  );

  // The whole sentence goes in a Verdana tooltip rather than the browser's
  // `title`, which the design system cannot style. Every call site already
  // passes it, so upgrading them all is this one branch — and the ones that do
  // not pass it degrade to a bare chip instead of breaking.
  if (!title) return <span className="ml-2 inline-flex align-middle">{chip}</span>;

  return (
    <Tooltip content={title} className="ml-2 align-middle">
      {chip}
    </Tooltip>
  );
}

/* -------------------------------------------------------------------------- */
/* Form controls                                                              */
/* -------------------------------------------------------------------------- */

/**
 * The Verdana field recipe: 42 px tall, 10×14 of padding, 8 px radius, hairline
 * border that goes navy on hover, and on focus a navy edge with its 3 px halo.
 *
 * The halo is a `ring` and the second pixel of the border is an inset shadow,
 * both of which paint outside the box model: spec-ing focus as a 2 px border
 * literally would move every character in the field by one pixel on focus.
 *
 * Exported because the custom `<Select>` trigger has to be the same object as an
 * `<Input>` — it is a field, and a field that is a button still has to look like
 * the field next to it.
 */
export const CONTROL_CLASSES =
  "h-[2.625rem] w-full rounded-md border border-border bg-card px-3.5 py-2.5 text-body-sm " +
  "text-foreground transition-[color,box-shadow,border-color] duration-150 " +
  "placeholder:text-text-muted hover:border-primary " +
  "focus:border-primary focus:ring-[3px] focus:ring-primary/10 focus:outline-none " +
  "focus-visible:outline-none " +
  "disabled:cursor-not-allowed disabled:border-border disabled:bg-surface-sunken disabled:opacity-100";

/** The error state of the same recipe: red edge and a red halo. */
export const CONTROL_INVALID =
  "border-error ring-[3px] ring-error/10 hover:border-error focus:border-error focus:ring-error/10";

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
        row ? "flex items-center gap-3" : "flex flex-col gap-1.5",
        className,
      )}
    >
      <span className="text-body-sm font-medium whitespace-nowrap text-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}

/**
 * The line under a field that says what it decides.
 *
 * @param props - Hint props.
 * @param props.error - Colours it red and makes it the error text instead.
 * @param props.children - The wording.
 * @return The rendered helper line.
 */
export function FieldHint({
  error = false,
  children,
}: {
  error?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "text-caption font-normal",
        error ? "text-error-ink" : "text-text-secondary",
      )}
    >
      {children}
    </span>
  );
}

/**
 * Text field.
 *
 * @param props - Input props, on top of everything an `<input>` accepts.
 * @param props.label - Label text, in the interface language.
 * @param props.hint - The line under the field explaining what it decides.
 * @param props.error - What is wrong with what was typed, shown instead of the hint.
 * @param props.fieldClass - Extra classes for the wrapping `<label>`.
 * @return The rendered input inside its label.
 */
export function Input({
  label,
  hint,
  error,
  fieldClass,
  className,
  ...rest
}: ComponentProps<"input"> & {
  label: string;
  /** What the field decides, when the label alone does not say it. */
  hint?: ReactNode;
  /** What is wrong with the value. Replaces the hint and turns the field red. */
  error?: ReactNode;
  /** Classes for the wrapping `<label>`, not for the box. */
  fieldClass?: string;
}) {
  return (
    <Field label={label} className={fieldClass}>
      <input
        aria-invalid={error ? true : undefined}
        className={cn(CONTROL_CLASSES, error && CONTROL_INVALID, className)}
        {...rest}
      />
      {error ? (
        <FieldHint error>{error}</FieldHint>
      ) : (
        hint && <FieldHint>{hint}</FieldHint>
      )}
    </Field>
  );
}

/**
 * A 1–10 slider with its value beside the label.
 *
 * **The value is always in sight, and that is not decoration**: a slider whose
 * number you cannot read is a control you cannot set on purpose, and these two
 * —risk profile and diversification— are the ones the whole experiment is
 * described by. The ends are named as well, because "1" and "10" do not say
 * which way is more risk.
 *
 * The filled half of the track is a gradient stop driven by the `--fill` custom
 * property set here: CSS cannot read an input's value, so without this the track
 * is a uniform bar and the control stops showing where in the range it sits.
 *
 * @param props - Slider props, on top of everything an `<input type="range">` accepts.
 * @param props.label - Label text, in the interface language.
 * @param props.value - Current value, shown next to the label.
 * @param props.low - What the low end means, in the interface language.
 * @param props.high - What the high end means.
 * @param props.hint - The line under the slider explaining what it decides.
 * @return The rendered slider inside its label.
 */
export function Slider({
  label,
  value,
  low,
  high,
  hint,
  className,
  ...rest
}: Omit<ComponentProps<"input">, "type" | "value"> & {
  label: string;
  value: number;
  low: string;
  high: string;
  hint?: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="flex items-baseline justify-between gap-2">
        <span className="text-body-sm font-medium text-foreground">{label}</span>
        <span className="tabular text-body-sm font-medium">{value}/10</span>
      </span>
      <input
        type="range"
        min={1}
        max={10}
        step={1}
        value={value}
        style={{ "--fill": `${((value - 1) / 9) * 100}%` } as React.CSSProperties}
        className={cn("slider", className)}
        {...rest}
      />
      <span className="flex justify-between text-caption font-normal text-text-secondary">
        <span>{low}</span>
        <span>{high}</span>
      </span>
      {hint && <FieldHint>{hint}</FieldHint>}
    </label>
  );
}
