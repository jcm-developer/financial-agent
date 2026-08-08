import { useEffect, useState } from "react";

import type { MarketInfo } from "@/api/types";

/**
 * Comprobacion de que el andamiaje esta vivo (tramo A de F4).
 *
 * Pinta `/api/markets` a proposito, y no una pantalla de verdad: ese endpoint
 * es el que demuestra las cuatro cosas que este tramo tenia que dejar
 * funcionando —React compila, Tailwind aplica la paleta, el proxy de `vite dev`
 * alcanza la API y los tipos generados encajan con lo que responde el
 * servidor—. Las pantallas llegan en el tramo D, y este componente desaparece
 * entonces.
 *
 * El `fetch` es a pelo: la capa de datos con TanStack Query es el tramo B, y
 * meterla aqui haria que un fallo del andamiaje se pareciera a un fallo de la
 * capa de datos.
 */
export function App() {
  const [mercados, setMercados] = useState<MarketInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const corte = new AbortController();
    fetch("/api/markets", { signal: corte.signal })
      .then(async (respuesta) => {
        if (!respuesta.ok) {
          throw new Error(`La API respondio ${respuesta.status}.`);
        }
        return (await respuesta.json()) as MarketInfo[];
      })
      .then(setMercados)
      .catch((causa: unknown) => {
        if (causa instanceof DOMException && causa.name === "AbortError") return;
        setError(causa instanceof Error ? causa.message : String(causa));
      });
    return () => corte.abort();
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-5 pt-6 pb-16">
      <header className="mb-6 flex flex-wrap items-baseline justify-between gap-4 border-b border-border pb-5">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">financial-bot</h1>
          <p className="mt-1 text-[13px] text-text-secondary">
            Andamiaje de F4 en pie. Las pantallas llegan en el tramo D.
          </p>
        </div>
        <span className="rounded-full border border-border bg-card px-[9px] py-0.5 text-xs text-text-secondary">
          {mercados ? `${mercados.length} mercados` : error ? "sin API" : "cargando…"}
        </span>
      </header>

      {error && (
        <p className="rounded-md border border-negative/40 bg-card p-4 text-[13px] text-negative">
          No se pudo hablar con la API: {error}
          <br />
          <span className="text-text-muted">
            Con <code>vite dev</code> hace falta la API escuchando en el 8000:{" "}
            <code>python run.py api</code>
          </span>
        </p>
      )}

      {!error && !mercados && (
        <p className="text-[13px] text-text-muted">Consultando /api/markets…</p>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        {mercados?.map((mercado) => (
          <article
            key={mercado.code}
            className="rounded-lg border border-border bg-card p-4 shadow-[var(--shadow-card)]"
          >
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="font-semibold">{mercado.label}</h2>
              <span
                className={
                  mercado.is_operating
                    ? "text-xs font-semibold text-delta-good"
                    : "text-xs text-text-muted"
                }
              >
                {mercado.is_operating ? "en ventana" : "fuera de ventana"}
              </span>
            </div>
            <p className="mt-1 text-[13px] text-text-secondary">{mercado.status_text}</p>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[13px]">
              <dt className="text-text-muted">Sesion</dt>
              <dd className="tabular text-right">
                {mercado.session_open}–{mercado.session_close}
              </dd>
              <dt className="text-text-muted">Ventana</dt>
              <dd className="tabular text-right">
                {mercado.operating_open}–{mercado.operating_close}
              </dd>
              <dt className="text-text-muted">Universo</dt>
              <dd className="tabular text-right">{mercado.universe_size} valores</dd>
              <dt className="text-text-muted">Liquidez min.</dt>
              <dd className="tabular text-right">
                {mercado.min_turnover.toLocaleString("es-ES")} {mercado.currency}
              </dd>
            </dl>
          </article>
        ))}
      </div>
    </div>
  );
}
