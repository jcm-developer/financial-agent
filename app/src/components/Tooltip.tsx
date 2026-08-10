import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

/**
 * Verdana's tooltip: navy ground, near-white 12 px text, a 6 px arrow, 240 px of
 * width at most, and the asymmetric timing the system asks for — 150 ms to
 * appear, nothing at all to leave.
 *
 * ⚠️ **A tooltip is never the only place a meaning lives.** This project's rule
 * is that colour never carries meaning on its own, and the sentence that backs a
 * coloured chip has to reach someone who is not pointing at anything: that is
 * why the bubble is tied to its trigger with `aria-describedby` and not merely
 * painted next to it, and why the trigger keeps `tabIndex={0}` so a keyboard can
 * reach the explanation at all. It replaces the browser's `title`, which Verdana
 * cannot style, and it has to replace all of it — including the part `title`
 * did for people who never see it.
 */

/** Gap between trigger and bubble, the arrow's half-width, and the viewport margin. */
const GAP = 8;
const ARROW = 6;
const EDGE = 8;
const MAX_WIDTH = 240;

export interface TooltipPosition {
  left: number;
  top: number;
  /** Whether the bubble sits above the trigger. Decides which side the arrow is on. */
  above: boolean;
  /** Where the arrow goes along the bubble's width, so it still points at the trigger. */
  arrowLeft: number;
}

/**
 * Where the bubble goes, given where the trigger is.
 *
 * It prefers to sit above —that is where a tooltip is looked for— and flips
 * below only when there is no room. Horizontally it is centred on the trigger
 * and then clamped to the viewport, with the arrow moved back by however much
 * the clamp shifted it: otherwise a tooltip near the right edge points at
 * nothing.
 *
 * @param trigger - The trigger's rectangle, in viewport coordinates.
 * @param viewport - Width and height of the viewport.
 * @param bubble - Measured width and height of the bubble.
 * @return The fixed-position box for the bubble, and its arrow offset.
 */
export function placeTooltip(
  trigger: { top: number; bottom: number; left: number; width: number },
  viewport: { width: number; height: number },
  bubble: { width: number; height: number },
): TooltipPosition {
  const above = trigger.top - bubble.height - GAP - EDGE >= 0;
  const top = above ? trigger.top - bubble.height - GAP : trigger.bottom + GAP;

  const centred = trigger.left + trigger.width / 2 - bubble.width / 2;
  const left = Math.max(EDGE, Math.min(centred, viewport.width - bubble.width - EDGE));

  // The arrow tracks the trigger's centre, not the bubble's, and is kept far
  // enough from the corners that it never pokes out of the rounded edge.
  const centre = trigger.left + trigger.width / 2 - left;
  const arrowLeft = Math.max(ARROW * 2, Math.min(centre, bubble.width - ARROW * 2));

  return { left, top, above, arrowLeft };
}

/** How long the pointer has to rest before the bubble appears, in milliseconds. */
const SHOW_DELAY = 150;

interface Props {
  /** The sentence. In the interface language: it is read out loud. */
  content: ReactNode;
  /** What the tooltip explains. */
  children: ReactNode;
  className?: string;
}

/**
 * A tooltip anchored to whatever it wraps.
 *
 * @param props - Tooltip props.
 * @param props.content - The sentence, in the interface language.
 * @param props.children - What the tooltip explains.
 * @param props.className - Extra classes for the wrapper.
 * @return The wrapped children, plus the bubble while it is showing.
 */
export function Tooltip({ content, children, className }: Props) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<TooltipPosition | null>(null);

  const triggerRef = useRef<HTMLSpanElement>(null);
  const bubbleRef = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // 150 ms to show and 0 to hide, which is the whole point of the timer: without
  // the delay, dragging the pointer across a row of chips flashes four bubbles.
  function show() {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(true), SHOW_DELAY);
  }

  function hide() {
    clearTimeout(timer.current);
    setOpen(false);
    setPosition(null);
  }

  useEffect(() => () => clearTimeout(timer.current), []);

  // Measured after the bubble has rendered, because where it goes depends on how
  // big it turned out to be. It paints invisible on the first frame and lands on
  // the second, which is why `position` gates the opacity.
  useEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    const bubble = bubbleRef.current;
    if (!trigger || !bubble) return;

    setPosition(
      placeTooltip(
        trigger.getBoundingClientRect(),
        { width: window.innerWidth, height: window.innerHeight },
        { width: bubble.offsetWidth, height: bubble.offsetHeight },
      ),
    );
  }, [open, content]);

  // Esc dismisses it, which is what the keyboard needs once focus has summoned
  // one: without it the only way out is to move focus somewhere else.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") hide();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  return (
    <>
      <span
        ref={triggerRef}
        tabIndex={0}
        aria-describedby={open ? id : undefined}
        onPointerEnter={show}
        onPointerLeave={hide}
        onFocus={show}
        onBlur={hide}
        className={cn("inline-flex rounded-sm", className)}
      >
        {children}
      </span>

      {open &&
        createPortal(
          <div
            ref={bubbleRef}
            id={id}
            role="tooltip"
            style={{
              left: position?.left ?? 0,
              top: position?.top ?? 0,
              maxWidth: MAX_WIDTH,
            }}
            className={cn(
              "pointer-events-none fixed z-50 rounded-md bg-primary px-3 py-1.5",
              "text-caption font-normal text-background shadow-lg",
              position ? "opacity-100" : "opacity-0",
            )}
          >
            {content}
            {position && (
              <span
                aria-hidden
                style={{
                  left: position.arrowLeft - ARROW,
                  [position.above ? "bottom" : "top"]: -ARROW / 2,
                }}
                className="absolute size-[6px] rotate-45 bg-primary"
              />
            )}
          </div>,
          document.body,
        )}
    </>
  );
}
