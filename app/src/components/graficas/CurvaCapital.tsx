import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { EquityPoint } from "@/api/types";
import { COLORES, EJE, Grafica, Globo, TablaSimple } from "@/components/graficas/base";
import { dinero, fechaHora, porcentaje } from "@/lib/formato";

/**
 * Curva de capital y caída, en **dos gráficas y no en una con dos ejes**.
 *
 * Un eje doble —capital en euros a la izquierda, caída en % a la derecha— deja
 * que la escala elegida decida cuál de las dos líneas parece dominar, y dos
 * personas leen cosas distintas del mismo dibujo. Separadas y con el mismo eje de
 * tiempo debajo, la comparación se hace mirando hacia abajo, que es honesto.
 */

interface Props {
  puntos: EquityPoint[];
  simbolo: string;
  presupuesto: number | null | undefined;
}

/**
 * The experiment's equity over time.
 *
 * @param props - Chart props.
 * @param props.puntos - Equity points, in chronological order.
 * @param props.simbolo - Currency symbol of the profile's market, never assumed.
 * @param props.presupuesto - Assigned budget, which is the reference line the
 *     experiment is measured against. Null falls back to no reference.
 * @return The rendered chart.
 */
export function CurvaCapital({ puntos, simbolo, presupuesto }: Props) {
  const datos = puntos.map((p) => ({ ...p, etiqueta: fechaHora(p.as_of) }));

  return (
    <Grafica
      titulo="Curva de capital"
      explicacion={
        presupuesto
          ? `La referencia es el presupuesto asignado (${dinero(presupuesto, simbolo)}), no el primer punto: es contra lo que se mide el experimento.`
          : undefined
      }
      vacia={
        datos.length === 0
          ? "Todavía no hay curva: se dibuja un punto por ciclo ejecutado."
          : undefined
      }
      tabla={
        <TablaSimple
          columnas={["Momento", "Capital", "Efectivo", "Posiciones", "Caída"]}
          filas={datos.map((p) => [
            p.etiqueta,
            dinero(p.equity, simbolo),
            dinero(p.cash, simbolo),
            p.open_positions ?? 0,
            porcentaje(p.drawdown_pct),
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={datos} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORES.rejilla} vertical={false} />
          <XAxis dataKey="etiqueta" {...EJE} minTickGap={40} />
          <YAxis {...EJE} width={70} domain={["auto", "auto"]} />
          {presupuesto ? (
            <ReferenceLine
              y={presupuesto}
              stroke={COLORES.neutro}
              strokeDasharray="4 4"
              label={{ value: "inicial", position: "insideTopRight", fontSize: 10, fill: COLORES.tenue }}
            />
          ) : null}
          <Tooltip
            content={<Globo formato={(v) => dinero(v, simbolo)} />}
            cursor={{ stroke: COLORES.eje }}
          />
          {/* 2 px y sin punto por dato: con un punto por ciclo y diez sesiones,
              marcarlos todos convierte la línea en un collar. */}
          <Line
            type="monotone"
            dataKey="equity"
            name="Capital"
            stroke={COLORES.serie1}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </Grafica>
  );
}

/**
 * The drop from the running peak, drawn below the equity curve and sharing its
 * time axis.
 *
 * @param props - Chart props.
 * @param props.puntos - Equity points, in chronological order.
 * @param props.simbolo - Currency symbol of the profile's market, never assumed.
 * @return The rendered chart.
 */
export function Drawdown({ puntos, simbolo }: Omit<Props, "presupuesto">) {
  const datos = puntos.map((p) => ({ ...p, etiqueta: fechaHora(p.as_of) }));
  const peor = datos.reduce((min, p) => Math.min(min, p.drawdown_pct ?? 0), 0);

  return (
    <Grafica
      titulo="Caída desde máximos"
      explicacion={
        datos.length
          ? `Cuánto habría dolido en el peor momento. La peor hasta ahora: ${porcentaje(peor)}.`
          : undefined
      }
      vacia={datos.length === 0 ? "Sin ciclos todavía." : undefined}
      tabla={
        <TablaSimple
          columnas={["Momento", "Capital", "Caída"]}
          filas={datos.map((p) => [
            p.etiqueta,
            dinero(p.equity, simbolo),
            porcentaje(p.drawdown_pct),
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={datos} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORES.rejilla} vertical={false} />
          <XAxis dataKey="etiqueta" {...EJE} minTickGap={40} />
          {/* El techo se fija en 0: la caída nunca es positiva, y dejar que el
              eje se ajuste solo haría que "sin caída" pareciera un valle. */}
          <YAxis {...EJE} width={50} domain={["auto", 0]} unit="%" />
          <Tooltip
            content={<Globo formato={(v) => porcentaje(v)} />}
            cursor={{ stroke: COLORES.eje }}
          />
          <Area
            type="monotone"
            dataKey="drawdown_pct"
            name="Caída"
            stroke={COLORES.negativo}
            strokeWidth={2}
            fill={COLORES.negativo}
            fillOpacity={0.15}
          />
        </AreaChart>
      </ResponsiveContainer>
    </Grafica>
  );
}
