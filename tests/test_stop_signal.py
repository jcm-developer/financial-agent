"""The stop request that travels between containers (F4.21).

What matters here is not that a file gets written —that is one line— but the two
decisions around it: **the request names a cycle**, so one that arrives late
cannot stop the next one, and **nothing here raises** on a read, because the
reader is a cycle holding open positions.
"""

from __future__ import annotations

from src import stop_signal


def test_a_request_lands_next_to_the_database(tmp_path):
    """The shared directory, not a configurable path: it is what makes the API's
    file the same one the scheduler's cycle reads."""
    db_path = tmp_path / "trading.db"

    stop_signal.request(db_path, "ciclo-1")

    assert stop_signal.path_for(db_path) == tmp_path / "stop.request"
    assert stop_signal.pending(db_path) == "ciclo-1"


def test_a_request_is_only_honoured_for_the_cycle_it_names(tmp_path):
    """The reason it carries an id and not a bare flag.

    A Parar pressed a second after the cycle finished leaves the file behind. With
    a flag, the next cycle —the scheduler's, hours later— would stop on its own
    and it would read as a skipped session.
    """
    db_path = tmp_path / "trading.db"
    stop_signal.request(db_path, "ciclo-1")

    assert stop_signal.requested_for(db_path, "ciclo-1") is True
    assert stop_signal.requested_for(db_path, "ciclo-2") is False


def test_with_no_request_nothing_is_pending_and_nothing_fails(tmp_path):
    """A missing file is the normal case, not an error: it is what "nobody has
    asked for anything" looks like."""
    db_path = tmp_path / "trading.db"

    assert stop_signal.pending(db_path) is None
    assert stop_signal.requested_for(db_path, "ciclo-1") is False
    # And clearing what is not there is not an error either: the cycle clears on
    # registering, which is almost always over nothing.
    stop_signal.clear(db_path)


def test_clearing_removes_the_request(tmp_path):
    db_path = tmp_path / "trading.db"
    stop_signal.request(db_path, "ciclo-1")

    stop_signal.clear(db_path)

    assert stop_signal.pending(db_path) is None
