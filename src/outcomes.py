"""Whether each decision was right, `hold` included (F9.27).

Until this, only executed trades were judged, so the P&L mixed the model's
judgement with what the Risk Manager, the entry cap and the screener's order did
to it — and the `hold`s, the vast majority of what the analyst says, were never
judged at all. `eu-sol-base` answered `hold` to 40 of 40 in its first two cycles:
without this there is no way to say whether that prudence was right.

**It is a calculation, not a record.** It reads `decisions` and the daily bars
already in `bar_cache`, so it covers the whole history retroactively and adds
nothing to the cycle. The cycle never reads it either: a model that saw its own
scorecard would stop being the same experiment.

How a decision is judged, and why:

  * **The price it is measured from is `reference_price`**, the one the model
    saw. Not the fill: a `hold` has no fill, and judging buys from the fill and
    holds from the quote would compare two different things.
  * **Forward return at 5 and 20 completed sessions**, counting the decision
    day's own close as the first: a cycle decides mid-session, so that close
    already comes after the decision. Today's bar is left out while the day is
    still open, because its close is not a close yet. The experiment's horizon
    (180 days) is too long to wait for, so it is not a column; 20 sessions is a
    month, which is what can be read while the duel is still running.
  * **What counts as right is the sign of that return, read by what the action
    claimed.** A `buy`, and a `hold` of an open position, claim the price will
    go up: right if it did. A `sell`, and a `hold` of a candidate —staying out—,
    claim it will not: right if it did not. That is the only reading that puts
    the `hold`s on the same scale as the buys.
  * **For a `buy` with levels, which one it touched first**, from the daily
    highs and lows. If a single bar spans both, it counts as the stop: a daily
    bar does not say which came first, and assuming the kind outcome would flatter
    the model exactly where the data is silent.

Discarded: netting the commission out of the return. It is about 0,15 % of a
2.000 € order, below the resolution of a 20-session move, and it would put a
cost on the `hold`s that they never paid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .db import Database

#: Completed sessions after the decision at which it is judged. **0 means «up
#: to the last close»**, whatever the number of sessions: without it the screen
#: would stay empty for the first week of every experiment, and a week is when
#: one most wants to know which way things are going. It is provisional and the
#: screen says so; the fixed horizons are the verdict.
HORIZONS: tuple[int, ...] = (0, 5, 20)

#: The actions that claim the price will go up, by decision kind.
_CLAIMS_UP = {("entry", "buy"), ("exit", "buy"), ("exit", "hold")}

#: How each (kind, action) is named on screen, in the order the table shows them.
GROUPS: tuple[tuple[str, str, str], ...] = (
    ("entry", "buy", "Comprar"),
    ("entry", "hold", "Quedarse fuera"),
    ("exit", "hold", "Mantener la posición"),
    ("exit", "buy", "Ampliar"),
    ("exit", "sell", "Vender o reducir"),
)


@dataclass(frozen=True)
class Bar:
    day: date
    high: float
    low: float
    close: float


@dataclass
class Outcome:
    decision_id: str
    kind: str
    action: str
    conviction: int
    #: Percentage change from `reference_price`, per horizon. None = not yet.
    returns: dict[int, float | None] = field(default_factory=dict)
    #: Whether the claim held, per horizon. None = not yet.
    right: dict[int, bool | None] = field(default_factory=dict)
    #: For a `buy` with levels: "stop", "target", or None if neither yet.
    first_touch: str | None = None


def score(
    decision: dict[str, Any], bars: list[Bar], *, horizons: tuple[int, ...] = HORIZONS
) -> Outcome:
    """Judges one decision against the completed bars from its day on.

    @param decision: A `decisions` row, with at least `id`, `kind`, `action`,
        `conviction`, `reference_price`, `created_at`, and the suggested levels.
    @param bars: Completed daily bars of the symbol, oldest first, **starting on
        the decision's day**. The caller trims them.
    """
    outcome = Outcome(
        decision_id=decision["id"], kind=decision["kind"], action=decision["action"],
        conviction=int(decision["conviction"]),
    )
    reference = decision.get("reference_price")
    claims_up = (outcome.kind, outcome.action) in _CLAIMS_UP
    for horizon in horizons:
        sessions = len(bars) if horizon == 0 else horizon
        if not reference or not bars or len(bars) < sessions:
            outcome.returns[horizon] = None
            outcome.right[horizon] = None
            continue
        change = (bars[sessions - 1].close / float(reference) - 1) * 100
        outcome.returns[horizon] = round(change, 2)
        outcome.right[horizon] = change > 0 if claims_up else change <= 0

    stop, target = decision.get("suggested_stop"), decision.get("suggested_target")
    if outcome.action == "buy" and stop and target:
        for bar in bars:
            if bar.low <= stop:
                outcome.first_touch = "stop"
                break
            if bar.high >= target:
                outcome.first_touch = "target"
                break
    return outcome


def summarize(outcomes: list[Outcome], *, horizons: tuple[int, ...] = HORIZONS) -> dict[str, Any]:
    """Hit rate and mean return per action and horizon, and a conviction ladder.

    The ladder is built from the entry decisions —buy and stay out together—
    because that is where the model chooses between candidates: if the
    conviction means anything, the `buy`s of 70 should do better than those of
    55, and a `hold` of 70 should be right more often than one of 55.
    """
    groups = []
    for kind, action, label in GROUPS:
        members = [o for o in outcomes if o.kind == kind and o.action == action]
        if not members:
            continue
        groups.append({
            "kind": kind, "action": action, "label": label, "decisions": len(members),
            "horizons": [_cell(members, h) for h in horizons],
            "stops": sum(1 for o in members if o.first_touch == "stop"),
            "targets": sum(1 for o in members if o.first_touch == "target"),
        })

    ladder = []
    entries = [o for o in outcomes if o.kind == "entry" and o.action in ("buy", "hold")]
    for bucket in sorted({o.conviction // 10 * 10 for o in entries}):
        for action in ("buy", "hold"):
            members = [
                o for o in entries if o.action == action and o.conviction // 10 * 10 == bucket
            ]
            if members:
                ladder.append({
                    "bucket": bucket, "action": action, "decisions": len(members),
                    "horizons": [_cell(members, h) for h in horizons],
                })

    return {
        "horizons": list(horizons),
        "decisions": len(outcomes),
        # Judged means at the first fixed horizon, not «up to the last close».
        "judged": sum(
            1 for o in outcomes
            if o.right.get(next((h for h in horizons if h), 0)) is not None
        ),
        "groups": groups,
        "ladder": ladder,
    }


def _cell(members: list[Outcome], horizon: int) -> dict[str, Any]:
    judged = [o for o in members if o.right.get(horizon) is not None]
    returns = [o.returns[horizon] for o in judged if o.returns.get(horizon) is not None]
    return {
        "sessions": horizon,
        "judged": len(judged),
        "right": sum(1 for o in judged if o.right[horizon]),
        "hit_rate_pct": round(sum(1 for o in judged if o.right[horizon]) / len(judged) * 100, 1)
        if judged else None,
        "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
    }


# ----------------------------------------------------------------------
# From the database
# ----------------------------------------------------------------------

def score_portfolio(
    db: Database, portfolio_id: str, *, today: date | None = None,
    horizons: tuple[int, ...] = HORIZONS,
) -> list[Outcome]:
    """Every decision of a book, judged with the bars in `bar_cache`.

    One query for the decisions and one for the bars of all their symbols, not
    one per decision: a month of a profile is ~1.000 rows.

    @param today: Bars from this day on are left out as still open. Defaults to
        the UTC date, which in Europe is the local one from 02:00 on.
    """
    today = today or datetime.now(timezone.utc).date()
    decisions = db.query(
        "select id, symbol, kind, action, conviction, reference_price, "
        "       suggested_stop, suggested_target, created_at "
        "from decisions where portfolio_id = ? order by created_at",
        (portfolio_id,),
    )
    if not decisions:
        return []
    symbols = sorted({d["symbol"] for d in decisions})
    first_day = min(d["created_at"][:10] for d in decisions)
    marks = ", ".join("?" for _ in symbols)
    by_symbol: dict[str, list[Bar]] = {}
    for row in db.query(
        "select symbol, ts, high, low, close from bar_cache "
        f"where interval = '1d' and symbol in ({marks}) and substr(ts, 1, 10) >= ? "
        "order by ts",
        (*symbols, first_day),
    ):
        day = date.fromisoformat(row["ts"][:10])
        if day >= today:
            continue
        by_symbol.setdefault(row["symbol"], []).append(
            Bar(day=day, high=row["high"], low=row["low"], close=row["close"])
        )

    outcomes = []
    for decision in decisions:
        decided = date.fromisoformat(decision["created_at"][:10])
        bars = [bar for bar in by_symbol.get(decision["symbol"], []) if bar.day >= decided]
        outcomes.append(score(decision, bars, horizons=horizons))
    return outcomes
