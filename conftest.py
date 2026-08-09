"""Makes `src` and `tools` importable from the tests.

Without this, `python -m pytest` only works when invoked from the project root,
which is exactly what fails inside the container and in continuous integration.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
