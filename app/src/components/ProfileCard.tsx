import type { ReactNode } from "react";
import { Link } from "react-router";

import type { ProfileSummary } from "@/api/types";
import { Card, LINK_CLASSES, Stat } from "@/components/pieces";
import { ProfileStatus } from "@/components/ProfileStatus";
import { dateTime, money, percent, signClass } from "@/lib/format";

/**
 * One experiment, with the figures that say whether it is worth opening (F5.2).
 *
 * **The card is not a `<Link>`, and the name inside it is.** The obvious version
 * —the whole card navigating— is what the minimal list did, and it cannot hold
 * the actions of F5.4: a `<button>` inside an `<a>` is invalid HTML, and the
 * browsers that do render it make the click ambiguous. The name being the link
 * also gives the screen reader something to announce that is not "link,
 * europa-01 activo mercado EU 10.240,00 € …".
 *
 * **Which six figures**, out of the twelve `metrics` carries: the ones that
 * answer "is this experiment alive and is it working". Capital and total return
 * say how it is going, the day's P&L whether it moved today, open positions
 * whether it is holding anything, the win rate whether it is getting them right
 * —with the count beside it, because a 100 % over two trades is not a win rate—
 * and the last cycle whether it is still running at all. The rest is a click
 * away in the summary.
 */
interface Props {
  profile: ProfileSummary;
  /** The actions of F5.4, rendered by whoever owns the mutations. */
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
    <Card as="article" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-[15px] font-semibold">
          <Link
            to={`/p/${encodeURIComponent(profile.name)}/summary`}
            className={LINK_CLASSES}
          >
            {profile.name}
          </Link>
        </h3>
        <ProfileStatus status={profile.status} />
      </div>

      {profile.description && (
        <p className="text-[13px] text-text-secondary">{profile.description}</p>
      )}

      <p className="text-[13px] text-text-muted">
        {/* The market comes first because it decides the currency, and the
            currency is what stops two budgets being compared as if they were the
            same unit (FE.8). */}
        {profile.market.toUpperCase()} · {profile.currency} ·{" "}
        {profile.llm_provider}/{profile.llm_model}
        {(profile.watched_symbols ?? 0) > 0 &&
          ` · ${profile.watched_symbols} símbolos en vivo`}
      </p>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-[13px] sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="Capital" value={money(m.equity, symbol)}>
          de {money(m.initial_budget, symbol)}
        </Stat>
        <Stat
          label="Rentabilidad"
          value={percent(m.total_return_pct, { sign: true })}
          valueClass={signClass(m.total_return_pct)}
          title="Contra el presupuesto asignado, no contra el primer día."
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
          {/* The count travels with the rate, always. A 100 % over two trades
              and a 100 % over thirty are the same number and not the same
              claim, and the list is exactly where two experiments get compared
              on it. */}
          {closed === 0 ? "sin cerrar ninguna" : `sobre ${closed} cerradas`}
        </Stat>
        <Stat
          label="Último ciclo"
          value={dateTime(m.last_cycle_at)}
          title={
            m.last_cycle_status
              ? `El último ciclo terminó en estado ${m.last_cycle_status}.`
              : "Este experimento no ha ejecutado ningún ciclo todavía."
          }
        >
          <span className={m.last_cycle_status === "failed" ? "text-delta-bad" : undefined}>
            {m.last_cycle_status ?? "ninguno"}
          </span>
        </Stat>
      </dl>

      {actions && <div className="flex flex-wrap gap-2 border-t border-border pt-3">{actions}</div>}
    </Card>
  );
}
