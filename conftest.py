"""Hace importables `src` y `tools` desde los tests.

Sin esto, `python -m pytest` solo funciona invocado desde la raiz del proyecto,
que es justo lo que falla dentro del contenedor y en integracion continua.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
