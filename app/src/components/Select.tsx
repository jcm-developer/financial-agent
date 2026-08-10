import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";

import { CONTROL_CLASSES, FieldHint } from "@/components/pieces";
import { cn } from "@/lib/utils";

/**
 * The dropdown, written by hand instead of left as the browser's `<select>`.
 *
 * A native `<select>` renders its list with the operating system's own widget,
 * which means the panel is the only surface in the application that Verdana
 * cannot reach: no 12 px radius, no diffused elevation, no DM Sans, no check
 * mark on the chosen row, and on Windows a white popup with square corners under
 * a design system that has none. Replacing it is what makes the dropdown part of
 * the system rather than an exception to it.
 *
 * What it does NOT give up, because it is what the native one was worth:
 *
 * - **The full keyboard contract.** `↓`/`↑`/`Home`/`End` move, `Enter` and
 *   `Space` choose, `Esc` closes, `Tab` closes and moves on, and typing letters
 *   jumps to the option that starts with them — including the repeated-letter
 *   cycling a native select does.
 * - **The right announcement.** `combobox` with `aria-expanded` and
 *   `aria-controls` on the trigger, `listbox` on the panel and `option` with
 *   `aria-selected` on each row, tied together with `aria-activedescendant` so
 *   focus never leaves the trigger and no focus trap is needed.
 *
 * The panel is rendered in a **portal**, and that is not a detail: several of
 * these sit inside cards that carry `overflow-x-auto` for their tables, and a
 * panel positioned inside one would be clipped by it.
 *
 * ⚠️ It stacks with `z-index`, so it would render **behind** a `<dialog>` opened
 * with `showModal()`, which lives in the top layer. No dialog holds one today;
 * the day one does, the panel has to move into the dialog.
 */

/** An option, as the value that travels and the text that is read. */
export type SelectOption = readonly [value: string, text: string];

/* -------------------------------------------------------------------------- */
/* The pure parts, kept out of the component so they can be tested             */
/* -------------------------------------------------------------------------- */

/** Height of one option row, and the panel's own padding, both in pixels. */
const OPTION_HEIGHT = 38;
const PANEL_PADDING = 8;
/** Gap between trigger and panel, and the margin kept against the viewport. */
const GAP = 6;
const EDGE = 8;
/** Never squeeze the panel below this; flip it above the trigger instead. */
const MIN_HEIGHT = 120;
const MAX_HEIGHT = 320;

export interface PanelPosition {
  left: number;
  top: number;
  width: number;
  maxHeight: number;
  /** Whether it opens downwards. Drives the animation's origin. */
  below: boolean;
}

/**
 * Where the panel goes, given where the trigger is.
 *
 * It opens downwards unless there is not enough room and there is more of it
 * above, which is the case that matters: the profile selector lives in the
 * header, but the filters of the risk screen can end up near the fold.
 *
 * @param trigger - The trigger's rectangle, in viewport coordinates.
 * @param viewport - Width and height of the viewport.
 * @param optionCount - How many rows the panel will hold.
 * @return The fixed-position box for the panel.
 */
export function placePanel(
  trigger: { top: number; bottom: number; left: number; width: number },
  viewport: { width: number; height: number },
  optionCount: number,
): PanelPosition {
  const wanted = Math.min(MAX_HEIGHT, optionCount * OPTION_HEIGHT + PANEL_PADDING);
  const roomBelow = viewport.height - trigger.bottom - GAP - EDGE;
  const roomAbove = trigger.top - GAP - EDGE;

  const below = roomBelow >= wanted || roomBelow >= roomAbove;
  const maxHeight = Math.max(MIN_HEIGHT, Math.min(wanted, below ? roomBelow : roomAbove));

  // Clamped so a trigger near the right edge does not push the panel off screen.
  const width = trigger.width;
  const left = Math.max(EDGE, Math.min(trigger.left, viewport.width - width - EDGE));
  const top = below ? trigger.bottom + GAP : Math.max(EDGE, trigger.top - GAP - maxHeight);

  return { left, top, width, maxHeight, below };
}

/**
 * Where the highlight moves for a navigation key.
 *
 * @param current - Index highlighted now, or -1 when nothing is.
 * @param count - How many options there are.
 * @param key - The `event.key` that was pressed.
 * @return The new index, or null when the key is not one of these.
 */
export function nextActive(current: number, count: number, key: string): number | null {
  if (count === 0) return null;
  switch (key) {
    case "ArrowDown":
      return current < 0 ? 0 : Math.min(count - 1, current + 1);
    case "ArrowUp":
      return current < 0 ? count - 1 : Math.max(0, current - 1);
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

/**
 * Typeahead, with the behaviour a native `<select>` has.
 *
 * A **repeated single letter cycles** through the options starting with it, and
 * that is why the search starts one past the current row for a one-character
 * query and at the current row for a longer one: pressing `s`, `s`, `s` walks
 * the three options beginning with s, while typing `s`, `a`, `n` keeps refining
 * the same match instead of walking away from it.
 *
 * @param options - The options, searched by their text.
 * @param query - What has been typed so far within the timeout.
 * @param from - Index highlighted now, or -1.
 * @return The index of the first match, or -1 when nothing matches.
 */
export function findByPrefix(
  options: readonly SelectOption[],
  query: string,
  from: number,
): number {
  const needle = query.toLowerCase();
  if (!needle) return -1;

  const start = query.length > 1 ? Math.max(0, from) : from + 1;
  for (let step = 0; step < options.length; step++) {
    const index = (start + step + options.length) % options.length;
    const option = options[index];
    if (option && option[1].toLowerCase().startsWith(needle)) return index;
  }
  return -1;
}

/** How long a typeahead query stays alive between keystrokes, in milliseconds. */
const TYPEAHEAD_MS = 500;

/* -------------------------------------------------------------------------- */
/* The component                                                              */
/* -------------------------------------------------------------------------- */

interface Props {
  /** Label text, in the interface language. */
  label: string;
  /** The chosen value. */
  value: string;
  /** Options as `[value, text]` pairs, text already in the interface language. */
  options: readonly SelectOption[];
  /** Called with the chosen value — the value itself, not an event. */
  onChange: (value: string) => void;
  /** Label to the left instead of above. For the header, where there is no height to spend. */
  row?: boolean;
  /** What to show when the value matches no option. */
  placeholder?: string;
  /** The line under the field explaining what it decides. */
  hint?: React.ReactNode;
  disabled?: boolean;
  /** Classes for the wrapping block, not for the trigger. */
  fieldClass?: string;
  className?: string;
}

/**
 * The application's dropdown.
 *
 * @param props - Select props.
 * @param props.label - Label text, in the interface language.
 * @param props.value - The chosen value.
 * @param props.options - Options as `[value, text]` pairs.
 * @param props.onChange - Called with the chosen value.
 * @param props.row - Label to the left instead of above.
 * @param props.placeholder - Shown when the value matches no option.
 * @param props.hint - The line under the field.
 * @param props.disabled - Whether the control can be opened.
 * @param props.fieldClass - Classes for the wrapping block.
 * @param props.className - Classes for the trigger.
 * @return The rendered dropdown.
 */
export function Select({
  label,
  value,
  options,
  onChange,
  row = false,
  placeholder = "Elegir…",
  hint,
  disabled = false,
  fieldClass,
  className,
}: Props) {
  const ids = useId();
  const labelId = `${ids}-label`;
  const triggerId = `${ids}-trigger`;
  const listId = `${ids}-list`;

  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [position, setPosition] = useState<PanelPosition | null>(null);

  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const typed = useRef({ text: "", at: 0 });

  const selectedIndex = options.findIndex(([optionValue]) => optionValue === value);
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined;

  /**
   * Measures the trigger and works out where the panel goes.
   *
   * @return The position, also stored in state.
   */
  function reposition() {
    const node = triggerRef.current;
    if (!node) return null;
    const next = placePanel(
      node.getBoundingClientRect(),
      { width: window.innerWidth, height: window.innerHeight },
      options.length,
    );
    setPosition(next);
    return next;
  }

  function openPanel(startAt = selectedIndex) {
    if (disabled) return;
    reposition();
    setActive(startAt < 0 ? 0 : startAt);
    setOpen(true);
  }

  function closePanel() {
    setOpen(false);
    setPosition(null);
  }

  function choose(index: number) {
    const option = options[index];
    if (option) onChange(option[0]);
    closePanel();
    triggerRef.current?.focus();
  }

  // A pointer down anywhere that is neither the panel nor the trigger closes it.
  // `pointerdown` and not `click`: a click on a scrollbar or a drag that starts
  // outside never produces a click, and the panel would stay open behind it.
  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) {
        return;
      }
      closePanel();
    }

    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  // While it is open the page can still scroll under it —the panel is fixed and
  // the trigger is not— so it follows rather than detaching. `capture` because
  // the scroll usually happens in a card's own scroller, not on the window.
  useEffect(() => {
    if (!open) return;

    const follow = () => reposition();
    window.addEventListener("scroll", follow, true);
    window.addEventListener("resize", follow);
    return () => {
      window.removeEventListener("scroll", follow, true);
      window.removeEventListener("resize", follow);
    };
  });

  // Keeps the highlighted row inside the scrolled panel. Indexing `children`
  // instead of querying by id on purpose: `useId` produces ids with colons in
  // them, which are valid attributes but need escaping in a selector.
  useEffect(() => {
    if (!open || active < 0) return;
    const row = panelRef.current?.children[active];
    row?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  function onKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    const { key } = event;

    if (key === "Escape") {
      if (open) {
        event.preventDefault();
        closePanel();
      }
      return;
    }

    if (key === "Tab") {
      if (open) closePanel();
      return;
    }

    if (!open) {
      if (key === "ArrowDown" || key === "ArrowUp" || key === "Enter" || key === " ") {
        event.preventDefault();
        openPanel();
        return;
      }
    } else {
      if (key === "Enter" || (key === " " && !typed.current.text)) {
        event.preventDefault();
        if (active >= 0) choose(active);
        return;
      }

      const moved = nextActive(active, options.length, key);
      if (moved !== null) {
        event.preventDefault();
        setActive(moved);
        return;
      }
    }

    // Typeahead. A single printable character, and none of the modifier
    // combinations, which belong to the browser.
    if (key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      const now = Date.now();
      typed.current = {
        text: now - typed.current.at > TYPEAHEAD_MS ? key : typed.current.text + key,
        at: now,
      };
      const found = findByPrefix(options, typed.current.text, open ? active : selectedIndex);
      if (found < 0) return;
      if (open) setActive(found);
      else choose(found);
    }
  }

  const trigger = (
    <button
      type="button"
      ref={triggerRef}
      id={triggerId}
      role="combobox"
      aria-haspopup="listbox"
      aria-expanded={open}
      aria-controls={open ? listId : undefined}
      // Both ids: the label says what the field is and the trigger's own text
      // says what it currently holds, which is how a native select reads.
      aria-labelledby={`${labelId} ${triggerId}`}
      aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
      disabled={disabled}
      onClick={() => (open ? closePanel() : openPanel())}
      onKeyDown={onKeyDown}
      className={cn(
        CONTROL_CLASSES,
        "flex items-center justify-between gap-2 text-left",
        open && "border-primary ring-[3px] ring-primary/10",
        className,
      )}
    >
      <span className={cn("truncate", !selected && "text-text-muted")}>
        {selected ? selected[1] : placeholder}
      </span>
      <ChevronDown
        className={cn(
          "size-4 shrink-0 text-text-muted transition-transform duration-150",
          open && "rotate-180",
        )}
        aria-hidden
      />
    </button>
  );

  return (
    <div className={cn(row ? "flex items-center gap-3" : "flex flex-col gap-1.5", fieldClass)}>
      <span
        id={labelId}
        className="text-body-sm font-medium whitespace-nowrap text-foreground"
      >
        {label}
      </span>
      {trigger}
      {hint && <FieldHint>{hint}</FieldHint>}

      {open &&
        position &&
        createPortal(
          <div
            ref={panelRef}
            id={listId}
            role="listbox"
            aria-labelledby={labelId}
            style={{
              left: position.left,
              top: position.top,
              minWidth: position.width,
              maxHeight: position.maxHeight,
            }}
            className={cn(
              "appear fixed z-50 overflow-y-auto rounded-lg border border-border bg-popover p-1 shadow-xl",
              position.below ? "appear-down" : "appear-up",
            )}
          >
            {options.map(([optionValue, text], index) => (
              <div
                key={optionValue}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={optionValue === value}
                onMouseEnter={() => setActive(index)}
                // Keeps focus on the trigger, so the panel never has to trap it
                // and `aria-activedescendant` stays the single source of truth.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(index)}
                className={cn(
                  "flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-body-sm",
                  index === active ? "bg-surface-sunken" : "bg-transparent",
                  optionValue === value && "font-medium",
                )}
              >
                <Check
                  className={cn(
                    "size-4 shrink-0 text-accent",
                    optionValue === value ? "opacity-100" : "opacity-0",
                  )}
                  aria-hidden
                />
                <span className="truncate">{text}</span>
              </div>
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}
