import { describe, expect, it } from "vitest";

import { findByPrefix, nextActive, placePanel, type SelectOption } from "./Select";

/**
 * The dropdown's three pure decisions, tested without a DOM.
 *
 * There is no jsdom in this project and adding one for four functions would be a
 * dependency tree to test arithmetic, so `Select.tsx` keeps the parts that can be
 * got wrong —where the panel goes, where the highlight moves, what the typing
 * matches— out of the component and takes them as inputs. What is left inside is
 * wiring, which a test of this shape would not have caught anyway.
 */

const VIEWPORT = { width: 1280, height: 800 };

/**
 * A trigger rectangle, given where its top edge is.
 *
 * @param top - Distance from the top of the viewport.
 * @param width - Width of the trigger.
 * @return The rectangle, 42 px tall like every control in the system.
 */
function trigger(top: number, width = 200) {
  return { top, bottom: top + 42, left: 100, width };
}

describe("placePanel", () => {
  it("abre hacia abajo cuando hay sitio de sobra", () => {
    const position = placePanel(trigger(100), VIEWPORT, 4);

    expect(position.below).toBe(true);
    expect(position.top).toBe(148); // 100 + 42 + 6 de separación
  });

  it("se da la vuelta cuando abajo no cabe y arriba sí", () => {
    // El disparador está a 40 px del fondo: por debajo no queda nada.
    const position = placePanel(trigger(718), VIEWPORT, 6);

    expect(position.below).toBe(false);
    expect(position.top + position.maxHeight).toBeLessThanOrEqual(718 - 6);
  });

  it("no pasa del alto máximo por muchas opciones que haya", () => {
    const position = placePanel(trigger(20), VIEWPORT, 200);

    expect(position.maxHeight).toBeLessThanOrEqual(320);
  });

  it("no se sale por la derecha", () => {
    const wide = { top: 100, bottom: 142, left: 1200, width: 300 };
    const position = placePanel(wide, VIEWPORT, 3);

    expect(position.left + position.width).toBeLessThanOrEqual(VIEWPORT.width);
    expect(position.left).toBeGreaterThanOrEqual(0);
  });

  it("hereda el ancho del disparador, que es lo que lo alinea con el campo", () => {
    expect(placePanel(trigger(100, 240), VIEWPORT, 3).width).toBe(240);
  });
});

describe("nextActive", () => {
  it("baja y sube sin salirse de los extremos", () => {
    expect(nextActive(0, 3, "ArrowDown")).toBe(1);
    expect(nextActive(2, 3, "ArrowDown")).toBe(2);
    expect(nextActive(1, 3, "ArrowUp")).toBe(0);
    expect(nextActive(0, 3, "ArrowUp")).toBe(0);
  });

  it("entra por el extremo que toca cuando no hay nada resaltado", () => {
    expect(nextActive(-1, 3, "ArrowDown")).toBe(0);
    expect(nextActive(-1, 3, "ArrowUp")).toBe(2);
  });

  it("Home y End van a los extremos", () => {
    expect(nextActive(1, 5, "Home")).toBe(0);
    expect(nextActive(1, 5, "End")).toBe(4);
  });

  it("devuelve null para una tecla que no navega, para no tragársela", () => {
    expect(nextActive(1, 5, "a")).toBeNull();
    expect(nextActive(1, 5, "Enter")).toBeNull();
  });

  it("no propone nada sobre una lista vacía", () => {
    expect(nextActive(-1, 0, "ArrowDown")).toBeNull();
  });
});

describe("findByPrefix", () => {
  const OPTIONS: SelectOption[] = [
    ["sc", "Santander"],
    ["sn", "Sanofi"],
    ["ib", "Iberdrola"],
    ["se", "Sabadell"],
  ];

  it("encuentra por prefijo sin distinguir mayúsculas", () => {
    expect(findByPrefix(OPTIONS, "ib", -1)).toBe(2);
    expect(findByPrefix(OPTIONS, "IBER", -1)).toBe(2);
  });

  it("una letra repetida recorre las que empiezan por ella", () => {
    // Es el comportamiento del <select> nativo: s, s, s pasea por las tres.
    expect(findByPrefix(OPTIONS, "s", -1)).toBe(0);
    expect(findByPrefix(OPTIONS, "s", 0)).toBe(1);
    expect(findByPrefix(OPTIONS, "s", 1)).toBe(3);
    expect(findByPrefix(OPTIONS, "s", 3)).toBe(0); // da la vuelta
  });

  it("una consulta de varias letras afina sobre la misma fila, no salta a la siguiente", () => {
    // Escribiendo «san» estando ya en Santander se queda en Santander.
    expect(findByPrefix(OPTIONS, "san", 0)).toBe(0);
  });

  it("devuelve -1 cuando no hay coincidencia, y no mueve el resaltado", () => {
    expect(findByPrefix(OPTIONS, "zz", 0)).toBe(-1);
  });

  it("una consulta vacía no coincide con todo", () => {
    expect(findByPrefix(OPTIONS, "", 0)).toBe(-1);
  });
});
