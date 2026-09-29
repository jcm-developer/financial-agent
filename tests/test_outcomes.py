"""Tests of the decision scorecard (F9.27).

`score` and `summarize` are pure and tested with hand-made bars; the database
half is tested once, end to end, on a real SQLite.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.db import Database
from src.outcomes import Bar, score, score_portfolio, summarize


def bars(*closes: float, start: date = date(2026, 9, 1)) -> list[Bar]:
    return [
        Bar(day=start + timedelta(days=i), high=c * 1.01, low=c * 0.99, close=c)
        for i, c in enumerate(closes)
    ]


def decision(**fields) -> dict:
    return {
        "id": "d", "kind": "entry", "action": "buy", "conviction": 60,
        "reference_price": 100.0, "suggested_stop": None, "suggested_target": None,
        "created_at": "2026-09-01T08:20:00+00:00", **fields,
    }


def test_a_buy_is_right_when_the_price_went_up():
    outcome = score(decision(), bars(101, 102, 103, 104, 105))

    assert outcome.returns[5] == 5.0 and outcome.right[5] is True
    assert outcome.returns[20] is None and outcome.right[20] is None
    # «Up to the last close» is there from the first session.
    assert score(decision(), bars(103)).returns[0] == 3.0


def test_staying_out_is_right_when_the_price_did_not_go_up():
    """The hold of a candidate claims the opposite of a buy, so the same move
    judges them opposite ways — the only reading that puts them on one scale."""
    falling = bars(99, 98, 97, 96, 95)

    assert score(decision(action="hold"), falling).right[5] is True
    assert score(decision(action="buy"), falling).right[5] is False


def test_keeping_a_position_claims_it_goes_up():
    rising = bars(101, 102, 103, 104, 105)

    assert score(decision(kind="exit", action="hold"), rising).right[5] is True
    assert score(decision(kind="exit", action="sell"), rising).right[5] is False


def test_a_bar_that_spans_both_levels_counts_as_the_stop():
    """A daily bar does not say which came first; the unkind reading is taken."""
    wide = [Bar(day=date(2026, 9, 1), high=130, low=80, close=100)]

    outcome = score(decision(suggested_stop=90, suggested_target=120), wide)

    assert outcome.first_touch == "stop"


def test_the_target_counts_when_it_comes_first():
    path = bars(105, 112, 121)

    outcome = score(decision(suggested_stop=90, suggested_target=120), path)

    assert outcome.first_touch == "target"


def test_the_summary_separates_the_groups_and_the_ladder():
    outcomes = [
        score(decision(id="a", conviction=72), bars(101, 102, 103, 104, 106)),
        score(decision(id="b", conviction=55), bars(99, 98, 97, 96, 94)),
        score(decision(id="c", action="hold", conviction=70), bars(99, 98, 97, 96, 95)),
        score(decision(id="d", action="hold", conviction=70), bars(101)),
    ]

    summary = summarize(outcomes)

    buy = next(g for g in summary["groups"] if g["action"] == "buy")
    assert buy["decisions"] == 2
    assert buy["horizons"][1] == {
        "sessions": 5, "judged": 2, "right": 1, "hit_rate_pct": 50.0, "avg_return_pct": 0.0,
    }
    hold = next(g for g in summary["groups"] if g["action"] == "hold")
    # One judged, one still waiting for its fifth session.
    assert hold["decisions"] == 2 and hold["horizons"][1]["judged"] == 1
    assert hold["horizons"][0]["judged"] == 2
    assert summary["judged"] == 3
    assert [(r["bucket"], r["action"]) for r in summary["ladder"]] == [
        (50, "buy"), (70, "buy"), (70, "hold"),
    ]


def test_the_book_is_scored_from_bar_cache_without_todays_open_bar(tmp_path):
    with Database(path=tmp_path / "t.db") as db:
        portfolio_id = db.ensure_portfolio(name="p", mode="paper", initial_budget=10_000)
        cycle_id = db.start_cycle(
            portfolio_id=portfolio_id, equity_start=10_000, cash_start=10_000,
            market_open=True, symbols=["SAP.DE"], llm_model="m", settings={},
        )
        db.execute(
            "insert into decisions (id, cycle_id, portfolio_id, symbol, kind, action, "
            "conviction, reference_price, created_at) values ('d1', ?, ?, 'SAP.DE', "
            "'entry', 'hold', 70, 100, '2026-09-01T08:20:00+00:00')",
            (cycle_id, portfolio_id),
        )
        for i, close in enumerate([99, 98, 97, 96, 95, 150]):
            db.execute(
                "insert into bar_cache (symbol, interval, ts, open, high, low, close) "
                "values ('SAP.DE', '1d', ?, ?, ?, ?, ?)",
                (f"2026-09-0{i + 1}T00:00:00+00:00", close, close, close, close),
            )

        # On the 6th the sixth bar is today's, still open, and is left out.
        [outcome] = score_portfolio(db, portfolio_id, today=date(2026, 9, 6))

    assert outcome.returns[5] == -5.0 and outcome.right[5] is True
