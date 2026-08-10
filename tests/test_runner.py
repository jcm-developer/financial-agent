"""The cycle runner's state, without launching a subprocess (F4.19 / F4.21).

`with_external` is the piece that decides what the panel says, and it is tested
here rather than through the endpoint because **three** callers share it and what
matters is the shape, not the route.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api.runner import EXTERNAL_STAGE, CycleRunner, with_external
from src import cycle_log, stop_signal


def _running_cycle(minutes_ago: float = 2.0) -> dict:
    started = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {"id": "ciclo-1", "started_at": started.isoformat(), "profile": "europa-01"}


def test_a_scheduler_cycle_brings_its_experiment_and_its_clock(tmp_path):
    """What the panel could not say before: it read "en marcha" without saying
    which experiment nor since when, because the runner does not know — the row
    does, and it was already being read to find out there was one at all."""
    runner = CycleRunner(db_path=str(tmp_path / "trading.db"))

    state = with_external(runner.status(), _running_cycle())

    assert state["external"] is True
    # Not `running`: this process has no subprocess of its own.
    assert state["running"] is False
    assert state["stage"] == EXTERNAL_STAGE
    assert state["profile"] == "europa-01"
    assert state["elapsed_seconds"] >= 110
    # Nothing of ours finished: reporting a return code beside a live cycle would
    # read as this one having ended.
    assert state["finished_at"] is None
    assert state["returncode"] is None


def test_with_nothing_running_the_state_is_left_alone(tmp_path):
    runner = CycleRunner(db_path=str(tmp_path / "trading.db"))

    state = with_external(runner.status(), None)

    assert state.get("external", False) is False
    assert state["stage"] == "inactivo"
    assert state["elapsed_seconds"] is None


def test_a_leftover_stop_request_is_not_reported_with_nothing_running(tmp_path):
    """A Parar pressed a second after the cycle finished leaves the file behind
    until the next cycle clears it. Reported as it is, the panel would light up
    "parada pedida" for ever, over nothing."""
    db_path = str(tmp_path / "trading.db")
    stop_signal.request(db_path, "ciclo-1")
    runner = CycleRunner(db_path=db_path)

    assert runner.status()["stop_requested"] is True
    assert with_external(runner.status(), None)["stop_requested"] is False
    # With a cycle running it is reported, which is the whole point of the field.
    assert with_external(runner.status(), _running_cycle())["stop_requested"] is True


def test_the_log_comes_from_the_file_and_not_from_a_pipe(tmp_path):
    """F4.22: the lines are read from the shared volume, so the panel shows the log
    of a cycle this process never launched."""
    db_path = str(tmp_path / "trading.db")
    cycle_log.path_for(db_path).write_text("uno\ndos\ntres\n", encoding="utf-8")
    runner = CycleRunner(db_path=db_path)

    assert runner.status()["lines"] == ["uno", "dos", "tres"]
    assert runner.lines_since(1) == (3, ["dos", "tres"])
    # A client further along than the file —it was truncated by a new cycle— gets
    # nothing rather than an error: losing old lines of a live log is acceptable.
    assert runner.lines_since(99) == (3, [])


def test_stopping_a_registered_cycle_writes_the_request(tmp_path):
    db_path = str(tmp_path / "trading.db")
    runner = CycleRunner(db_path=db_path)

    ok, message = runner.stop(cycle_id="ciclo-1")

    assert ok is True
    assert "punto de control" in message
    assert stop_signal.pending(db_path) == "ciclo-1"


def test_stopping_with_nothing_registered_and_nothing_of_ours_says_so(tmp_path):
    """No id and no subprocess: there is nothing here to stop, and saying so is
    better than a button that reports a success nobody got."""
    runner = CycleRunner(db_path=str(tmp_path / "trading.db"))

    ok, message = runner.stop(cycle_id=None)

    assert ok is False
    assert "No hay ningun ciclo en marcha" in message
