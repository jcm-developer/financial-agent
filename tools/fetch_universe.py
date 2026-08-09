#!/usr/bin/env python
"""Downloads the list of S&P 500 constituents and stores it in a file.

    python tools/fetch_universe.py                    -> data/universe_sp500.txt
    python tools/fetch_universe.py --out other.txt

The source is GitHub's `datasets/s-and-p-500-companies` repository, which is
maintained from Wikipedia. It is preferred over scraping Wikipedia directly
because it is a stable CSV instead of HTML that keeps changing shape.

Why a file and not a call on every startup: the index's composition changes a few
times a year, and depending on the network to know what to analyse would let a
connection outage stop the agent. The file is versioned with the download date
inside, so it is known when it grows stale.

A note on the symbols: Yahoo uses a hyphen where the index uses a dot (BRK.B is
BRK-B on Yahoo). The conversion happens here, when the file is written.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

SOURCES = (
    ("datasets/s-and-p-500-companies (GitHub)",
     "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"),
    ("datasets/s-and-p-500-companies (rama master)",
     "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"),
)

# In `universe/` and not in `data/`: this file is part of the project and has to
# travel with git and with the Docker image, whereas `data/` is excluded from both
# because it holds the database.
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "universe" / "sp500.txt"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z.\-]{0,6}$")


def to_yahoo(symbol: str) -> str:
    """BRK.B -> BRK-B. Yahoo uses a hyphen for share classes."""
    return symbol.strip().upper().replace(".", "-")


def fetch() -> tuple[list[tuple[str, str, str]], str]:
    """Returns [(symbol, name, sector)] and the name of the source used."""
    headers = {"User-Agent": "financial-agent/0.1 (universe fetch)"}
    errors: list[str] = []

    for name, url in SOURCES:
        try:
            response = httpx.get(url, headers=headers, timeout=30.0, follow_redirects=True)
        except httpx.HTTPError as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue

        if response.status_code != 200:
            errors.append(f"{name}: HTTP {response.status_code}")
            continue

        rows = list(csv.DictReader(io.StringIO(response.text)))
        entries: list[tuple[str, str, str]] = []
        for row in rows:
            raw = (row.get("Symbol") or "").strip().upper()
            if not SYMBOL_RE.match(raw):
                continue
            entries.append((
                to_yahoo(raw),
                (row.get("Security") or "").strip(),
                (row.get("GICS Sector") or "").strip(),
            ))

        # An index with fewer than 400 names means the CSV changed shape.
        if len(entries) < 400:
            errors.append(f"{name}: solo {len(entries)} simbolos validos, formato inesperado")
            continue

        return entries, name

    raise SystemExit(
        "No se pudo descargar la lista. Detalle:\n  "
        + "\n  ".join(errors)
        + "\n\nAlternativa: crea el fichero a mano con un simbolo por linea.\n"
          "Las lineas que empiezan por # se ignoran."
    )


def write(entries: list[tuple[str, str, str]], source: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sectors: dict[str, int] = {}
    for _, _, sector in entries:
        sectors[sector or "(sin sector)"] = sectors.get(sector or "(sin sector)", 0) + 1

    lines = [
        "# Universo de analisis: componentes del S&P 500.",
        f"# Descargado el {stamp} de {source}.",
        f"# {len(entries)} simbolos, en notacion de Yahoo (BRK-B, no BRK.B).",
        "#",
        "# Refrescar con:  python tools/fetch_universe.py",
        "# Las lineas que empiezan por # y las vacias se ignoran.",
        "#",
        "# Reparto por sector:",
    ]
    for sector, count in sorted(sectors.items(), key=lambda kv: -kv[1]):
        lines.append(f"#   {count:>4}  {sector}")
    lines.append("")
    lines.extend(symbol for symbol, _, _ in sorted(entries))
    lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Descarga el universo S&P 500.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    entries, source = fetch()
    write(entries, source, args.out)

    print(f"  {len(entries)} simbolos escritos en {args.out}")
    print(f"  Fuente: {source}")
    print(f"\n  Para usarlo, en el .env:  UNIVERSE_FILE={args.out.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
