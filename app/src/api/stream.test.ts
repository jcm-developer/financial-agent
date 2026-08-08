import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { keys } from "@/api/keys";
import { antiguedadReal, aplicarEvento, fundirCiclo, fundirIngest } from "@/api/stream";
import type { CycleControl, IngestStatus } from "@/api/types";

/**
 * El empalme de los eventos del stream, que es la parte que no se valida a ojo.
 *
 * Los tres casos que importan salen de la forma real de los eventos de F3.5: el
 * `ingest` manda un subconjunto de campos, el `cycle` manda el buffer completo la
 * primera vez y solo lo nuevo despues, y entre medias puede perderse un trozo.
 */

const SALUD: IngestStatus = {
  healthy: true,
  message: "Al dia.",
  last_tick_at: "2026-08-08T15:30:00+00:00",
  seconds_since_last_tick: 12,
  consecutive_failures: 0,
  rate_limited_recently: false,
  avg_latency_ms: 850,
  symbols_tracked: 89,
  symbols_by_market: { eu: 89 },
  bars_stored: 45_400,
  quotes_stored: 89,
  last_backfill_at: null,
  recent: [],
};

describe("fundirIngest", () => {
  it("conserva los campos que el evento no manda", () => {
    // El evento trae 5 de los 14 campos. Reemplazar en vez de fundir dejaria el
    // panel de salud a medias justo cuando el ingestor cambia de veredicto.
    const fundido = fundirIngest(SALUD, {
      healthy: false,
      message: "3 fallos seguidos.",
      last_tick_at: "2026-08-08T15:32:00+00:00",
      seconds_since_last_tick: 180,
      consecutive_failures: 3,
    });

    expect(fundido?.healthy).toBe(false);
    expect(fundido?.consecutive_failures).toBe(3);
    // Lo que el evento no trae sigue ahi.
    expect(fundido?.avg_latency_ms).toBe(850);
    expect(fundido?.symbols_tracked).toBe(89);
    expect(fundido?.bars_stored).toBe(45_400);
  });

  it("no inventa nada si no habia cache", () => {
    // Rellenar los otros nueve campos con ceros seria afirmar cosas que no
    // sabemos: "0 barras almacenadas" se leeria como un ingestor sin datos.
    expect(
      fundirIngest(undefined, {
        healthy: true,
        message: "Al dia.",
        last_tick_at: null,
        seconds_since_last_tick: null,
        consecutive_failures: 0,
      }),
    ).toBeUndefined();
  });
});

describe("fundirCiclo", () => {
  const base: CycleControl = { enabled: true, running: true, stage: "analizando" };

  it("el primer evento reemplaza: trae el buffer entero", () => {
    const { estado, hayHueco } = fundirCiclo(undefined, {
      ...base,
      lines: ["uno", "dos"],
    });

    expect(estado.lines).toEqual(["uno", "dos"]);
    expect(hayHueco).toBe(false);
  });

  it("los incrementales se empalman en `from`", () => {
    const previo: CycleControl = { ...base, lines: ["uno", "dos"] };
    const { estado, hayHueco } = fundirCiclo(previo, {
      ...base,
      lines: ["tres"],
      from: 2,
    });

    expect(estado.lines).toEqual(["uno", "dos", "tres"]);
    expect(hayHueco).toBe(false);
  });

  it("un `from` por detras reescribe, no duplica", () => {
    // Puede pasar tras una reconexion: el servidor reenvia algo que ya teniamos.
    const previo: CycleControl = { ...base, lines: ["uno", "dos", "tres"] };
    const { estado } = fundirCiclo(previo, { ...base, lines: ["DOS", "TRES"], from: 1 });

    expect(estado.lines).toEqual(["uno", "DOS", "TRES"]);
  });

  it("un hueco se declara en vez de disimularse", () => {
    // Tenemos 2 lineas y el servidor manda desde la 5: faltan tres. Empalmar
    // sin mas dejaria un log que se lee como continuo sin serlo.
    const previo: CycleControl = { ...base, lines: ["uno", "dos"] };
    const { hayHueco } = fundirCiclo(previo, { ...base, lines: ["seis"], from: 5 });

    expect(hayHueco).toBe(true);
  });
});

describe("aplicarEvento", () => {
  it("escribe las cotizaciones en la clave que lee useQuotes", () => {
    // Si la clave no coincidiera, el stream actualizaria una entrada y la
    // pantalla leeria otra: los precios no se moverian y no habria ningun error.
    const cliente = new QueryClient();
    const clave = keys.quotes(undefined);

    aplicarEvento(
      cliente,
      "quotes",
      {
        mark: "2026-08-08T15:31:00+00:00",
        quotes: [
          {
            symbol: "SAN.MC",
            price: 4.8,
            as_of: "2026-08-08T15:30:00+00:00",
            updated_at: "2026-08-08T15:31:00+00:00",
            age_seconds: 60,
          },
        ],
      },
      clave,
    );

    expect(cliente.getQueryData(clave)).toHaveLength(1);
  });

  it("un evento desconocido no toca nada", () => {
    const cliente = new QueryClient();
    aplicarEvento(cliente, "inventado", { lo: "que sea" }, keys.quotes(undefined));
    expect(cliente.getQueryCache().getAll()).toHaveLength(0);
  });
});

describe("antiguedadReal", () => {
  it("suma lo que ha pasado desde que llego el evento", () => {
    // `age_seconds` lo calcula el servidor al leer, asi que se congela en cache:
    // enseñarlo tal cual diria "hace 60 s" durante media hora.
    const recibidas = 1_000_000;
    const edad = antiguedadReal({ age_seconds: 60 }, recibidas, recibidas + 90_000);

    expect(edad).toBe(150);
  });

  it("sin evento previo devuelve lo que dijo el servidor", () => {
    expect(antiguedadReal({ age_seconds: 42 }, null)).toBe(42);
  });

  it("un precio sin antiguedad no se convierte en cero", () => {
    // Un 0 se leeria como "recien llegado", que es lo contrario de "no se sabe".
    expect(antiguedadReal({ age_seconds: null }, 1_000)).toBeNull();
  });
});
