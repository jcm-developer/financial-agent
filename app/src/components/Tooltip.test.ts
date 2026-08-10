import { describe, expect, it } from "vitest";

import { placeTooltip } from "./Tooltip";

/**
 * Where the bubble lands, tested without a DOM for the same reason the
 * dropdown's placement is: it is arithmetic, and arithmetic is what gets it
 * wrong near the edges of the screen.
 */

const VIEWPORT = { width: 1280, height: 800 };
const BUBBLE = { width: 200, height: 40 };

/**
 * A trigger rectangle, given where its top-left corner is.
 *
 * @param top - Distance from the top of the viewport.
 * @param left - Distance from the left.
 * @param width - Width of the trigger.
 * @return The rectangle, 20 px tall like a chip.
 */
function trigger(top: number, left = 500, width = 80) {
  return { top, bottom: top + 20, left, width };
}

describe("placeTooltip", () => {
  it("se coloca encima cuando hay sitio, que es donde se busca un tooltip", () => {
    const position = placeTooltip(trigger(300), VIEWPORT, BUBBLE);

    expect(position.above).toBe(true);
    expect(position.top).toBe(300 - 40 - 8);
  });

  it("baja cuando arriba no cabe", () => {
    const position = placeTooltip(trigger(10), VIEWPORT, BUBBLE);

    expect(position.above).toBe(false);
    expect(position.top).toBe(30 + 8);
  });

  it("se centra sobre el disparador", () => {
    const position = placeTooltip(trigger(300, 500, 80), VIEWPORT, BUBBLE);

    // Centro del disparador 540, mitad del globo 100.
    expect(position.left).toBe(440);
  });

  it("no se sale por la derecha, y la flecha se queda apuntando al disparador", () => {
    const position = placeTooltip(trigger(300, 1240, 30), VIEWPORT, BUBBLE);

    expect(position.left + BUBBLE.width).toBeLessThanOrEqual(VIEWPORT.width);
    // La flecha ya no está en el centro del globo: sigue al disparador.
    expect(position.arrowLeft).toBeGreaterThan(BUBBLE.width / 2);
  });

  it("no se sale por la izquierda", () => {
    const position = placeTooltip(trigger(300, 4, 30), VIEWPORT, BUBBLE);

    expect(position.left).toBeGreaterThanOrEqual(0);
  });

  it("la flecha nunca se asoma por la esquina redondeada", () => {
    const position = placeTooltip(trigger(300, 0, 10), VIEWPORT, BUBBLE);

    expect(position.arrowLeft).toBeGreaterThanOrEqual(12);
    expect(position.arrowLeft).toBeLessThanOrEqual(BUBBLE.width - 12);
  });
});
