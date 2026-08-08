import { NavLink } from "react-router";
import {
  Activity,
  ClipboardList,
  FlaskConical,
  ChartLine,
  LayoutDashboard,
  Radio,
  ReceiptText,
  ShieldAlert,
  Sliders,
  Wallet,
} from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Barra lateral (F4.3).
 *
 * Dos grupos, y la separación importa: arriba lo que depende del experimento que
 * se esté mirando, abajo lo que no. Sin esa raya, "Perfiles" y "Posiciones"
 * parecen la misma clase de cosa y no lo son — una vale para todos los
 * experimentos y la otra cambia por completo según cuál esté seleccionado.
 */

const DEL_PERFIL = [
  { a: "resumen", texto: "Resumen", Icono: LayoutDashboard },
  { a: "analitica", texto: "Analítica", Icono: ChartLine },
  { a: "posiciones", texto: "Posiciones", Icono: Wallet },
  { a: "decisiones", texto: "Decisiones", Icono: ClipboardList },
  { a: "ordenes", texto: "Órdenes", Icono: ReceiptText },
  { a: "riesgo", texto: "Riesgo", Icono: ShieldAlert },
  { a: "ciclos", texto: "Ciclos", Icono: Activity },
  { a: "ajustes", texto: "Ajustes", Icono: Sliders },
] as const;

const GENERALES = [
  { a: "/perfiles", texto: "Experimentos", Icono: FlaskConical },
  { a: "/diagnostico", texto: "Ingesta", Icono: Radio },
] as const;

const CLASES_ENLACE =
  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors";

/**
 * @param props - What `NavLink` passes to its class callback.
 * @param props.isActive - Whether the link points at the current route.
 * @return The class string for that link.
 */
function clases({ isActive }: { isActive: boolean }) {
  return cn(
    CLASES_ENLACE,
    isActive
      ? "bg-surface-sunken font-medium text-foreground"
      : "text-text-secondary hover:bg-surface-sunken hover:text-foreground",
  );
}

/**
 * The section navigation.
 *
 * @param props - Sidebar props.
 * @param props.perfil - Active profile name, which the per-profile links hang
 *     off. Undefined shows only the sections that exist without one.
 * @return The rendered navigation.
 */
export function BarraLateral({ perfil }: { perfil: string | undefined }) {
  return (
    <nav aria-label="Secciones" className="flex flex-col gap-1">
      {perfil ? (
        DEL_PERFIL.map(({ a, texto, Icono }) => (
          <NavLink key={a} to={`/p/${encodeURIComponent(perfil)}/${a}`} className={clases}>
            <Icono className="size-3.5 shrink-0" aria-hidden />
            {texto}
          </NavLink>
        ))
      ) : (
        <p className="px-2.5 py-1.5 text-[13px] text-text-muted">
          Elige un experimento para ver sus datos.
        </p>
      )}

      <hr className="my-2 border-border" />

      {GENERALES.map(({ a, texto, Icono }) => (
        <NavLink key={a} to={a} className={clases}>
          <Icono className="size-3.5 shrink-0" aria-hidden />
          {texto}
        </NavLink>
      ))}
    </nav>
  );
}
