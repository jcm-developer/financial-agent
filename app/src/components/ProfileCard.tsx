import type { ReactNode } from "react";
import { Link } from "react-router";

import type { ProfileSummary } from "@/api/types";
import { Card, LINK_CLASSES, Stat, Tag } from "@/components/pieces";
import { ProfileStatus } from "@/components/ProfileStatus";
import { dateTime, money, percent, signClass } from "@/lib/format";
import { cycleStatusLabel } from "@/lib/labels";

/**
 * One experiment, with the figures that say whether it is worth opening.
 *
 * **The card is not a `<Link>`, and the name inside it is**: the action row holds
 * buttons, and a `<button>` inside an `<a>` is invalid HTML.
 *
 * **Which six figures**, out of the twelve `metrics` carries: the ones that
 * answer "is this experiment alive and is it working". The win rate always
 * travels with its count, because a 100 % over two trades is not a win rate.
 */
interface Props {
  profile: ProfileSummary;
  /** The action row, rendered by whoever owns the mutations. */
  actions?: ReactNode;
}

/**
 * The card for one experiment.
 *
 * @param props - Card props.
 * @param props.profile - The profile, with its metrics already computed by the API.
 * @param props.actions - What to render in the action row, when there is one.
 * @return The rendered card.
 */
export function ProfileCard({ profile, actions }: Props) {
  const m = profile.metrics;
  const symbol = profile.currency_symbol;
  const closed = m.closed_trades ?? 0;

  return (
    <Card as="article" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-col gap-1">
          <h3 className="text-h4 font-semibold">
            <Link
              to={`/p/${encodeURIComponent(profile.name)}/summary`}
              className={LINK_CLASSES}
            >
              {profile.name}
            </Link>
            {/* The control's numbers are meant to be worse; without the label
                they read as a failed experiment. */}
            {profile.screener_mode === "random" && (
              <Tag tone="neutral" title="Grupo de control: candidatos elegidos al azar.">
                control
              </Tag>
            )}
          </h3>
          <p className="text-body-sm text-text-muted">
            {profile.market.toUpperCase()} · {profile.currency} · {profile.llm_provider}/
            {profile.llm_model}
            {(profile.watched_symbols ?? 0) > 0 &&
              ` · ${profile.watched_symbols} símbolos en vivo`}
          </p>
        </div>
        <ProfileStatus status={profile.status} />
      </div>

      {profile.description && (
        <p className="text-body-sm text-text-secondary">{profile.description}</p>
      )}

      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 border-t border-border pt-4 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Capital" value={money(m.equity, symbol)}>
          de {money(m.initial_budget, symbol)}
        </Stat>
        <Stat
          label="Rentabilidad"
          value={percent(m.total_return_pct, { sign: true })}
          valueClass={signClass(m.total_return_pct)}
        />
        <Stat
          label="P&L del día"
          value={percent(m.day_pnl_pct, { sign: true })}
          valueClass={signClass(m.day_pnl_pct)}
        />
        <Stat label="Abiertas" value={String(m.open_positions ?? 0)} />
        <Stat
          label="Aciertos"
          value={percent(m.win_rate_pct)}
          valueClass={m.win_rate_pct === null || m.win_rate_pct === undefined
            ? "text-text-muted"
            : undefined}
        >
          {closed === 0 ? "sin operaciones cerradas" : `de ${closed} cerradas`}
        </Stat>
        <Stat label="Último ciclo" value={dateTime(m.last_cycle_at)}>
          <span className={m.last_cycle_status === "failed" ? "text-delta-bad" : undefined}>
            {m.last_cycle_status ? cycleStatusLabel(m.last_cycle_status) : "ninguno"}
          </span>
        </Stat>
      </dl>

      {actions && <div className="flex flex-wrap gap-2 border-t border-border pt-4">{actions}</div>}
    </Card>
  );
}
