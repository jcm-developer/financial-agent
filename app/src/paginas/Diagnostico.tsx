import { useIngestStatus, useMarkets, useQuotes } from "@/api/hooks";
import { antiguedadReal, useQuotesRecibidasEn } from "@/api/stream";
import type { MarketInfo, QuoteRow } from "@/api/types";
import { Tarjeta, TituloBloque, TituloPagina } from "@/components/piezas";
import { Seccion } from "@/components/Seccion";
import { Cabecera, Fila, Tabla, Td, Th } from "@/components/Tabla";
import { useTitulo } from "@/layout/useTitulo";
import { cn } from "@/lib/utils";

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
 *
 * @return The rendered screen.
 */
export function Diagnostico() {
  useTitulo("Ingesta");
  const mercados = useMarkets();
  const ingesta = useIngestStatus();
  const cotizaciones = useQuotes();
  // Solo la marca de llegada, para corregir la antigüedad. La conexión SSE la
  // abre el Layout una sola vez: pedirla aquí abriría una segunda.
  const quotesRecibidasEn = useQuotesRecibidasEn();

  return (
    <>
      <TituloPagina>Ingesta y mercados</TituloPagina>

      <Seccion titulo="Salud del ingestor" consulta={ingesta}>
        {(datos) => (
          <Tarjeta>
            <p className="text-[13px]">
              <span
                className={cn(
                  "font-semibold",
                  datos.healthy ? "text-delta-good" : "text-delta-bad",
                )}
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
          </Tarjeta>
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
            <Tabla titulo="Último precio conocido de cada símbolo, con su antigüedad">
              <Cabecera>
                <Th>Símbolo</Th>
                <Th numerica>Precio</Th>
                <Th numerica>Variación</Th>
                <Th numerica>Antigüedad</Th>
              </Cabecera>
              <tbody>
                {datos.map((fila) => (
                  <FilaCotizacion
                    key={fila.symbol}
                    fila={fila}
                    recibidasEn={quotesRecibidasEn}
                  />
                ))}
              </tbody>
            </Tabla>
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

/**
 * A label and its value inside a definition list.
 *
 * @param props - Item props.
 * @param props.etiqueta - Label, in the interface language.
 * @param props.valor - Value, already formatted.
 * @return The rendered pair.
 */
function Dato({ etiqueta, valor }: { etiqueta: string; valor: string | number }) {
  return (
    <div>
      <dt className="text-text-muted">{etiqueta}</dt>
      <dd className="tabular">{valor}</dd>
    </div>
  );
}

/**
 * One market's card: session, calendar, universe and liquidity floor.
 *
 * @param props - Card props.
 * @param props.mercado - The market, whose own currency labels its figures.
 * @return The rendered card.
 */
function TarjetaMercado({ mercado }: { mercado: MarketInfo }) {
  return (
    <Tarjeta etiqueta="article">
      <div className="flex items-baseline justify-between gap-3">
        <TituloBloque>{mercado.label}</TituloBloque>
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
    </Tarjeta>
  );
}

/**
 * One row of the quotes table, showing how old the price really is.
 *
 * @param props - Row props.
 * @param props.fila - The quote.
 * @param props.recibidasEn - When the batch arrived, which is added to the
 *     server's age so a stale price cannot pass as fresh.
 * @return The rendered row.
 */
function FilaCotizacion({
  fila,
  recibidasEn,
}: {
  fila: QuoteRow;
  recibidasEn: number | null;
}) {
  const edad = antiguedadReal(fila, recibidasEn);
  const variacion = fila.change_pct;
  const sinVariacion = variacion === null || variacion === undefined;

  return (
    <Fila>
      <Td encabezado>{fila.symbol}</Td>
      <Td numerica>{fila.price.toFixed(2)}</Td>
      <Td
        numerica
        className={
          sinVariacion
            ? "text-text-muted"
            : variacion >= 0
              ? "text-delta-good"
              : "text-delta-bad"
        }
      >
        {sinVariacion ? "—" : `${variacion >= 0 ? "+" : ""}${variacion.toFixed(2)}%`}
      </Td>
      {/* Se avisa a partir de 5 minutos: es el umbral donde «en vivo» deja de
          serlo con barras de un minuto. */}
      <Td
        numerica
        className={
          edad !== null && edad > 300 ? "font-medium text-warning" : "text-text-muted"
        }
      >
        {edad === null ? "n/d" : `${Math.round(edad)} s`}
      </Td>
    </Fila>
  );
}
