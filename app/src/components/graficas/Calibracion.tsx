import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { CalibrationBucket, ConvictionBucket } from "@/api/types";
import { COLORES, EJE, Grafica, Globo, TablaSimple } from "@/components/graficas/base";
import { dinero, entero, porcentaje } from "@/lib/formato";

/** Debajo de esto, un porcentaje de acierto no significa nada. */
const MUESTRA_MINIMA = 5;

/**
 * **La gráfica que decide el experimento.**
 *
 * Acierto real agrupado por la convicción que el modelo declaró al entrar. Si las
 * barras no suben de izquierda a derecha, la convicción no informa de nada y se
 * está operando con ruido caro — que es exactamente la pregunta que este proyecto
 * existe para responder.
 *
 * **Cada barra lleva su número de operaciones encima**, y las que no llegan a
 * cinco salen atenuadas. Sin eso la gráfica miente en su momento más peligroso:
 * un tramo con una sola operación ganadora dibuja una barra del 100 % idéntica a
 * la de un tramo con treinta, y es justo al principio —cuando hay pocas— cuando
 * más ganas dan de sacar conclusiones.
 */
export function Calibracion({
  tramos,
  simbolo,
}: {
  tramos: CalibrationBucket[];
  simbolo: string;
}) {
  const datos = tramos.map((t) => ({
    ...t,
    etiqueta: `${t.conviction_bucket}–${t.conviction_bucket + 9}`,
    fiable: t.trades >= MUESTRA_MINIMA,
  }));
  const pocas = datos.some((d) => !d.fiable);

  return (
    <Grafica
      titulo="Calibración de la convicción"
      explicacion={
        <>
          Acierto real según la convicción declarada al entrar. Si no sube de izquierda a
          derecha, la convicción del modelo no informa de nada.
          {pocas && " Los tramos atenuados tienen menos de cinco operaciones: no concluyas de ellos."}
        </>
      }
      vacia={
        datos.length === 0
          ? "Hace falta al menos una operación cerrada que venga de una decisión de entrada. Con un ciclo al día, esto tarda semanas en decir algo."
          : undefined
      }
      tabla={
        <TablaSimple
          columnas={["Convicción", "Operaciones", "Aciertos", "P&L medio"]}
          filas={datos.map((d) => [
            d.etiqueta,
            d.trades,
            porcentaje(d.win_rate_pct),
            dinero(d.avg_pnl, simbolo),
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={datos} margin={{ top: 18, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORES.rejilla} vertical={false} />
          <XAxis dataKey="etiqueta" {...EJE} />
          <YAxis {...EJE} width={45} domain={[0, 100]} unit="%" />
          {/* El 50 % es la referencia que da sentido a la altura: por debajo, el
              tramo acierta menos que una moneda. */}
          <ReferenceLine
            y={50}
            stroke={COLORES.neutro}
            strokeDasharray="4 4"
            label={{ value: "azar", position: "insideTopRight", fontSize: 10, fill: "var(--color-text-muted)" }}
          />
          <Tooltip
            content={<Globo formato={(v) => porcentaje(v)} />}
            cursor={{ fill: "var(--color-surface-sunken)" }}
          />
          <Bar dataKey="win_rate_pct" name="Aciertos" radius={[4, 4, 0, 0]}>
            {datos.map((d) => (
              <Cell
                key={d.conviction_bucket}
                fill={COLORES.serie1}
                fillOpacity={d.fiable ? 1 : 0.35}
              />
            ))}
            <LabelList
              dataKey="trades"
              position="top"
              fontSize={10}
              fill="var(--color-text-muted)"
              formatter={(valor) => (valor === undefined ? "" : `n=${valor}`)}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Grafica>
  );
}

/**
 * Reparto de la convicción declarada, por acción propuesta.
 *
 * Si se concentra en un solo tramo, el modelo no discrimina entre oportunidades:
 * declara lo mismo para todo y su convicción no es una señal, es una costumbre.
 *
 * Los colores son una escala **divergente**, no categórica: comprar y vender son
 * los dos polos y mantener es el punto neutro, así que le toca el gris. Validado
 * como divergente y no como paleta categórica — un categórico exigiría croma en
 * los tres y aquí el gris del medio es lo correcto.
 */
export function HistogramaConviccion({ tramos }: { tramos: ConvictionBucket[] }) {
  const datos = tramos.map((t) => ({
    ...t,
    etiqueta: `${t.bucket}–${t.bucket + 9}`,
  }));

  return (
    <Grafica
      titulo="Convicción declarada"
      explicacion="Cuántas decisiones cayeron en cada tramo. Si se concentra en uno solo, el modelo no está discriminando entre oportunidades."
      vacia={datos.length === 0 ? "El analista no ha registrado decisiones todavía." : undefined}
      tabla={
        <TablaSimple
          columnas={["Convicción", "Compras", "Mantener", "Ventas", "Total"]}
          filas={datos.map((d) => [
            d.etiqueta,
            d.buys ?? 0,
            d.holds ?? 0,
            d.sells ?? 0,
            d.total ?? 0,
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={datos} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={COLORES.rejilla} vertical={false} />
          <XAxis dataKey="etiqueta" {...EJE} />
          <YAxis {...EJE} width={40} allowDecimals={false} />
          <Tooltip
            content={<Globo formato={(v) => entero(v)} />}
            cursor={{ fill: "var(--color-surface-sunken)" }}
          />
          {/* Apiladas con 2 px de hueco entre segmentos: sin la separación, dos
              tramos contiguos del mismo alto se leen como uno solo. */}
          <Bar dataKey="buys" name="Compras" stackId="a" fill={COLORES.positivo} />
          <Bar dataKey="holds" name="Mantener" stackId="a" fill={COLORES.neutro} />
          <Bar
            dataKey="sells"
            name="Ventas"
            stackId="a"
            fill={COLORES.negativo}
            radius={[4, 4, 0, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </Grafica>
  );
}
