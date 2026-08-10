"""The cycle's log as a file the other container can read (F4.22).

The two things worth pinning down: the mirror **does not steal the console** —the
scheduler's `docker compose logs -f` has to keep working, and it is what was used
to follow a cycle before there was a screen— and reading the tail never raises,
because it is read on every pass of the SSE.
"""

from __future__ import annotations

import logging
import sys

from src import cycle_log


def test_the_mirror_writes_to_the_file_without_taking_the_console(tmp_path, capsys):
    db_path = tmp_path / "trading.db"

    with cycle_log.capture(db_path) as path:
        print("Ciclo abc iniciado.")
        print("Evaluando 3 candidatos a entrada.")

    assert path == tmp_path / "cycle.log"
    assert cycle_log.read_tail(db_path, 400) == [
        "Ciclo abc iniciado.",
        "Evaluando 3 candidatos a entrada.",
    ]
    # Still on stdout: whoever launched the cycle from a terminal keeps seeing it.
    assert "Evaluando 3 candidatos" in capsys.readouterr().out


def test_the_log_is_mirrored_too_and_not_only_the_prints(tmp_path):
    """The order `run.py` depends on: a handler built **inside** the capture holds
    the mirror, which is why `setup_logging` is called in there and not before."""
    db_path = tmp_path / "trading.db"

    with cycle_log.capture(db_path):
        handler = logging.StreamHandler(sys.stdout)
        log = logging.getLogger("test_cycle_log")
        log.addHandler(handler)
        log.warning("KILL SWITCH activado.")
        log.removeHandler(handler)

    assert cycle_log.read_tail(db_path, 400) == ["KILL SWITCH activado."]


def test_every_start_empties_the_log(tmp_path):
    """A cycle's log is worth as long as the cycle: keeping the previous one would
    show it as if it were the new one's."""
    db_path = tmp_path / "trading.db"
    with cycle_log.capture(db_path):
        print("ciclo viejo")

    with cycle_log.capture(db_path):
        print("ciclo nuevo")

    assert cycle_log.read_tail(db_path, 400) == ["ciclo nuevo"]


def test_only_the_last_lines_are_read(tmp_path):
    db_path = tmp_path / "trading.db"
    with cycle_log.capture(db_path):
        for index in range(50):
            print(f"linea {index}")

    assert cycle_log.read_tail(db_path, 3) == ["linea 47", "linea 48", "linea 49"]


def test_with_no_log_the_tail_is_empty_and_does_not_raise(tmp_path):
    """The volume of an experiment that has not run a cycle yet. The panel already
    knows how to show nothing; an exception here would take the stream down."""
    assert cycle_log.read_tail(tmp_path / "trading.db", 400) == []


def test_a_huge_log_is_read_from_the_end(tmp_path, monkeypatch):
    """A cycle in DEBUG writes megabytes and this is read every two seconds per
    connection, so the file is seeked into instead of loaded whole."""
    db_path = tmp_path / "trading.db"
    monkeypatch.setattr(cycle_log, "MAX_TAIL_BYTES", 200)
    cycle_log.path_for(db_path).write_text(
        "\n".join(f"linea {index:04d}" for index in range(500)), encoding="utf-8"
    )

    tail = cycle_log.read_tail(db_path, 400)

    assert tail[-1] == "linea 0499"
    # Way below the 400 asked for: what got read is the last 200 bytes.
    assert len(tail) < 30


def test_truncating_a_log_that_is_not_there_is_not_an_error(tmp_path):
    """`api/runner.py` empties it before launching, and on a fresh volume there is
    nothing to empty."""
    cycle_log.truncate(tmp_path / "trading.db")

    assert cycle_log.read_tail(tmp_path / "trading.db", 400) == []
