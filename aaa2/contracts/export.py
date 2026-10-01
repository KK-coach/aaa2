"""A szerződések JSON Schema exportja: `python -m aaa2.contracts.export <mappa>` szerződésenként
egy `<Név>.schema.json` fájlt ír (a repóban: `docs/contracts/`), hogy egy HTTP API vagy más
nyelvű kliens ugyanazt az alakot használhassa."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from aaa2.contracts.models import CONTRACTS, SCHEMA_VERSION


def json_schemas() -> dict[str, dict]:
    """Szerződésnév → JSON Schema; a séma `x-module` és `x-schema-version` kulcsa a kiadó
    modul és a szerződés verziója."""
    schemas = {}
    for model in CONTRACTS:
        schema = _public(model.model_json_schema())
        schema["x-module"] = model.module
        schema["x-schema-version"] = SCHEMA_VERSION
        schemas[model.__name__] = schema
    return schemas


def _public(node):
    """A séma a tárolási jelölés (`json_column`) nélkül."""
    if isinstance(node, dict):
        return {key: _public(value) for key, value in node.items() if key != "json_column"}
    if isinstance(node, list):
        return [_public(value) for value in node]
    return node


def schema_text(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def export(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in json_schemas().items():
        path = directory / f"{name}.schema.json"
        path.write_text(schema_text(schema), encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in export(Path(sys.argv[1])):
        print(written_path)
