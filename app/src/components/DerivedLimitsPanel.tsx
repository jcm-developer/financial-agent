import type { DerivedLimits } from "@/api/types";
import { Card, SectionTitle, Stat } from "@/components/pieces";
import { money, percent } from "@/lib/format";

/**
 * What the sliders mean, in numbers (F6.8).
 *
 * **The arithmetic is not repeated here.** The nine limits come from
 * `/api/profiles/limits-preview`, which calls the same `derive_limits` the Risk
 * Manager uses. A second implementation in TypeScript would be two formulas
 * condemned to disagree the day an anchor is tweaked, and then this panel would
 * be promising limits the agent does not enforce — which is the one thing this
 * screen must not do.
 *
 * **`sector_cap` is shown greyed out and says so.** It is computed and **not
 * applied** (F6.5, FE.12): there is no sector datum per symbol at runtime.
 * Showing it as if it were in force would be the worst of the three options,
 * because it is a limit whose absence is invisible — nothing fails, positions
 * just pile up in one sector.
 */
interface Props {
  limits: DerivedLimits;
  /** Currency symbol of the profile's market, never assumed (FE.8). */
  symbol: string;
  /** True while a fresher answer is on its way, so the figures can be dimmed. */
  stale?: boolean;
}

/**
 * The panel showing the nine effective limits.
 *
 * @param props - Panel props.
 * @param props.limits - The limits, as the API derived them.
 * @param props.symbol - Currency symbol of the profile's market.
 * @param props.stale - Whether a fresher answer is still loading.
 * @return The rendered panel.
 */
export function DerivedLimitsPanel({ limits, symbol, stale = false }: Props) {
  return (
    <Card padding="p-4" className={stale ? "opacity-60 transition-opacity" : undefined}>
      <SectionTitle className="mb-3">Con estos ajustes</SectionTitle>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-[13px] sm:grid-cols-3">
        <Stat
          label="Riesgo por operación"
          value={percent(limits.risk_per_trade_pct)}
        />
        <Stat label="Máx. por posición" value={percent(limits.max_position_pct)} />
        <Stat label="Exposición total" value={percent(limits.max_total_exposure_pct)} />
        <Stat label="Posiciones abiertas" value={`máx. ${limits.max_open_positions}`} />
        <Stat
          label="Kill switch diario"
          value={`−${percent(limits.max_daily_loss_pct)}`}
          title="Pérdida diaria a partir de la cual el ciclo se detiene sin operar."
        />
        <Stat label="Convicción mínima" value={String(limits.min_conviction)} />
        <Stat label="Stop" value={`${limits.stop_atr_multiple}× ATR`} />
        <Stat label="Reward/risk mínimo" value={String(limits.min_reward_risk)} />
        <Stat
          label="Orden mínima"
          value={money(limits.min_order_notional, symbol)}
          title="Fricción de ejecución, no apetito de riesgo: no se mueve con el deslizador."
        />
      </dl>

      <p className="mt-4 border-t border-border pt-3 text-xs leading-relaxed text-text-muted">
        <span className="font-semibold">
          Tope por sector: máx. {limits.sector_cap} posiciones — calculado pero no aplicado.
        </span>{" "}
        El Risk Manager no lo hace cumplir porque no hay dato de sector por símbolo en tiempo
        de ejecución (F6.5, FE.12). La diversificación limita cuántas posiciones hay, no en
        cuántos sectores están.
      </p>
    </Card>
  );
}
