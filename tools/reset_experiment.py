"""Wipes an experiment's history without deleting the experiment.

**Why this is a tool and not a handful of `delete from` typed into sqlite3.** The
history of a profile lives in seven tables plus the broker's ledger, and two of
them are not reachable by any cascade: `sim_accounts.id` **is** the portfolio_id
but carries no `references`, so `sim_positions` and `sim_fills` hang off a row
that nothing points at. Doing this by hand leaves cash and simulated positions
behind, and the next cycle starts with a book that does not match the history —
the worst possible state, because everything looks fine.

**What it deliberately does not touch:** `profiles`, `agent_settings`,
`agent_settings_history`, `profile_universe` and `portfolios`. That is the whole
point of the tool: the experiment —its 41 parameters, its universe, its name, its
id— survives, and only what it has *done* goes away. Deleting the profile is
`delete_profile` and is a different operation.

It is used when a change to the rules makes the accumulated history
uninterpretable. That happened on 2026-08-10 with F9.9 and F9.10: the Risk
Manager started sizing and filtering with the commissions, so the cycles before
that point had been approved by a filter that ignored friction, and mixing the
two halves in one calibration measures neither.

    python tools/reset_experiment.py --profile eu-05-muy-agresivo --confirm eu-05-muy-agresivo

The name has to be repeated, and the reason is `delete_profile`'s: it is data that
took days to produce and there is no undo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Infra  # noqa: E402
from src.db import Database  # noqa: E402

#: History tables, in an order that would work even without the cascades.
#: `positions` before `orders` because it points at them, `orders` and
#: `risk_events` before `decisions`, and `decisions` before `cycles`.
HISTORY_TABLES = (
    "positions",
    "orders",
    "risk_events",
    "decisions",
    "equity_snapshots",
    "cycles",
)

#: The simulated broker's ledger. `sim_accounts` goes last: the other two cascade
#: off it, and deleting it first would work but hide what was removed.
LEDGER_TABLES = ("sim_positions", "sim_fills", "sim_accounts")

#: What must still be standing afterwards. Checked, not trusted: a reset that
#: quietly took the settings with it would look like a clean start and behave like
#: a different experiment.
PRESERVED_TABLES = (
    "profiles",
    "agent_settings",
    "agent_settings_history",
    "profile_universe",
    "portfolios",
)


def counts(db: Database, portfolio_id: str) -> dict[str, int]:
    """How many rows each table holds for this experiment."""
    out: dict[str, int] = {}
    for table in HISTORY_TABLES:
        out[table] = db.query(
            f"select count(1) as n from {table} where portfolio_id = ?", (portfolio_id,)
        )[0]["n"]
    out["sim_positions"] = db.query(
        "select count(1) as n from sim_positions where account_id = ?", (portfolio_id,)
    )[0]["n"]
    out["sim_fills"] = db.query(
        "select count(1) as n from sim_fills where account_id = ?", (portfolio_id,)
    )[0]["n"]
    out["sim_accounts"] = db.query(
        "select count(1) as n from sim_accounts where id = ?", (portfolio_id,)
    )[0]["n"]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Nombre del experimento.")
    parser.add_argument(
        "--confirm",
        default="",
        help="Repite el nombre del experimento. Sin esto no se borra nada.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Cuenta lo que se borraria y no borra nada.",
    )
    args = parser.parse_args()

    # `Infra` and not the full profile settings: this only needs to know where the
    # file is, and `resolve_settings` would refuse a profile whose universe no
    # longer validates — which has nothing to do with being able to wipe it.
    infra = Infra.load()

    with Database(path=infra.db_path) as db:
        profile = db.get_profile_by_name(args.profile)
        if profile is None:
            print(f"No hay ningun experimento llamado {args.profile!r}.")
            return 1
        portfolio_id = profile.get("portfolio_id")
        if not portfolio_id:
            print(f"{args.profile} no tiene cartera: no hay nada que resetear.")
            return 0

        before = counts(db, portfolio_id)
        total = sum(before.values())
        print(f"Experimento: {args.profile}  (cartera {portfolio_id})")
        for table, n in before.items():
            print(f"  {table:20} {n:6}")
        print(f"  {'TOTAL':20} {total:6}")

        if args.dry_run:
            print("\n--dry-run: no se ha borrado nada.")
            return 0

        if args.confirm != args.profile:
            print(
                f"\nNo se ha borrado nada. Repite el nombre para confirmar:\n"
                f"  --confirm {args.profile}"
            )
            return 1

        for table in HISTORY_TABLES:
            db.execute(f"delete from {table} where portfolio_id = ?", (portfolio_id,))
        db.execute("delete from sim_positions where account_id = ?", (portfolio_id,))
        db.execute("delete from sim_fills where account_id = ?", (portfolio_id,))
        # The account row goes too, cash included: `SimBroker` recreates it with
        # `initial_cash` on the next cycle, so resetting the cash by hand here
        # would be a second definition of the starting capital -- and the one that
        # would drift the day the budget changed in the profile.
        db.execute("delete from sim_accounts where id = ?", (portfolio_id,))

        after = counts(db, portfolio_id)
        leftovers = {t: n for t, n in after.items() if n}
        if leftovers:
            print(f"\n⚠️  Ha quedado algo sin borrar: {leftovers}")
            return 1

        for table in PRESERVED_TABLES:
            n = db.query(f"select count(1) as n from {table}")[0]["n"]
            if n == 0:
                print(f"\n⚠️  {table} ha quedado vacia y no debia tocarse.")
                return 1

        universe = len(db.get_profile_universe(profile["id"]))
        print(
            f"\nBorradas {total} filas. El experimento sigue en pie: "
            f"{universe} simbolos en el universo, parametros intactos.\n"
            f"El efectivo lo repone SimBroker en el proximo ciclo, con el "
            f"presupuesto del perfil."
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
