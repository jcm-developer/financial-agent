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
 * Sidebar navigation.
 *
 * Two groups, and the separation matters: above, what depends on the experiment
 * being looked at; below, what does not. Without that rule, "Perfiles" and
 * "Posiciones" look like the same kind of thing and they are not — one holds for
 * every experiment and the other changes completely depending on which is
 * selected.
 *
 * The groups now carry **written headings** rather than a bare rule between
 * them. The distinction was documented here and invisible on screen, which made
 * it a rule nobody could follow while reading the sidebar; a chip-styled caption
 * costs one line each and says it out loud.
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

/** Verdana's list row, at the sidebar's scale: 40 px tall, 8×12, 8 px radius. */
const LINK_CLASSES =
  "flex h-10 items-center gap-3 rounded-md px-3 text-body-sm transition-colors duration-150";

const GROUP_TITLE = "px-3 pb-2 text-caption tracking-[0.5px] text-text-muted uppercase";

/**
 * @param props - What `NavLink` passes to its class callback.
 * @param props.isActive - Whether the link points at the current route.
 * @return The class string for that link.
 */
function linkClasses({ isActive }: { isActive: boolean }) {
  return cn(
    LINK_CLASSES,
    isActive
      ? // The active row is Verdana's #0F172A06 active background, with the navy
        // restated on the label and the icon. `aria-current` carries it for a
        // screen reader, so the tint is never the only thing saying which one
        // it is.
        "bg-primary/4 font-medium text-primary"
      : "text-text-secondary hover:bg-background hover:text-foreground",
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
    <nav aria-label="Secciones" className="flex flex-col gap-6">
      <div className="flex flex-col gap-0.5">
        <p className={GROUP_TITLE}>Experimento</p>
        {profile ? (
          PROFILE_LINKS.map(({ to, text, Icon }) => (
            <NavLink
              key={to}
              to={`/p/${encodeURIComponent(profile)}/${to}`}
              className={linkClasses}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              {text}
            </NavLink>
          ))
        ) : (
          <p className="px-3 py-2 text-body-sm text-text-muted">
            Elige un experimento para ver sus datos.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-0.5">
        <p className={GROUP_TITLE}>General</p>
        {GENERAL_LINKS.map(({ to, text, Icon }) => (
          <NavLink key={to} to={to} className={linkClasses}>
            <Icon className="size-4 shrink-0" aria-hidden />
            {text}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
