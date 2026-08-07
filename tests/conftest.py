"""Fixtures compartidas y acceso a `tests/helpers.py`.

`tests/` no es un paquete a proposito (sin `__init__.py`), asi que se anade su
directorio al path para que los modulos de test puedan importar `helpers` sin
imports relativos, que rompen segun desde donde se invoque pytest.
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
    """Base de datos limpia en fichero temporal, con el esquema ya aplicado."""
    with Database(path=tmp_path / "test.db") as database:
        yield database
