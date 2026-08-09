#!/usr/bin/env python
"""Generates the frontend's TypeScript types from the OpenAPI document (F3.6).

    python tools/gen_api_types.py                 # -> app/src/api/types.ts
    python tools/gen_api_types.py --check         # fails if they are out of date
    python tools/gen_api_types.py --out other.ts

**Why not `openapi-typescript`.** It is the standard tool and it does this
better, but it needs Node, and today the repository does not even have a
`package.json`: the frontend arrives in F4. A 150-line generator with no
dependencies can be run from day one, which is when it is needed —the types are
exactly what F4.1's scaffolding is going to consume—. If the schema ever gets
complicated, moving to `openapi-typescript` means replacing this file, not
redoing anything.

The `--check` is meant for a hook or for F8.6: if someone adds a field to a model
in `api/models.py` and does not regenerate, the types file starts lying in
silence, which is exactly what F3.6 wants to avoid.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT = REPO_ROOT / "app" / "src" / "api" / "types.ts"

HEADER = """// Generado por tools/gen_api_types.py a partir del OpenAPI de la API.
// NO EDITAR A MANO: se regenera con  python tools/gen_api_types.py
//
// Los nombres salen de api/models.py. Si algo aqui no cuadra con lo que
// devuelve el servidor, el que manda es el servidor y este fichero esta
// desfasado.
"""


def _safe(name: str) -> str:
    """`Page_PositionRow_` -> `Page_PositionRow`. Unico y valido en TypeScript."""
    clean = re.sub(r"[^0-9a-zA-Z_]", "_", name).strip("_")
    return clean or "Unknown"


def _type(schema: dict[str, Any] | None) -> str:
    """One JSON Schema schema to its TypeScript equivalent."""
    if not schema:
        return "unknown"

    if "$ref" in schema:
        return _safe(schema["$ref"].rsplit("/", 1)[-1])

    if "const" in schema:
        return _literal(schema["const"])

    if "enum" in schema:
        return " | ".join(_literal(value) for value in schema["enum"]) or "never"

    for key in ("anyOf", "oneOf"):
        if key in schema:
            partes = [_type(sub) for sub in schema[key]]
            # `null` last: `string | null` reads better than `null | string`.
            partes = sorted(set(partes), key=lambda t: (t == "null", t))
            return " | ".join(partes) or "unknown"

    if "allOf" in schema:
        partes = [_type(sub) for sub in schema["allOf"]]
        return " & ".join(partes) if len(partes) > 1 else (partes[0] if partes else "unknown")

    tipo = schema.get("type")
    if tipo == "array":
        return f"Array<{_type(schema.get('items'))}>"
    if tipo == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"Record<string, {_type(extra)}>"
        if extra is False and "properties" in schema:
            return _inline_object(schema)
        return "Record<string, unknown>"
    if tipo == "string":
        return "string"
    if tipo in ("number", "integer"):
        return "number"
    if tipo == "boolean":
        return "boolean"
    if tipo == "null":
        return "null"
    if isinstance(tipo, list):
        return " | ".join(_type({"type": t}) for t in tipo)
    return "unknown"


def _literal(value: Any) -> str:
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _inline_object(schema: dict[str, Any]) -> str:
    required = set(schema.get("required") or ())
    campos = [
        f"{_key(name)}{'' if name in required else '?'}: {_type(sub)}"
        for name, sub in (schema.get("properties") or {}).items()
    ]
    return "{ " + "; ".join(campos) + " }" if campos else "Record<string, never>"


def _key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_$][0-9A-Za-z_$]*", name) else f'"{name}"'


def _interface(name: str, schema: dict[str, Any]) -> str:
    doc = schema.get("description") or ""
    lines: list[str] = []
    if doc:
        lines.append("/**")
        lines += [f" * {line}".rstrip() for line in doc.strip().splitlines()]
        lines.append(" */")

    # A model with no properties (a loose enum, a union) is an alias, not an
    # interface: `interface X extends string` does not exist in TypeScript.
    if "properties" not in schema:
        lines.append(f"export type {_safe(name)} = {_type(schema)};")
        return "\n".join(lines)

    required = set(schema.get("required") or ())
    lines.append(f"export interface {_safe(name)} {{")
    for campo, sub in schema["properties"].items():
        sub_doc = sub.get("description")
        if sub_doc:
            lines.append(f"  /** {' '.join(sub_doc.split())} */")
        opcional = "" if campo in required else "?"
        lines.append(f"  {_key(campo)}{opcional}: {_type(sub)};")
    lines.append("}")
    return "\n".join(lines)


def _paths(spec: dict[str, Any]) -> str:
    """A map of operations: method, path and type of the 2xx response.

    It does not aim to be a typed client: it is just enough for the frontend's
    `fetch` to know what to expect from each URL without going to look at the
    Python code.
    """
    lines = [
        "/** Operaciones de la API: 'METODO /ruta' -> tipo de la respuesta. */",
        "export interface ApiOperations {",
    ]
    for ruta, operaciones in sorted(spec.get("paths", {}).items()):
        for metodo, operacion in sorted(operaciones.items()):
            if metodo.upper() not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                continue
            respuestas = operacion.get("responses") or {}
            esquema: dict[str, Any] | None = None
            for codigo in sorted(respuestas):
                if codigo.startswith("2"):
                    contenido = (respuestas[codigo].get("content") or {})
                    esquema = (contenido.get("application/json") or {}).get("schema")
                    break
            summary = operacion.get("summary") or ""
            if summary:
                lines.append(f"  /** {summary} */")
            lines.append(f'  "{metodo.upper()} {ruta}": {_type(esquema)};')
    lines.append("}")
    return "\n".join(lines)


def render(spec: dict[str, Any]) -> str:
    esquemas = (spec.get("components") or {}).get("schemas") or {}
    bloques = [HEADER]
    for name in sorted(esquemas):
        bloques.append(_interface(name, esquemas[name]))
    bloques.append(_paths(spec))
    return "\n\n".join(bloques) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--check", action="store_true",
        help="No escribe: sale con 1 si el fichero no coincide con el esquema.",
    )
    args = parser.parse_args(argv)

    from api.main import create_app

    spec = create_app().openapi()
    contenido = render(spec)
    target = Path(args.out)

    if args.check:
        actual = target.read_text(encoding="utf-8") if target.is_file() else ""
        if actual == contenido:
            print(f"  {target} esta al dia.")
            return 0
        print(
            f"  {target} no coincide con el OpenAPI actual.\n"
            "  Regeneralo con:  python tools/gen_api_types.py",
            file=sys.stderr,
        )
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contenido, encoding="utf-8")
    tipos = len((spec.get("components") or {}).get("schemas") or {})
    print(f"  {tipos} tipos escritos en {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
