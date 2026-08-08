import { useIngestStatus, useMarkets, useQuotes } from "@/api/hooks";
import { antiguedadReal, useQuotesRecibidasEn } from "@/api/stream";
import type { MarketInfo, QuoteRow } from "@/api/types";
import { Seccion } from "@/components/Seccion";

/**
 * Estado de la ingesta y de las bolsas.
 *
 * Era la página de comprobación de los tramos A y B, y **se queda como pantalla
 * de verdad** en vez de borrarse: el dashboard viejo es anterior al ingestor, así
 * que la salud de la ingesta y la antigüedad de los precios no se ven hoy en
 * ningún sitio. Y son justo los dos números que hay que vigilar las dos primeras
 * semanas — «cada minuto» solo vale si el dato es de hace un minuto (F2.1c).
 *
 * No depende de ningún perfil: es infraestructura, no experimento.
 */
export function Diagnostico() {
  const mercados = useMarkets();
  const ingesta = useIngestStatus();
  const cotizaciones = useQuotes();
  // Solo la marca de llegada, para corregir la antigüedad. La conexión SSE la
  // abre el Layout una sola vez: pedirla aquí abriría una segunda.
  const quotesRecibidasEn = useQuotesRecibidasEn();

  return (
    <>
      <h1 className="mb-5 text-[17px] font-semibold tracking-tight">Ingesta y mercados</h1>

      <Seccion titulo="Salud del ingestor" consulta={ingesta}>
        {(datos) => (
          <div className="rounded-lg border border-border bg-card p-4">
            <p className="text-[13px]">
              <span
                className={
                  datos.healthy
                    ? "font-semibold text-delta-good"
                    : "font-semibold text-delta-bad"
                }
              >
                {datos.healthy ? "Sano" : "Con problemas"}
              </span>
              {" — "}
              {datos.message}
            </p>
            <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-[13px] sm:grid-cols-4">
              <Dato etiqueta="Símbolos seguidos" valor={datos.symbols_tracked ?? 0} />
              <Dato
                etiqueta="Barras guardadas"
                valor={(datos.bars_stored ?? 0).toLocaleString("es-ES")}
              />
              <Dato
                etiqueta="Latencia media"
                valor={
                  datos.avg_latency_ms === null || datos.avg_latency_ms === undefined
                    ? "n/d"
                    : `${Math.round(datos.avg_latency_ms)} ms`
                }
              />
              <Dato etiqueta="Fallos seguidos" valor={datos.consecutive_failures ?? 0} />
            </dl>
          </div>
        )}
      </Seccion>

      <Seccion titulo="Cotizaciones" consulta={cotizaciones}>
        {(datos: QuoteRow[]) =>
          datos.length === 0 ? (
            <p className="text-[13px] text-text-muted">
              Todavía no hay cotizaciones. El ingestor las escribe en horario de mercado, y
              solo de los símbolos de los perfiles activos.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border bg-card">
              <table className="w-full text-[13px]">
                <caption className="sr-only">
                  Último precio conocido de cada símbolo, con su antigüedad
                </caption>
                <thead className="text-left text-text-muted">
                  <tr className="border-b border-border">
                    <th scope="col" className="px-3 py-2 font-medium">
                      Símbolo
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">
                      Precio
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">
                      Variación
                    </th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">
                      Antigüedad
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {datos.map((fila) => (
                    <FilaCotizacion
                      key={fila.symbol}
                      fila={fila}
                      recibidasEn={quotesRecibidasEn}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Seccion>

      <Seccion titulo="Bolsas" consulta={mercados}>
        {(datos: MarketInfo[]) => (
          <div className="grid gap-4 sm:grid-cols-2">
            {datos.map((mercado) => (
              <TarjetaMercado key={mercado.code} mercado={mercado} />
            ))}
          </div>
        )}
      </Seccion>
    </>
  );
}

function Dato({ etiqueta, valor }: { etiqueta: string; valor: string | number }) {
  return (
    <div>
      <dt className="text-text-muted">{etiqueta}</dt>
      <dd className="tabular">{valor}</dd>
    </div>
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
        {/* La ventana operativa no es la sesión (FE.13): en Europa se trabaja de
            09:15 a 17:45 sobre una sesión de 09:00 a 17:30. */}
        <dt className="text-text-muted">Ventana</dt>
        <dd className="tabular text-right">
          {mercado.operating_open}–{mercado.operating_close}
        </dd>
        <dt className="text-text-muted">Universo</dt>
        <dd className="tabular text-right">{mercado.universe_size} valores</dd>
        <dt className="text-text-muted">Liquidez mínima</dt>
        <dd className="tabular text-right">
          {mercado.min_turnover.toLocaleString("es-ES")} {mercado.currency}
        </dd>
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
    <tr className="border-b border-border last:border-0">
      <th scope="row" className="px-3 py-1.5 text-left font-normal">
        {fila.symbol}
      </th>
      <td className="tabular px-3 py-1.5 text-right">{fila.price.toFixed(2)}</td>
      <td
        className={
          variacion === null || variacion === undefined
            ? "tabular px-3 py-1.5 text-right text-text-muted"
            : variacion >= 0
              ? "tabular px-3 py-1.5 text-right text-delta-good"
              : "tabular px-3 py-1.5 text-right text-delta-bad"
        }
      >
        {variacion === null || variacion === undefined
          ? "—"
          : `${variacion >= 0 ? "+" : ""}${variacion.toFixed(2)}%`}
      </td>
      {/* Se avisa a partir de 5 minutos: es el umbral donde «en vivo» deja de
          serlo con barras de un minuto. */}
      <td
        className={
          edad !== null && edad > 300
            ? "tabular px-3 py-1.5 text-right font-medium text-warning"
            : "tabular px-3 py-1.5 text-right text-text-muted"
        }
      >
        {edad === null ? "n/d" : `${Math.round(edad)} s`}
      </td>
    </tr>
  );
}
