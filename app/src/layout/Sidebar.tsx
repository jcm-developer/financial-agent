import { NavLink } from "react-router";
import {
  Activity,
  ClipboardList,
  FlaskConical,
  GitCompare,
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
 * Sidebar (F4.3).
 *
 * Two groups, and the separation matters: above, what depends on the experiment
 * being looked at; below, what does not. Without that rule, "Perfiles" and
 * "Posiciones" look like the same kind of thing and they are not — one holds for
 * every experiment and the other changes completely depending on which is
 * selected.
 */

const PROFILE_LINKS = [
  { to: "summary", text: "Resumen", Icon: LayoutDashboard },
  { to: "analytics", text: "Analítica", Icon: ChartLine },
  { to: "positions", text: "Posiciones", Icon: Wallet },
  { to: "decisions", text: "Decisiones", Icon: ClipboardList },
  { to: "orders", text: "Órdenes", Icon: ReceiptText },
  { to: "risk", text: "Riesgo", Icon: ShieldAlert },
  { to: "cycles", text: "Ciclos", Icon: Activity },
  { to: "settings", text: "Ajustes", Icon: Sliders },
] as const;

const GENERAL_LINKS = [
  { to: "/profiles", text: "Experimentos", Icon: FlaskConical },
  // It hangs off the lower group and not off the profile's: comparing is by
  // definition about more than one experiment, so putting it under the selected
  // one would suggest it only concerns that one.
  { to: "/compare", text: "Comparar", Icon: GitCompare },
  { to: "/diagnostics", text: "Ingesta", Icon: Radio },
] as const;

const LINK_CLASSES =
  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors";

/**
 * @param props - What `NavLink` passes to its class callback.
 * @param props.isActive - Whether the link points at the current route.
 * @return The class string for that link.
 */
function linkClasses({ isActive }: { isActive: boolean }) {
  return cn(
    LINK_CLASSES,
    isActive
      ? "bg-surface-sunken font-medium text-foreground"
      : "text-text-secondary hover:bg-surface-sunken hover:text-foreground",
  );
}

/**
 * The section navigation.
 *
 * @param props - Sidebar props.
 * @param props.profile - Active profile name, which the per-profile links hang
 *     off. Undefined shows only the sections that exist without one.
 * @return The rendered navigation.
 */
export function Sidebar({ profile }: { profile: string | undefined }) {
  return (
    <nav aria-label="Secciones" className="flex flex-col gap-1">
      {profile ? (
        PROFILE_LINKS.map(({ to, text, Icon }) => (
          <NavLink
            key={to}
            to={`/p/${encodeURIComponent(profile)}/${to}`}
            className={linkClasses}
          >
            <Icon className="size-3.5 shrink-0" aria-hidden />
            {text}
          </NavLink>
        ))
      ) : (
        <p className="px-2.5 py-1.5 text-[13px] text-text-muted">
          Elige un experimento para ver sus datos.
        </p>
      )}

      <hr className="my-2 border-border" />

      {GENERAL_LINKS.map(({ to, text, Icon }) => (
        <NavLink key={to} to={to} className={linkClasses}>
          <Icon className="size-3.5 shrink-0" aria-hidden />
          {text}
        </NavLink>
      ))}
    </nav>
  );
}
