"""Shared fixtures and access to `tests/helpers.py`.

`tests/` is deliberately not a package (no `__init__.py`), so its directory is
added to the path so the test modules can import `helpers` without relative
imports, which break depending on where pytest is invoked from.
"""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from src.db import Database  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """A clean database in a temporary file, with the schema already applied."""
    with Database(path=tmp_path / "test.db") as database:
        yield database
