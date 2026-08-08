import { useIngestStatus, useMarkets, useQuotes } from "@/api/hooks";
import { antiguedadReal, useStream } from "@/api/stream";
import { IndicadorEnVivo } from "@/components/IndicadorEnVivo";
import type { MarketInfo, QuoteRow } from "@/api/types";

/**
 * Comprobacion de que la capa de datos esta viva (tramos A y B de F4).
 *
 * Sigue siendo un cartel de "estoy vivo" y no una pantalla: las de verdad llegan
 * en el tramo D y este componente desaparece entonces. Lo que demuestra ahora es
 * la cadena completa —tipos generados, cliente, TanStack Query y el SSE
 * escribiendo en la cache— sobre los tres endpoints que no necesitan un perfil.
 */
export function App() {
  const mercados = useMarkets();
  const cotizaciones = useQuotes();
  const ingesta = useIngestStatus();
  const stream = useStream();

  return (
    <div className="mx-auto max-w-5xl px-5 pt-6 pb-16">
      <header className="mb-6 flex flex-wrap items-baseline justify-between gap-4 border-b border-border pb-5">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">financial-bot</h1>
          <p className="mt-1 text-[13px] text-text-secondary">
            Capa de datos de F4 en pie. Las pantallas llegan en el tramo D.
          </p>
        </div>
        <IndicadorEnVivo
          estado={stream.estado}
          reconexiones={stream.reconexiones}
          aviso={stream.ultimoAviso}
        />
      </header>

      <Seccion titulo="Mercados" consulta={mercados}>
        {(datos: MarketInfo[]) => (
          <div className="grid gap-4 sm:grid-cols-2">
            {datos.map((mercado) => (
              <TarjetaMercado key={mercado.code} mercado={mercado} />
            ))}
          </div>
        )}
      </Seccion>

      <Seccion titulo="Salud del ingestor" consulta={ingesta}>
        {(datos) => (
          <p className="text-[13px] text-text-secondary">
            <span className={datos.healthy ? "text-delta-good" : "text-delta-bad"}>
              {datos.healthy ? "Sano" : "Con problemas"}
            </span>
            {" — "}
            {datos.message} {datos.symbols_tracked ?? 0} símbolos seguidos,{" "}
            {/* Los campos con default en Pydantic salen opcionales en el tipo
                generado, así que hay que decidir qué significa que falten. Aquí
                un 0 es correcto: la API siempre los manda. */}
            <span className="tabular">
              {(datos.bars_stored ?? 0).toLocaleString("es-ES")}
            </span>{" "}
            barras guardadas.
          </p>
        )}
      </Seccion>

      <Seccion titulo="Cotizaciones" consulta={cotizaciones}>
        {(datos: QuoteRow[]) =>
          datos.length === 0 ? (
            <p className="text-[13px] text-text-muted">
              Todavía no hay cotizaciones: el ingestor las escribe en horario de mercado.
            </p>
          ) : (
            <table className="w-full text-[13px]">
              <thead className="text-left text-text-muted">
                <tr>
                  <th className="pb-1 font-medium">Símbolo</th>
                  <th className="pb-1 text-right font-medium">Precio</th>
                  <th className="pb-1 text-right font-medium">Var.</th>
                  <th className="pb-1 text-right font-medium">Antigüedad</th>
                </tr>
              </thead>
              <tbody>
                {datos.slice(0, 12).map((fila) => (
                  <FilaCotizacion
                    key={fila.symbol}
                    fila={fila}
                    recibidasEn={stream.quotesRecibidasEn}
                  />
                ))}
              </tbody>
            </table>
          )
        }
      </Seccion>
    </div>
  );
}

/**
 * Envoltorio con los tres estados de F4.8: cargando, error y vacío.
 *
 * Es un boceto de lo que el tramo D hará bien con shadcn. Está aquí porque la
 * alternativa —`datos?.map(...)` a secas— convierte un error de la API en una
 * sección en blanco, y una sección en blanco se lee como "no hay nada", que es
 * una afirmación distinta.
 */
function Seccion<T>({
  titulo,
  consulta,
  children,
}: {
  titulo: string;
  consulta: { data?: T; error: Error | null; isPending: boolean };
  children: (datos: T) => React.ReactNode;
}) {
  return (
    <section className="mb-8">
      <h2 className="mb-3 text-[13px] font-semibold tracking-wide text-text-secondary uppercase">
        {titulo}
      </h2>
      {consulta.isPending && <p className="text-[13px] text-text-muted">Cargando…</p>}
      {consulta.error && (
        <p className="rounded-md border border-negative/40 bg-card p-3 text-[13px] text-negative">
          {consulta.error.message}
          <br />
          <span className="text-text-muted">
            Con <code>npm run dev</code> hace falta la API escuchando:{" "}
            <code>python run.py api</code>
          </span>
        </p>
      )}
      {consulta.data !== undefined && children(consulta.data)}
    </section>
  );
}

function TarjetaMercado({ mercado }: { mercado: MarketInfo }) {
  return (
    <article className="rounded-lg border border-border bg-card p-4 shadow-[var(--shadow-card)]">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="font-semibold">{mercado.label}</h3>
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
        <dt className="text-text-muted">Sesión</dt>
        <dd className="tabular text-right">
          {mercado.session_open}–{mercado.session_close}
        </dd>
        <dt className="text-text-muted">Ventana</dt>
        <dd className="tabular text-right">
          {mercado.operating_open}–{mercado.operating_close}
        </dd>
        <dt className="text-text-muted">Universo</dt>
        <dd className="tabular text-right">{mercado.universe_size} valores</dd>
      </dl>
    </article>
  );
}

function FilaCotizacion({
  fila,
  recibidasEn,
}: {
  fila: QuoteRow;
  recibidasEn: number | null;
}) {
  const edad = antiguedadReal(fila, recibidasEn);
  const variacion = fila.change_pct;

  return (
    <tr className="border-t border-border">
      <td className="py-1">{fila.symbol}</td>
      <td className="tabular py-1 text-right">{fila.price.toFixed(2)}</td>
      <td
        className={
          variacion === null || variacion === undefined
            ? "tabular py-1 text-right text-text-muted"
            : variacion >= 0
              ? "tabular py-1 text-right text-delta-good"
              : "tabular py-1 text-right text-delta-bad"
        }
      >
        {variacion === null || variacion === undefined
          ? "—"
          : `${variacion >= 0 ? "+" : ""}${variacion.toFixed(2)}%`}
      </td>
      {/* La antigüedad es la medición de F2.1c puesta donde se ve todos los
          días: "cada minuto" solo vale si el dato es de hace un minuto. */}
      <td
        className={
          edad !== null && edad > 300
            ? "tabular py-1 text-right text-warning"
            : "tabular py-1 text-right text-text-muted"
        }
      >
        {edad === null ? "n/d" : `${Math.round(edad)} s`}
      </td>
    </tr>
  );
}
