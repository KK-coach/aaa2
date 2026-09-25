"""DuckDB kapcsolat és migrációk.

Egy fájl site-onként: data/<domain>.duckdb. A közös szabálytár és az általános
entitás-szótár: data/shared.duckdb. Mindkettőre ugyanaz a migráció fut.
"""
from __future__ import annotations

import re
from pathlib import Path

import duckdb

DATA_DIR = Path("data")
MIGRATIONS_DIR = Path(__file__).parent


def db_path(domain: str) -> Path:
    """data/<domain>.duckdb — a domain csak registrable domain lehet."""
    safe = re.sub(r"[^a-z0-9.-]", "_", domain.lower())
    return DATA_DIR / f"{safe}.duckdb"


def shared_path() -> Path:
    return DATA_DIR / "shared.duckdb"


def connect(path: Path | str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Megnyit egy adatbázist és lefuttatja a hiányzó migrációkat."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    migrate(con)
    return con


def migrate(con: duckdb.DuckDBPyConnection) -> list[str]:
    """NNN_*.sql fájlok sorrendben; a lefutottakat a _migrations tábla tartja.

    Ha futott migráció, utána CHECKPOINT: a séma az adatbázisfájlba kerül, a WAL-ban nem marad
    DDL. (A DuckDB 1.5 nem játssza vissza a `DEFAULT nextval(...)` oszlopos táblára futó
    `ALTER TABLE ... ADD COLUMN`-t, és a kilőtt folyamat adatbázisa nem nyílik meg.)
    """
    con.execute(
        "CREATE TABLE IF NOT EXISTS _migrations (name VARCHAR PRIMARY KEY, applied_at TIMESTAMP)"
    )
    done = {r[0] for r in con.execute("SELECT name FROM _migrations").fetchall()}
    applied: list[str] = []
    for sql_file in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        if sql_file.name in done:
            continue
        con.execute(sql_file.read_text(encoding="utf-8"))
        con.execute(
            "INSERT INTO _migrations VALUES (?, current_timestamp)", [sql_file.name]
        )
        applied.append(sql_file.name)
    if applied:
        con.execute("CHECKPOINT")
    return applied
