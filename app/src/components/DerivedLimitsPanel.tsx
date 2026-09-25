import type { DerivedLimits } from "@/api/types";
import { Card, SectionTitle, Stat } from "@/components/pieces";
import { money, percent } from "@/lib/format";

/**
 * What the limits in force are, in numbers (F6.8).
 *
 * **The arithmetic is not repeated here.** The eleven limits come from the API,
 * which calls the same `resolve_limits` the Risk Manager uses. A second
 * implementation in TypeScript would be two formulas condemned to disagree the
 * day an anchor is tweaked, and then this panel would be promising limits the
 * agent does not enforce — which is the one thing this screen must not do.
 *
 * ⚠️ **And it was doing exactly that until 2026-08-11.** The panel was always fed
 * from `/api/profiles/limits-preview`, which answers "what would the two sliders
 * give" and knows nothing about advanced mode. With the overrides on, the screen
 * showed the sliders' numbers under the heading "Con estos ajustes" while the
 * summary line above it —`risk_presets.describe`, which does honour the
 * overrides— showed the real ones. Both at once: 13 positions against 7, 40 %
 * against 14 %, stop 1,2× against 3×. It went unnoticed because until then every
 * advanced-mode profile had its columns at NULL, so the two answers happened to
 * agree.
 *
 * So `source` is not cosmetic: it says which question the figures answer, and the
 * caller has to pick the right one.
 *
 * **The sector cap is gone since 2026-09-25**, with the diversification slider
 * it was computed from. It was shown greyed out as "computed but not applied",
 * and a figure nothing enforces is the kind of setting this screen was cleaned
 * of. It comes back the day there is a sector datum per symbol (F9.30).
 */
interface Props {
  limits: DerivedLimits;
  /** Currency symbol of the profile's market, never assumed (FE.8). */
  symbol: string;
  /** True while a fresher answer is on its way, so the figures can be dimmed. */
  stale?: boolean;
  /**
   * Which question these figures answer.
   *
   * `"sliders"` is the live preview while a slider moves, and it is the truth
   * only when advanced mode is off. `"effective"` is what the agent will really
   * apply, overrides included, and it is what is **stored** — so a number typed
   * below and not yet saved does not show up here.
   */
  source: "sliders" | "effective";
}

const HAND_SET = "Fijado a mano en los limites duros de abajo.";

/**
 * The panel showing the eleven limits in force.
 *
 * @param props - Panel props.
 * @param props.limits - The limits, as the API resolved them.
 * @param props.symbol - Currency symbol of the profile's market.
 * @param props.stale - Whether a fresher answer is still loading.
 * @param props.source - Whether these are the sliders' or the effective limits.
 * @return The rendered panel.
 */
export function DerivedLimitsPanel({ limits, symbol, stale = false, source }: Props) {
  const derived = new Set(limits.derived_fields);

  /**
   * Marks the limits that are not coming from the sliders.
   *
   * Only in advanced mode: with the overrides off every limit is derived, so the
   * marker would be on all eleven and would say nothing.
   *
   * @param field - The limit's field name.
   * @return The `title` for the figure, or undefined.
   */
  function origin(field: keyof DerivedLimits): string | undefined {
    if (source === "sliders" || derived.has(field)) return undefined;
    return HAND_SET;
  }

  return (
    <Card padding="p-6" className={stale ? "opacity-60 transition-opacity" : undefined}>
      <SectionTitle className="mb-3">Con estos ajustes</SectionTitle>
      {source === "effective" && (
        <p className="mb-3 text-caption text-text-muted">Valores guardados, con los fijados a mano.</p>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-body-sm sm:grid-cols-3">
        <Stat
          label="Riesgo por operación"
          value={percent(limits.risk_per_trade_pct)}
          title={origin("risk_per_trade_pct")}
        />
        <Stat
          label="Posición"
          value={`${percent(limits.min_position_pct)} – ${percent(limits.max_position_pct)}`}
          title={
            origin("max_position_pct") ??
            origin("min_position_pct") ??
            "Banda de tamaño: el analista elige dentro, y el suelo es lo que evita que la cartera se quede a medio invertir."
          }
        />
        <Stat
          label="Exposición total"
          value={percent(limits.max_total_exposure_pct)}
          title={origin("max_total_exposure_pct")}
        />
        <Stat
          label="Posiciones abiertas"
          value={`máx. ${limits.max_open_positions}`}
          title={
            origin("max_open_positions") ??
            "Sale del tamaño: la exposición total en posiciones mínimas. Más riesgo, menos posiciones y más grandes."
          }
        />
        <Stat
          label="Kill switch diario"
          value={`−${percent(limits.max_daily_loss_pct)}`}
          title={
            origin("max_daily_loss_pct") ??
            "Pérdida diaria a partir de la cual el ciclo se detiene sin operar."
          }
        />
        <Stat
          label="Convicción mínima"
          value={String(limits.min_conviction)}
          title={origin("min_conviction")}
        />
        <Stat
          label="Stop"
          value={`${limits.stop_atr_multiple}× ATR · ${limits.stop_sigmas}σ`}
          title={
            origin("stop_atr_multiple") ??
            `A ${limits.stop_sigmas} sigmas del horizonte de ${limits.horizon_days} días: el riesgo decide cuántas sigmas y el horizonte cuánto vale una.`
          }
        />
        <Stat
          label="Reward/risk mínimo"
          value={String(limits.min_reward_risk)}
          title={origin("min_reward_risk")}
        />
        <Stat
          label="Objetivo mínimo"
          value={`${limits.min_target_sigma}σ`}
          title={
            origin("min_target_sigma") ??
            "Recorrido mínimo que tiene que prometer el objetivo, en sigmas del horizonte."
          }
        />
        <Stat
          label="Orden mínima"
          value={money(limits.min_order_notional, symbol)}
          title="Fricción de ejecución, no apetito de riesgo: no se mueve con el deslizador."
        />
      </dl>
    </Card>
  );
}
