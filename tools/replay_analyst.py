#!/usr/bin/env python
"""Replays the analyst over the recorded snapshots with daily indicators (F9.15).

**What it measures.** The profile runs on `bar_interval=1h`, so the `atr_14` the
analyst reads is the range of 14 *hours*, not of 14 *days* — some four times
smaller. The targets it proposes scale off that number (median 5,6x the ATR it is
handed), so a short target could mean either "the model is timid" or "the model
was given a small ruler". Those two are not the same finding and they call for
different fixes, which is why this exists: it asks the same model about the same
moments with **one variable changed** — the indicator bundle, rebuilt on daily
bars — and compares the distribution of `suggested_target` and `suggested_stop`.

**Why it can be offline.** `bar_cache` already holds the daily bars of the whole
universe (the screener always sifts on `1d`, see `universe_data.py`) and the
hourly ones of everything that reached the model, so both arms are rebuilt from
what is already stored. Nothing is downloaded from Yahoo and nothing is written:
the database is opened `read_only=True`, which is a SQLite-level guarantee and
not a promise about the SQL below.

**Why both arms are re-run, instead of comparing against the record.** The
hourly arm already exists in `decisions`, but it was produced with a different
account context and at a temperature that is not zero, so a difference against it
would mix three causes. Re-running both in the same session, with the same
neutral account, leaves exactly one difference between them. The record is still
reported — as a third column — because it is what actually traded.

**The account context is neutral and identical in both arms**, and that is a
deliberate simplification: it changes the prompt's "posiciones ya abiertas" line
and the ceiling `suggested_weight_pct` is measured against, neither of which is
what is being compared. Reconstructing the true book at each of the 200 snapshots
would add a second moving part to a measurement whose whole value is having one.

**The deterministic half needs no model at all** (`--no-llm`), and it is the half
F9.14 mostly rests on: the ATR ratio between the two intervals, the stop
`risk.py` would place with each, and the sigma check — stop and target expressed
in daily standard deviations over `horizon_days`. With a single closed trade the
hit rate says nothing and F5.7's calibration chart says nothing, but that
arithmetic can be done today.

    python tools/replay_analyst.py --no-llm
    python tools/replay_analyst.py --limit 40 --workers 3 --out replay.json

⚠️ **It shares the model quota with the scheduler, and the binding limit is not
the one R8 wrote down.** R8 counted 40 requests per minute against the 1-2 a
cycle asks for, and concluded there was room. Measured on 2026-08-11 with four
workers and a cycle in flight, NIM's free tier answered `503 ResourceExhausted:
Worker local total request limit reached (23/16)` and then a wall of 429s: what
is capped is **concurrent** requests to the shared worker, not the rate. Seven of
the first fifteen symbols came back with no analysis at all — and, worse, the
running cycle was competing for the same slots.

So the default is **one worker**, which is what a cycle itself does, and raising
it is a decision to take with the market closed and no cycle running. Serial, the
replay is about 30-40 seconds per call: an arm of 40 snapshots is roughly half an
hour, and `--limit` is there to say how much measurement is worth that wait.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import fees, market_calendar  # noqa: E402
from src.analyst import Analyst  # noqa: E402
from src.config import Infra, Settings  # noqa: E402
from src.db import Database  # noqa: E402
from src.indicators import Bar  # noqa: E402
from src.llm import LLMClient  # noqa: E402
from src.market_data import build_snapshot  # noqa: E402
from src.models import AccountState, MarketSnapshot  # noqa: E402
from src.profile_settings import resolve_settings, select_profile  # noqa: E402

#: Sessions in a year. It is the same constant `indicators.annualized_volatility`
#: uses to annualise, and it is needed here to undo that: what a horizon of N
#: days has to be measured against is the **daily** sigma.
TRADING_DAYS = 252.0

#: Floor on the bars read per symbol, mirroring `universe_data.fetch_snapshots`:
#: it reads `max(lookback_days, 260)` from the cache, and the replay has to read
#: the same or it is not rebuilding the same snapshot.
#:
#: It changes exactly one number, and that number reaches the model: every
#: indicator converges —MACD's EMA seed is worth (11/13)^140, which is nothing—
#: but `bars_available` is in the bundle, so reading 400 would have told the
#: hourly arm it had 399 bars where the cycle told it 259. Measured, and it is
#: the only difference the extra bars make.
BARS_FLOOR = 260

#: Indicators compared between the rebuilt hourly bundle and the recorded one, to
#: prove the reconstruction is faithful before anything is concluded from the
#: daily one. If these match, the daily rebuild follows the same code path with
#: the same inputs and can be trusted; if they do not, the whole report is a
#: measurement of a bug in this file.
FIDELITY_KEYS = ("price", "atr_14", "rsi_14", "sma_20", "sma_50", "bars_available")

#: Tolerance of that comparison, relative. Not zero because the cache's last bar
#: is rewritten on every cycle while the market is open (`bar_cache` uses
#: `insert or replace`), so a snapshot taken mid-session was computed over a bar
#: that has since been corrected. A drift of more than this is not that.
FIDELITY_TOLERANCE = 0.005

#: One step of each interval. It is what turns a snapshot's `as_of` back into the
#: moment the cycle ran, which is the filter both arms are rebuilt with.
RUN_STEP = {"1h": timedelta(hours=1), "1d": timedelta(days=1)}


# ----------------------------------------------------------------------
# Lectura de lo ya registrado
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class RecordedDecision:
    """One entry decision with the snapshot the model saw when it took it."""

    decision_id: str
    cycle_id: str
    symbol: str
    as_of: str
    action: str
    conviction: int
    horizon_days: int | None
    suggested_stop: float | None
    suggested_target: float | None
    reference_price: float
    indicators: dict[str, Any]


def load_decisions(db: Database, portfolio_id: str, *, limit: int = 0
                   ) -> list[RecordedDecision]:
    """The entry decisions that carry a snapshot, newest first.

    Only `kind='entry'`: the target and the stop are entry fields, and the exit
    review answers a different prompt with a different question. A decision whose
    snapshot could not be saved is skipped rather than replayed against a
    rebuilt-from-nothing context.
    """
    rows = db.query(
        "select d.id, d.cycle_id, d.symbol, d.action, d.conviction, d.horizon_days, "
        "       d.suggested_stop, d.suggested_target, d.reference_price, "
        "       s.as_of, s.indicators_json "
        "from decisions d join market_snapshots s on s.id = d.snapshot_id "
        "where d.portfolio_id = ? and d.kind = 'entry' "
        "order by d.created_at desc, d.id desc",
        (portfolio_id,),
    )
    decisions = [
        RecordedDecision(
            decision_id=str(row["id"]),
            cycle_id=str(row["cycle_id"]),
            symbol=str(row["symbol"]),
            as_of=str(row["as_of"]),
            action=str(row["action"]),
            conviction=int(row["conviction"] or 0),
            horizon_days=_opt_int(row["horizon_days"]),
            suggested_stop=_opt_float(row["suggested_stop"]),
            suggested_target=_opt_float(row["suggested_target"]),
            reference_price=float(row["reference_price"] or 0.0),
            indicators=json.loads(row["indicators_json"] or "{}"),
        )
        for row in rows
    ]
    return decisions[:limit] if limit else decisions


def cache_moment(as_of: str, run_interval: str) -> str:
    """The instant the cache held, from the `as_of` a snapshot recorded.

    ⚠️ **They are not the same instant, and taking them for one is an off-by-one
    that quietly moves every indicator back a bar.** `as_of` is the timestamp of
    the *decision* bar, and `build_snapshot` reserves the bar **after** it as the
    execution price — so the cache at that moment already held one bar more than
    `as_of` names. Filtering by `as_of` and rebuilding would hand `build_snapshot`
    the decision bar as its execution bar and decide on the one before: the
    fidelity check caught exactly that, with RSI drifting up to 54 %.
    """
    return (datetime.fromisoformat(as_of) + RUN_STEP[run_interval]).isoformat()


def bars_to_read(settings: Settings) -> int:
    """How many bars the funnel would have read. Same expression as it uses."""
    return max(settings.lookback_days, BARS_FLOOR)


def bars_until(db: Database, symbol: str, interval: str, moment: str,
               limit: int = BARS_FLOOR) -> list[Bar]:
    """A symbol's bars up to `moment`, oldest to newest.

    It queries `bar_cache` directly instead of going through `BarCache.get_bars`
    because that class has no point-in-time read, and adding one for an offline
    tool would put a parameter in the live path that nothing else uses. The SQL is
    otherwise the same.
    """
    rows = db.query(
        "select ts, open, high, low, close, volume from bar_cache "
        "where symbol = ? and interval = ? and ts <= ? "
        "order by ts desc limit ?",
        (symbol, interval, moment, limit),
    )
    return [
        Bar(
            timestamp=datetime.fromisoformat(row["ts"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
        )
        for row in reversed(rows)
    ]


def rebuild_snapshot(
    db: Database, symbol: str, moment: str, interval: str,
    limit: int = BARS_FLOOR,
) -> MarketSnapshot | None:
    """The snapshot the analyst would have got at `moment` on `interval` bars.

    It goes through `market_data.build_snapshot`, the same function the cycle
    uses, so the reserved execution bar, the 60-bar floor and the shape of
    `recent_bars` are not reimplemented here — a replay that computed its
    indicators its own way would be comparing this file against the system, not
    one interval against the other.

    **The daily bar of the day in progress is included, and it is right that it
    is.** At 12:00 the cache already holds a half-formed bar for that session;
    `build_snapshot` reserves the last bar as the execution price and decides on
    the one before, which is exactly what a cycle running on daily bars would have
    done at that same moment.
    """
    bars = bars_until(db, symbol, interval, moment, limit)
    if len(bars) < 2:
        return None
    return build_snapshot(symbol, bars)


# ----------------------------------------------------------------------
# La aritmetica que no necesita modelo
# ----------------------------------------------------------------------

def daily_sigma_pct(daily_indicators: dict[str, Any]) -> float | None:
    """One day's standard deviation, in % of price.

    Taken from `volatility_20d_pct` of the **daily** bundle, which is the
    annualised standard deviation of 20 daily returns, and un-annualised by
    dividing by sqrt(252). It is a real sigma, unlike the ATR, which is a mean
    range and runs some 20-40 % wider for the same series.

    ⚠️ It must come from the daily bundle even when judging an hourly decision.
    `annualized_volatility` multiplies by sqrt(252) whatever the interval, so on
    hourly bars it returns the standard deviation of 20 *hours* annualised as if
    they were days — about sqrt(8) too small. That is a second unit inherited
    from the daily design, and this function is where not to trip on it.
    """
    annual = _opt_float(daily_indicators.get("volatility_20d_pct"))
    if annual is None or annual <= 0:
        return None
    return annual / (TRADING_DAYS ** 0.5)


def sigmas(move_pct: float | None, sigma_daily: float | None,
           horizon_days: int | None) -> float | None:
    """A price move expressed in daily sigmas over its horizon.

    The scaling is the random walk's: the standard deviation over `t` days is
    sigma * sqrt(t). It is what turns "+3,5 %" into a number that can be compared
    with a horizon — a 3,5 % move is ambitious in two days and noise in twenty.

    @param move_pct: Distance to the level, in % of price. Sign is ignored: what
        is being asked is size, not direction.
    @param sigma_daily: One day's standard deviation, in % of price.
    @param horizon_days: Days the position is expected to be held.
    @return: The distance in sigmas, or None if anything is missing.
    """
    if not move_pct or not sigma_daily or not horizon_days or horizon_days <= 0:
        return None
    return abs(move_pct) / (sigma_daily * (horizon_days ** 0.5))


def pct_from(price: float, level: float | None) -> float | None:
    """`level` as a percentage away from `price`, signed."""
    if not level or price <= 0:
        return None
    return (level / price - 1.0) * 100.0


# ----------------------------------------------------------------------
# El replay
# ----------------------------------------------------------------------

@dataclass
class Arm:
    """One side of the comparison: an interval and the analyst that reads it."""

    key: str
    label: str
    interval: str
    analyst: Analyst | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    #: Snapshots the model gave no usable answer for. Counted and printed rather
    #: than left implicit: a distribution with holes looks exactly like a smaller
    #: sample, and the holes are not random — they are whichever symbols happened
    #: to run while the free tier was saturated.
    failures: int = 0


def build_analyst(settings: Settings, llm: LLMClient, interval: str) -> Analyst:
    """An analyst wired exactly as `cycle.py` wires it, minus the broker.

    The commission is rebuilt from `fees` plus the profile's surcharge instead of
    asking `SimBroker`, because instantiating the broker writes its account row —
    and this tool does not write.
    """
    market = market_calendar.get_market(settings.market)
    commission_for: Callable[[str], float] = (
        lambda symbol: fees.standard_commission(symbol) + settings.sim_commission
    )
    return Analyst(
        llm,
        interval=interval,
        currency=market.currency,
        commission_for=commission_for,
        max_position_pct=settings.risk.max_position_pct,
    )


def neutral_account(settings: Settings) -> AccountState:
    """The book both arms are told about: the profile's budget, nothing open.

    Identical in both arms on purpose. See the module docstring.
    """
    budget = settings.initial_budget
    return AccountState(
        equity=budget, cash=budget, buying_power=budget, last_equity=budget,
    )


def replay_one(
    arm: Arm, snapshot: MarketSnapshot, account: AccountState,
    recorded: RecordedDecision, daily_sigma: float | None,
) -> dict[str, Any] | None:
    """Asks the model about one snapshot and reduces the answer to the figures."""
    assert arm.analyst is not None
    proposal = arm.analyst.evaluate_entry(snapshot, account)
    if proposal is None:
        return None
    return measure(
        arm=arm.key,
        symbol=recorded.symbol,
        as_of=recorded.as_of,
        price=snapshot.price,
        action=proposal.action,
        conviction=proposal.conviction,
        horizon_days=proposal.horizon_days,
        stop=proposal.suggested_stop,
        target=proposal.suggested_target,
        atr_pct=_opt_float(snapshot.indicators.get("atr_pct")),
        daily_sigma=daily_sigma,
    )


def measure(
    *, arm: str, symbol: str, as_of: str, price: float, action: str,
    conviction: int, horizon_days: int | None, stop: float | None,
    target: float | None, atr_pct: float | None, daily_sigma: float | None,
) -> dict[str, Any]:
    """One row of the comparison, with the derived figures already in it."""
    target_pct = pct_from(price, target)
    stop_pct = pct_from(price, stop)
    return {
        "arm": arm,
        "symbol": symbol,
        "as_of": as_of,
        "price": round(price, 4),
        "action": action,
        "conviction": conviction,
        "horizon_days": horizon_days,
        "stop": stop,
        "target": target,
        "atr_pct": atr_pct,
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        # How many ATRs of its own interval the target is: the multiplier the
        # model applies to the ruler it was handed. This is the number that says
        # whether the model is timid or the ruler is short.
        "target_over_atr": (
            target_pct / atr_pct if target_pct and atr_pct else None
        ),
        "daily_sigma_pct": daily_sigma,
        "target_sigmas": sigmas(target_pct, daily_sigma, horizon_days),
        "stop_sigmas": sigmas(stop_pct, daily_sigma, horizon_days),
    }


def run_replay(
    db: Database, settings: Settings, decisions: list[RecordedDecision],
    *, arms: list[Arm], workers: int, log: Callable[[str], None],
) -> dict[str, Any]:
    """Rebuilds every snapshot and, if there are arms with a model, asks it.

    The deterministic part runs first and complete: it is what `--no-llm`
    returns, and it is also what the model part needs — the daily sigma of each
    moment is what turns an answer into sigmas.
    """
    account = neutral_account(settings)
    prepared: list[dict[str, Any]] = []
    fidelity: list[dict[str, Any]] = []
    skipped: list[str] = []

    limit = bars_to_read(settings)
    for recorded in decisions:
        moment = cache_moment(recorded.as_of, settings.bar_interval)
        daily = rebuild_snapshot(db, recorded.symbol, moment, "1d", limit)
        hourly = rebuild_snapshot(db, recorded.symbol, moment, "1h", limit)
        if daily is None or hourly is None:
            missing = "1d" if daily is None else "1h"
            skipped.append(f"{recorded.symbol}@{recorded.as_of} (sin barras {missing})")
            continue

        sigma = daily_sigma_pct(daily.indicators)
        fidelity.append(check_fidelity(recorded, hourly))
        prepared.append({
            "recorded": recorded,
            "1d": daily,
            "1h": hourly,
            "daily_sigma": sigma,
            # The record's own figures, measured against the same daily sigma:
            # this is the column that actually traded.
            "record_row": measure(
                arm="registro",
                symbol=recorded.symbol,
                as_of=recorded.as_of,
                price=recorded.reference_price,
                action=recorded.action,
                conviction=recorded.conviction,
                horizon_days=recorded.horizon_days,
                stop=recorded.suggested_stop,
                target=recorded.suggested_target,
                atr_pct=_opt_float(recorded.indicators.get("atr_pct")),
                daily_sigma=sigma,
            ),
            # What the two intervals say about the same moment, with no model in
            # the middle. The ATR ratio and the stop are the whole of F9.14.
            "atr_pct_1h": _opt_float(hourly.indicators.get("atr_pct")),
            "atr_pct_1d": _opt_float(daily.indicators.get("atr_pct")),
            "vol_1h": _opt_float(hourly.indicators.get("volatility_20d_pct")),
            "vol_1d": _opt_float(daily.indicators.get("volatility_20d_pct")),
        })

    log(f"Reconstruidos {len(prepared)} snapshots de {len(decisions)} decisiones.")
    if skipped:
        log(f"Omitidos {len(skipped)}: {', '.join(skipped[:5])}"
            + (" ..." if len(skipped) > 5 else ""))

    model_arms = [arm for arm in arms if arm.analyst is not None]
    if model_arms and prepared:
        jobs = [
            (arm, item) for arm in model_arms for item in prepared
        ]
        log(f"Lanzando {len(jobs)} llamadas al modelo con {workers} hilos "
            f"({len(model_arms)} brazos x {len(prepared)} snapshots).")
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    replay_one, arm, item[arm.interval], account,
                    item["recorded"], item["daily_sigma"],
                )
                for arm, item in jobs
            ]
            for (arm, _item), future in zip(jobs, futures):
                row = future.result()
                done += 1
                if done % 20 == 0:
                    log(f"  {done}/{len(jobs)} llamadas")
                if row is None:
                    arm.failures += 1
                else:
                    arm.results.append(row)

    return {
        "prepared": prepared,
        "fidelity": fidelity,
        "skipped": skipped,
    }


def check_fidelity(recorded: RecordedDecision, rebuilt: MarketSnapshot
                   ) -> dict[str, Any]:
    """Compares the rebuilt hourly bundle against the one that was stored."""
    worst_key = ""
    worst_drift = 0.0
    for key in FIDELITY_KEYS:
        stored = _opt_float(recorded.indicators.get(key))
        fresh = _opt_float(rebuilt.indicators.get(key))
        if stored is None or fresh is None or stored == 0:
            continue
        drift = abs(fresh / stored - 1.0)
        if drift > worst_drift:
            worst_key, worst_drift = key, drift
    return {
        "symbol": recorded.symbol,
        "as_of": recorded.as_of,
        "worst_key": worst_key,
        "worst_drift": worst_drift,
        "ok": worst_drift <= FIDELITY_TOLERANCE,
    }


# ----------------------------------------------------------------------
# El informe
# ----------------------------------------------------------------------

def summary(values: list[float]) -> dict[str, float | int] | None:
    """n, extremes, quartiles and mean of a series. None if it is empty."""
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    return {
        "n": len(clean),
        "min": clean[0],
        "p25": _percentile(clean, 0.25),
        "median": statistics.median(clean),
        "p75": _percentile(clean, 0.75),
        "max": clean[-1],
        "mean": statistics.fmean(clean),
    }


def _percentile(ordered: list[float], fraction: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _line(label: str, stats: dict[str, float | int] | None, unit: str = "%") -> str:
    if stats is None:
        return f"  {label:28} (sin datos)"
    return (
        f"  {label:28} n={stats['n']:>3}  "
        f"min {stats['min']:+.2f}{unit}  p25 {stats['p25']:+.2f}{unit}  "
        f"mediana {stats['median']:+.2f}{unit}  p75 {stats['p75']:+.2f}{unit}  "
        f"max {stats['max']:+.2f}{unit}"
    )


def _pick(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [row[key] for row in rows if row.get(key) is not None]


def report(
    profile_name: str, db_path: str, settings: Settings,
    decisions: list[RecordedDecision], prepared: list[dict[str, Any]],
    fidelity: list[dict[str, Any]], arms: list[Arm],
) -> tuple[str, dict[str, Any]]:
    """The text a person reads, and the same thing as JSON."""
    out: list[str] = []
    data: dict[str, Any] = {"profile": profile_name, "db": db_path}
    add = out.append

    stop_multiple = settings.risk.stop_atr_multiple
    record_rows = [item["record_row"] for item in prepared]

    add("=" * 78)
    add("F9.15 — Replay del analista con indicadores diarios")
    add(f"Experimento: {profile_name}   Base: {db_path} (solo lectura)")
    add(f"Intervalo en marcha: {settings.bar_interval}   "
        f"Stop del perfil: {stop_multiple:g}x ATR   "
        f"R/R minimo: {settings.risk.min_reward_risk:g}")
    add(f"Decisiones de entrada con snapshot: {len(decisions)}   "
        f"Reconstruidas: {len(prepared)}")
    add("=" * 78)

    # -- 1. Fidelidad --------------------------------------------------
    bad = [f for f in fidelity if not f["ok"]]
    worst = max((f["worst_drift"] for f in fidelity), default=0.0)
    add("")
    add("1. FIDELIDAD DE LA RECONSTRUCCION  (sin modelo)")
    add("   Rehacer el bundle horario desde bar_cache tiene que devolver lo que se")
    add("   guardo en market_snapshots. Si no, lo que mide este informe es un fallo")
    add("   de este fichero y no del intervalo.")
    add(f"  snapshots comparados       {len(fidelity)}")
    add(f"  fuera de tolerancia        {len(bad)}  (tolerancia {FIDELITY_TOLERANCE:.1%})")
    add(f"  desviacion maxima          {worst:.4%}")
    for item in bad[:5]:
        add(f"    ⚠️  {item['symbol']}@{item['as_of']}: "
            f"{item['worst_key']} desvia {item['worst_drift']:.2%}")
    data["fidelity"] = {"compared": len(fidelity), "failed": len(bad), "worst": worst}

    # -- 2. El ATR ------------------------------------------------------
    atr_1h = summary([item["atr_pct_1h"] for item in prepared])
    atr_1d = summary([item["atr_pct_1d"] for item in prepared])
    ratios = [
        item["atr_pct_1d"] / item["atr_pct_1h"]
        for item in prepared
        if item["atr_pct_1h"] and item["atr_pct_1d"]
    ]
    ratio = summary(ratios)
    add("")
    add("2. EL ATR, HORARIO CONTRA DIARIO  (sin modelo)")
    add(_line("atr_pct sobre barras 1h", atr_1h))
    add(_line("atr_pct sobre barras 1d", atr_1d))
    add(_line("ratio diario/horario", ratio, unit="x"))
    data["atr"] = {"hourly": atr_1h, "daily": atr_1d, "ratio": ratio}

    # -- 3. El stop determinista ----------------------------------------
    stop_1h = summary([
        -stop_multiple * item["atr_pct_1h"] for item in prepared if item["atr_pct_1h"]
    ])
    stop_1d = summary([
        -stop_multiple * item["atr_pct_1d"] for item in prepared if item["atr_pct_1d"]
    ])
    add("")
    add(f"3. EL STOP QUE PONDRIA risk.py  ({stop_multiple:g}x ATR, sin modelo)")
    add(_line("con el ATR horario", stop_1h))
    add(_line("con el ATR diario", stop_1d))
    data["deterministic_stop"] = {"hourly": stop_1h, "daily": stop_1d}

    # -- 4. La volatilidad, que tambien viene mal escalada ---------------
    vol_1h = summary([item["vol_1h"] for item in prepared])
    vol_1d = summary([item["vol_1d"] for item in prepared])
    add("")
    add("4. volatility_20d_pct, LA OTRA UNIDAD HEREDADA  (sin modelo)")
    add("   annualized_volatility multiplica por raiz(252) sea cual sea el intervalo,")
    add("   asi que sobre barras horarias anualiza 20 HORAS como si fueran 20 dias.")
    add(_line("sobre barras 1h", vol_1h))
    add(_line("sobre barras 1d", vol_1d))
    data["volatility"] = {"hourly": vol_1h, "daily": vol_1d}

    # -- 5. Lo que propuso el modelo ------------------------------------
    add("")
    add("5. LO QUE PROPONE EL MODELO")
    columns: list[tuple[str, list[dict[str, Any]], int]] = [
        ("registro", record_rows, 0)
    ]
    columns += [(arm.label, arm.results, arm.failures) for arm in arms if arm.results]
    data["arms"] = {}
    for label, rows, failures in columns:
        buys = [row for row in rows if row["action"] == "buy"]
        holes = f", {failures} sin respuesta" if failures else ""
        add("")
        add(f"  --- {label}  ({len(rows)} respuestas, {len(buys)} compras{holes}) ---")
        add(_line("objetivo, % sobre precio", summary(_pick(buys, "target_pct"))))
        add(_line("stop, % sobre precio", summary(_pick(buys, "stop_pct"))))
        add(_line("objetivo / ATR del brazo", summary(_pick(buys, "target_over_atr")),
                 unit="x"))
        add(_line("conviccion", summary([float(r["conviction"]) for r in buys]),
                 unit=""))
        add(_line("horizon_days", summary(
            [float(r["horizon_days"]) for r in buys if r["horizon_days"]]), unit=""))
        data["arms"][label] = {
            "responses": len(rows),
            "buys": len(buys),
            "no_answer": failures,
            "target_pct": summary(_pick(buys, "target_pct")),
            "stop_pct": summary(_pick(buys, "stop_pct")),
            "target_over_atr": summary(_pick(buys, "target_over_atr")),
            "conviction": summary([float(r["conviction"]) for r in buys]),
            "horizon_days": summary(
                [float(r["horizon_days"]) for r in buys if r["horizon_days"]]),
        }

    # -- 6. Sigmas ------------------------------------------------------
    add("")
    add("6. STOP Y OBJETIVO EN SIGMAS DIARIAS SOBRE horizon_days")
    add("   sigma = volatilidad diaria del activo; la distancia se escala por")
    add("   raiz(horizon_days). Por debajo de 0,5 sigma el nivel esta dentro del")
    add("   ruido del horizonte: se alcanza por azar, no por la tesis.")
    data["sigmas"] = {}
    for label, rows, _failures in columns:
        buys = [row for row in rows if row["action"] == "buy"]
        target_sigmas = _pick(buys, "target_sigmas")
        stop_sigmas = _pick(buys, "stop_sigmas")
        under = sum(
            1 for row in buys
            if row.get("target_sigmas") is not None
            and row.get("stop_sigmas") is not None
            and row["target_sigmas"] < 0.5 and abs(row["stop_sigmas"]) < 0.5
        )
        add("")
        add(f"  --- {label} ---")
        add(_line("objetivo, en sigmas", summary(target_sigmas), unit="s"))
        add(_line("stop, en sigmas", summary([abs(v) for v in stop_sigmas]), unit="s"))
        add(f"  {'los dos bajo 0,5 sigma':28} {under}/{len(buys)}")
        data["sigmas"][label] = {
            "target": summary(target_sigmas),
            "stop": summary([abs(v) for v in stop_sigmas]),
            "both_under_half": under,
            "buys": len(buys),
        }

    add("")
    add("=" * 78)
    return "\n".join(out), data


# ----------------------------------------------------------------------

def _opt_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="", help="Nombre del experimento.")
    parser.add_argument("--db", default="", help="Ruta de la base. Por defecto, DB_PATH.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Replay solo de las N decisiones mas recientes.")
    parser.add_argument("--workers", type=int, default=1,
                        help="Llamadas al modelo en paralelo. Por defecto 1, que es "
                             "lo que hace un ciclo: el tier gratuito de NIM limita "
                             "peticiones simultaneas, no por minuto.")
    parser.add_argument("--retries", type=int, default=6,
                        help="Reintentos por llamada. Mas que en un ciclo a "
                             "proposito: aqui esperar es gratis y perder una "
                             "muestra sesga la distribucion que se esta midiendo.")
    parser.add_argument("--no-llm", action="store_true",
                        help="Solo la parte determinista: ATR, stop y sigmas.")
    parser.add_argument("--out", default="", help="Fichero JSON con el informe.")
    args = parser.parse_args()

    infra = Infra.load()
    db_path = args.db or infra.db_path

    # `read_only=True` is the guarantee, not the comment: the experiment is live
    # and a replay that could write would be a second writer on the same book.
    with Database(path=db_path, read_only=True) as db:
        profile_id = select_profile(db, name=args.profile)
        profile = db.get_profile(profile_id)
        assert profile is not None
        portfolio_id = profile.get("portfolio_id")
        if not portfolio_id:
            print(f"{profile['name']} no tiene cartera: no hay nada que reejecutar.")
            return 1

        settings = resolve_settings(db, profile_id, infra=infra)
        decisions = load_decisions(db, str(portfolio_id), limit=args.limit)
        if not decisions:
            print("No hay decisiones de entrada con snapshot que reejecutar.")
            return 1

        running = db.query(
            "select count(1) as n from cycles where portfolio_id = ? and status = 'running'",
            (portfolio_id,),
        )[0]["n"]
        if running and not args.no_llm and args.workers > 1:
            print(f"⚠️  Hay {running} ciclo(s) en marcha y pides {args.workers} hilos. "
                  f"El limite del tier gratuito son peticiones SIMULTANEAS, no por "
                  f"minuto: con 4 hilos y un ciclo en vuelo devolvio 503 y se quedaron "
                  f"sin analisis 7 de 15 simbolos, los del replay y los del ciclo. "
                  f"Con el experimento corriendo, --workers 1.\n")

        arms = [
            Arm("1h", "reejecutado sobre 1h", "1h"),
            Arm("1d", "reejecutado sobre 1d", "1d"),
        ]
        llm: LLMClient | None = None
        try:
            if not args.no_llm:
                llm = LLMClient(
                    api_key=settings.model_api_key,
                    model=settings.llm_model,
                    provider=settings.llm_provider,
                    base_url=settings.model_base_url,
                    temperature=settings.llm_temperature,
                    timeout=settings.llm_timeout_seconds,
                    # The profile's `llm_max_retries` is tuned for a cycle, where
                    # giving up on a symbol costs one analysis out of twenty-five
                    # and waiting delays the next candidate. Here the trade is the
                    # other way round: a lost sample does not just go missing, it
                    # leaves the distribution with a hole nobody can see.
                    max_retries=max(1, args.retries),
                )
                for arm in arms:
                    arm.analyst = build_analyst(settings, llm, arm.interval)

            outcome = run_replay(
                db, settings, decisions,
                arms=arms, workers=max(1, args.workers), log=print,
            )
        finally:
            if llm is not None:
                llm.close()

        text, data = report(
            str(profile["name"]), str(db_path), settings, decisions,
            outcome["prepared"], outcome["fidelity"], arms,
        )
        print(text)

        if args.out:
            data["rows"] = (
                [item["record_row"] for item in outcome["prepared"]]
                + [row for arm in arms for row in arm.results]
            )
            Path(args.out).write_text(
                json.dumps(data, ensure_ascii=False, indent=1, default=str),
                encoding="utf-8",
            )
            print(f"Informe en {args.out}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
