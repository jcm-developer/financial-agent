import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { RejectionCount, SymbolPerformance } from "@/api/types";
import { COLORES, EJE, Grafica, Globo, TablaSimple } from "@/components/graficas/base";
import { dinero, entero, fechaHora, porcentaje } from "@/lib/formato";

/**
 * P&L realizado por activo.
 *
 * El color codifica **polaridad**, no identidad: azul lo que ganó, rojo lo que
 * perdió. Por eso no hay leyenda —no hay series que distinguir— y sí una línea en
 * el cero, que es donde está el significado.
 */
export function PnlPorSimbolo({
  filas,
  simbolo,
}: {
  filas: SymbolPerformance[];
  simbolo: string;
}) {
  const datos = filas.filter((f) => f.total_pnl !== null && f.total_pnl !== undefined);

  return (
    <Grafica
      titulo="P&L realizado por activo"
      explicacion="Solo operaciones cerradas. Las abiertas no cuentan hasta que se cierran."
      vacia={
        datos.length === 0
          ? "Ninguna posición cerrada todavía, así que no hay nada realizado que repartir."
          : undefined
      }
      tabla={
        <TablaSimple
          columnas={["Activo", "Operaciones", "Aciertos", "P&L total", "Días medios"]}
          filas={datos.map((f) => [
            f.symbol,
            f.trades,
            porcentaje(f.win_rate_pct),
            dinero(f.total_pnl, simbolo),
            f.avg_holding_days ?? "—",
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={datos} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORES.rejilla} vertical={false} />
          <XAxis dataKey="symbol" {...EJE} />
          <YAxis {...EJE} width={70} />
          <ReferenceLine y={0} stroke={COLORES.eje} />
          <Tooltip
            content={<Globo formato={(v) => dinero(v, simbolo)} />}
            cursor={{ fill: COLORES.cursor }}
          />
          <Bar dataKey="total_pnl" name="P&L" radius={[4, 4, 0, 0]}>
            {datos.map((f) => (
              <Cell
                key={f.symbol}
                fill={(f.total_pnl ?? 0) >= 0 ? COLORES.positivo : COLORES.negativo}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Grafica>
  );
}

/**
 * Contra qué límite choca el modelo.
 *
 * Barras horizontales porque las etiquetas son nombres de regla
 * (`max_position_pct`, `min_conviction`) y en vertical se solaparían o habría que
 * girarlas, que es peor. Una sola magnitud, así que un solo tono.
 */
export function RechazosPorRegla({ filas }: { filas: RejectionCount[] }) {
  const datos = [...filas].sort((a, b) => b.rejections - a.rejections);

  return (
    <Grafica
      titulo="Rechazos del Risk Manager"
      explicacion="Si casi todos son de la misma regla, o el modelo insiste en algo que no cabe o ese límite está mal puesto."
      vacia={
        datos.length === 0
          ? "El Risk Manager no ha rechazado nada. Con pocas propuestas es lo esperable."
          : undefined
      }
      tabla={
        <TablaSimple
          columnas={["Regla", "Rechazos", "Último"]}
          filas={datos.map((f) => [f.rule, f.rejections, fechaHora(f.last_seen)])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={datos}
          layout="vertical"
          margin={{ top: 8, right: 16, bottom: 0, left: 8 }}
        >
          <CartesianGrid stroke={COLORES.rejilla} horizontal={false} />
          <XAxis type="number" {...EJE} allowDecimals={false} />
          <YAxis type="category" dataKey="rule" {...EJE} width={140} />
          <Tooltip
            content={<Globo formato={(v) => entero(v)} />}
            cursor={{ fill: COLORES.cursor }}
          />
          <Bar
            dataKey="rejections"
            name="Rechazos"
            fill={COLORES.serie2}
            radius={[0, 4, 4, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </Grafica>
  );
}
