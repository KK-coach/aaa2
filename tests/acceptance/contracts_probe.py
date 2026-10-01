"""Mérés: a szerződések (aaa2/contracts) felépíthetők-e egy valódi adatbázis minden sorából.

`python -m tests.acceptance.contracts_probe <adatbázis> [<adatbázis> …]`: adatbázisonként és
szerződésenként a forrástábla sorainak és a felépített szerződéseknek a száma; a hiányzó tábla
külön jelölve. Csak olvas."""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
from pydantic import ValidationError

from aaa2.contracts import CONTRACTS
from tests.contract_rows import build_all


def probe(db: Path) -> tuple[list[str], int]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        try:
            built = build_all(con)
        except ValidationError as error:
            return [f"{db.name}: HIBA: {error.errors()[0]}"], 1
    finally:
        con.close()
    lines = [f"== {db.name}"]
    for model in CONTRACTS:
        name = model.__name__
        if name not in built:
            lines.append(f"  {name}: nincs ilyen tábla")
            continue
        rows, items = built[name]
        lines.append(f"  {name}: {len(items)}/{rows}")
    total = sum(len(items) for _, items in built.values())
    lines.append(f"  összesen: {total} szerződés, {len(built)}/{len(CONTRACTS)} fajta")
    return lines, 0


def main() -> None:
    failed = 0
    for path in sys.argv[1:]:
        lines, errors = probe(Path(path))
        failed += errors
        print("\n".join(lines))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
