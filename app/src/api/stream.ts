import { useEffect, useRef, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

import { keys } from "@/api/keys";
import type { CycleControl, IngestStatus, QuoteRow } from "@/api/types";

/**
 * El hook de tiempo real (F4.5), sobre el SSE de F3.5.
 *
 * **Escribe en la cache de TanStack Query, no en un estado propio.** Es la
 * decision de diseño del tramo B: con dos fuentes para el mismo precio —la
 * respuesta de `/api/quotes` y lo que llega por el stream— la pantalla acabaria
 * enseñando dos numeros distintos segun el componente, y no habria un sitio
 * donde arreglarlo. Asi `useQuotes()` es la unica lectura y el stream solo la
 * mantiene fresca.
 *
 * Y conviene recordar lo que dice F3.5 sin adornos: **por dentro esto sondea**.
 * El ingestor corre en otro proceso, asi que el servidor mira el fichero cada
 * dos segundos y manda solo lo que cambia. La ganancia es mover el sondeo del
 * navegador al servidor, no que haya empuje de verdad.
 */

export type EstadoStream = "conectando" | "vivo" | "desconectado";

/** Lo que manda el evento `ingest`: cinco de los trece campos de IngestStatus. */
type IngestEvento = Pick<
  IngestStatus,
  | "healthy"
  | "message"
  | "last_tick_at"
  | "seconds_since_last_tick"
  | "consecutive_failures"
>;

/** Lo que manda el evento `cycle`: el estado, y `from` solo en los incrementales. */
type CicloEvento = CycleControl & { from?: number };

interface QuotesEvento {
  quotes: QuoteRow[];
  mark: string;
}

/**
 * Funde el evento `ingest` con lo que ya hay en la cache.
 *
 * **No se puede reemplazar**: el evento trae cinco campos y `/api/ingest-status`
 * devuelve trece —comprobado contra el servidor, no supuesto—, asi que un
 * `setQueryData(evento)` borraria `avg_latency_ms`, `symbols_tracked`,
 * `bars_stored` y la lista de ticks recientes. El panel de salud se quedaria a
 * medias en cuanto el ingestor cambiara de veredicto, que es justo el momento en
 * que se mira. TypeScript no avisa porque el evento es un subconjunto valido.
 *
 * Si no hay nada en cache todavia se devuelve `undefined`: inventar los otros
 * ocho campos con ceros seria afirmar cosas que no sabemos. La consulta normal
 * los traera.
 */
export function fundirIngest(
  previo: IngestStatus | undefined,
  evento: IngestEvento,
): IngestStatus | undefined {
  if (!previo) return undefined;
  return { ...previo, ...evento };
}

/** Lo que hay que hacer con un evento `cycle` tras intentar fundirlo. */
export interface ResultadoCiclo {
  estado: CycleControl;
  /** True si se perdieron lineas y hay que releer del servidor. */
  hayHueco: boolean;
}

/**
 * Funde el evento `cycle`, que llega de dos formas distintas.
 *
 * El servidor manda el estado completo al abrir la conexion (con todas las
 * lineas del buffer y sin `from`) y luego solo las nuevas, con `from` diciendo
 * desde que indice van. Reenviar las 400 lineas cada dos segundos convertiria el
 * "en vivo" en un goteo de megabytes, asi que el cliente tiene que empalmar.
 *
 * El caso interesante es el hueco: si `from` es mayor que las lineas que
 * tenemos, nos hemos perdido algo por el camino. **No se rellena a ojo** —
 * empalmar dejando el hueco produciria un log que se lee como continuo sin
 * serlo, y eso es peor que no tenerlo — se pide releer al servidor, que tiene la
 * verdad.
 */
export function fundirCiclo(
  previo: CycleControl | undefined,
  evento: CicloEvento,
): ResultadoCiclo {
  const { from, ...estado } = evento;
  const nuevas = evento.lines ?? [];

  if (from === undefined) {
    // Estado inicial (o reconexion): el evento ya trae el buffer entero.
    return { estado, hayHueco: false };
  }

  const anteriores = previo?.lines ?? [];
  if (from > anteriores.length) {
    return { estado: { ...estado, lines: [...anteriores, ...nuevas] }, hayHueco: true };
  }
  return {
    estado: { ...estado, lines: [...anteriores.slice(0, from), ...nuevas] },
    hayHueco: false,
  };
}

/** Aplica un evento del stream a la cache. Fuera del hook para poder probarla. */
export function aplicarEvento(
  cliente: QueryClient,
  nombre: string,
  datos: unknown,
  claveQuotes: readonly unknown[],
): void {
  switch (nombre) {
    case "quotes": {
      const evento = datos as QuotesEvento;
      cliente.setQueryData(claveQuotes, evento.quotes);
      break;
    }
    case "ingest": {
      cliente.setQueryData<IngestStatus | undefined>(keys.ingestStatus(), (previo) =>
        fundirIngest(previo, datos as IngestEvento),
      );
      break;
    }
    case "cycle": {
      const previo = cliente.getQueryData<CycleControl>(keys.cycleControl());
      const { estado, hayHueco } = fundirCiclo(previo, datos as CicloEvento);
      cliente.setQueryData(keys.cycleControl(), estado);
      if (hayHueco) {
        void cliente.invalidateQueries({ queryKey: keys.cycleControl() });
      }
      break;
    }
  }
}

interface OpcionesStream {
  symbols?: string[];
  enabled?: boolean;
}

export interface Stream {
  estado: EstadoStream;
  /** Cuantas veces se ha reconectado. Util para ver si la conexion baila. */
  reconexiones: number;
  /**
   * Cuando llego el ultimo lote de cotizaciones, en `Date.now()`.
   *
   * Existe para poder enseñar la antiguedad real de un precio. `age_seconds`
   * viene calculado por el servidor **en el momento de leer**, asi que en cuanto
   * se guarda en cache se congela: enseñarlo tal cual diria "hace 4 s" durante
   * media hora. Sumando lo que ha pasado desde que llego el evento se corrige, y
   * ademas sin depender del reloj del navegador —que puede ir desviado del del
   * servidor—, porque solo se usa una diferencia local.
   */
  quotesRecibidasEn: number | null;
  /** Motivo del ultimo corte, si el servidor lo dijo. */
  ultimoAviso: string | null;
}

export function useStream({ symbols, enabled = true }: OpcionesStream = {}): Stream {
  const cliente = useQueryClient();
  const lista = symbols?.length ? symbols.join(",") : undefined;

  const [estado, setEstado] = useState<EstadoStream>("conectando");
  const [reconexiones, setReconexiones] = useState(0);
  const [quotesRecibidasEn, setQuotesRecibidasEn] = useState<number | null>(null);
  const [ultimoAviso, setUltimoAviso] = useState<string | null>(null);
  // Para no contar como reconexion la primera conexion de todas.
  const abiertoAlgunaVez = useRef(false);

  useEffect(() => {
    if (!enabled) {
      setEstado("desconectado");
      return;
    }

    const url = lista
      ? `/api/stream?symbols=${encodeURIComponent(lista)}`
      : "/api/stream";
    const fuente = new EventSource(url);
    const claveQuotes = keys.quotes(lista);

    const alRecibir = (nombre: string) => (evento: MessageEvent<string>) => {
      let datos: unknown;
      try {
        datos = JSON.parse(evento.data);
      } catch {
        return; // Un evento ilegible se ignora; el siguiente traera el estado.
      }
      if (nombre === "quotes") setQuotesRecibidasEn(Date.now());
      if (nombre === "bye" || nombre === "error") {
        const motivo = (datos as { reason?: string; message?: string });
        setUltimoAviso(motivo.message ?? motivo.reason ?? null);
        return;
      }
      aplicarEvento(cliente, nombre, datos, claveQuotes);
    };

    const suscripciones = ["quotes", "ingest", "cycle", "bye", "error"] as const;
    const oyentes = suscripciones.map((nombre) => {
      const oyente = alRecibir(nombre);
      fuente.addEventListener(nombre, oyente as EventListener);
      return [nombre, oyente] as const;
    });

    fuente.onopen = () => {
      setEstado("vivo");
      if (abiertoAlgunaVez.current) setReconexiones((n) => n + 1);
      abiertoAlgunaVez.current = true;
      // Al reconectar, lo que haya en cache puede ser viejo: el servidor manda
      // el estado completo, pero las tablas paginadas no van por el stream.
      void cliente.invalidateQueries({ queryKey: keys.ingestStatus() });
    };

    fuente.onerror = () => {
      // `EventSource` reconecta solo —es la razon de elegir SSE en D6— asi que
      // esto no siempre es un fallo: tambien salta cuando el servidor retira la
      // conexion por edad (F3.5, 15 min). El estado sale de `readyState`, que es
      // lo unico que distingue "reintentando" de "cerrada para siempre".
      setEstado(fuente.readyState === EventSource.CLOSED ? "desconectado" : "conectando");
    };

    return () => {
      for (const [nombre, oyente] of oyentes) {
        fuente.removeEventListener(nombre, oyente as EventListener);
      }
      fuente.close();
    };
    // `cliente` es estable (viene del provider) pero se declara por honestidad.
  }, [cliente, lista, enabled]);

  return { estado, reconexiones, quotesRecibidasEn, ultimoAviso };
}

/**
 * Antiguedad real de un precio, en segundos.
 *
 * `age_seconds` del servidor mas lo que ha pasado desde que llego el evento. Ver
 * la nota de `quotesRecibidasEn` sobre por que no se recalcula desde
 * `updated_at` con el reloj del navegador.
 */
export function antiguedadReal(
  fila: Pick<QuoteRow, "age_seconds">,
  recibidasEn: number | null,
  ahora: number = Date.now(),
): number | null {
  if (fila.age_seconds === null || fila.age_seconds === undefined) return null;
  if (recibidasEn === null) return fila.age_seconds;
  return fila.age_seconds + Math.max(0, (ahora - recibidasEn) / 1000);
}
