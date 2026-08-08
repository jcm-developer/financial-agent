#!/usr/bin/env python
"""Genera los tipos de TypeScript del frontend a partir del OpenAPI (F3.6).

    python tools/gen_api_types.py                 # -> app/src/api/types.ts
    python tools/gen_api_types.py --check         # falla si estan desfasados
    python tools/gen_api_types.py --out otro.ts

**Por que no `openapi-typescript`.** Es la herramienta estandar y hace esto
mejor, pero necesita Node, y hoy el repositorio no tiene ni `package.json`: el
frontend llega en F4. Un generador de 150 lineas sin dependencias se puede correr
desde el primer dia, que es cuando hace falta —los tipos son justo lo que va a
consumir el andamiaje de F4.1—. Si algun dia el esquema se complica, cambiar a
`openapi-typescript` es sustituir este fichero, no rehacer nada.

El `--check` esta pensado para un hook o para F8.6: si alguien añade un campo a
un modelo de `api/models.py` y no regenera, el fichero de tipos empieza a mentir
en silencio, que es exactamente lo que F3.6 quiere evitar.
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
    """Un esquema de JSON Schema a su equivalente en TypeScript."""
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
            # `null` al final: se lee mejor `string | null` que `null | string`.
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
        f"{_key(nombre)}{'' if nombre in required else '?'}: {_type(sub)}"
        for nombre, sub in (schema.get("properties") or {}).items()
    ]
    return "{ " + "; ".join(campos) + " }" if campos else "Record<string, never>"


def _key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_$][0-9A-Za-z_$]*", name) else f'"{name}"'


def _interface(name: str, schema: dict[str, Any]) -> str:
    doc = schema.get("description") or ""
    lineas: list[str] = []
    if doc:
        lineas.append("/**")
        lineas += [f" * {linea}".rstrip() for linea in doc.strip().splitlines()]
        lineas.append(" */")

    # Un modelo sin propiedades (un enum suelto, una union) es un alias, no una
    # interfaz: `interface X extends string` no existe en TypeScript.
    if "properties" not in schema:
        lineas.append(f"export type {_safe(name)} = {_type(schema)};")
        return "\n".join(lineas)

    required = set(schema.get("required") or ())
    lineas.append(f"export interface {_safe(name)} {{")
    for campo, sub in schema["properties"].items():
        sub_doc = sub.get("description")
        if sub_doc:
            lineas.append(f"  /** {' '.join(sub_doc.split())} */")
        opcional = "" if campo in required else "?"
        lineas.append(f"  {_key(campo)}{opcional}: {_type(sub)};")
    lineas.append("}")
    return "\n".join(lineas)


def _paths(spec: dict[str, Any]) -> str:
    """Un mapa de operaciones: metodo, ruta y tipo de la respuesta 2xx.

    No pretende ser un cliente tipado: es lo justo para que el `fetch` del
    frontend sepa que espera de cada URL sin ir a mirar el codigo Python.
    """
    lineas = [
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
            resumen = operacion.get("summary") or ""
            if resumen:
                lineas.append(f"  /** {resumen} */")
            lineas.append(f'  "{metodo.upper()} {ruta}": {_type(esquema)};')
    lineas.append("}")
    return "\n".join(lineas)


def render(spec: dict[str, Any]) -> str:
    esquemas = (spec.get("components") or {}).get("schemas") or {}
    bloques = [HEADER]
    for nombre in sorted(esquemas):
        bloques.append(_interface(nombre, esquemas[nombre]))
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
    destino = Path(args.out)

    if args.check:
        actual = destino.read_text(encoding="utf-8") if destino.is_file() else ""
        if actual == contenido:
            print(f"  {destino} esta al dia.")
            return 0
        print(
            f"  {destino} no coincide con el OpenAPI actual.\n"
            "  Regeneralo con:  python tools/gen_api_types.py",
            file=sys.stderr,
        )
        return 1

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(contenido, encoding="utf-8")
    tipos = len((spec.get("components") or {}).get("schemas") or {})
    print(f"  {tipos} tipos escritos en {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
