#!/usr/bin/env python
"""Descarga la lista de componentes del S&P 500 y la guarda en un fichero.

    python tools/fetch_universe.py                    -> data/universe_sp500.txt
    python tools/fetch_universe.py --out otra.txt

La fuente es el repositorio `datasets/s-and-p-500-companies` de GitHub, que se
mantiene a partir de Wikipedia. Se prefiere a raspar Wikipedia directamente
porque es un CSV estable en lugar de HTML que cambia de forma.

Por que un fichero y no una llamada en cada arranque: la composicion del indice
cambia unas pocas veces al ano, y depender de la red para saber que analizar
haria que un corte de conexion detuviera el agente. El fichero se versiona con la
fecha de descarga dentro, para que se sepa cuando envejece.

Nota sobre los simbolos: Yahoo usa guion donde el indice usa punto (BRK.B es
BRK-B en Yahoo). La conversion se hace aqui, al escribir el fichero.
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

# En `universe/` y no en `data/`: este fichero forma parte del proyecto y tiene que
# viajar con git y con la imagen de Docker, mientras que `data/` esta excluido de
# ambos porque contiene la base de datos.
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "universe" / "sp500.txt"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z.\-]{0,6}$")


def to_yahoo(symbol: str) -> str:
    """BRK.B -> BRK-B. Yahoo usa guion para las clases de accion."""
    return symbol.strip().upper().replace(".", "-")


def fetch() -> tuple[list[tuple[str, str, str]], str]:
    """Devuelve [(simbolo, nombre, sector)] y el nombre de la fuente usada."""
    headers = {"User-Agent": "financial-bot/0.1 (universe fetch)"}
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

        # Un indice con menos de 400 nombres significa que el CSV cambio de forma.
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
