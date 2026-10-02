"""Az extract modul lekérdező függvényei a saját tábláira, amelyeknek egyedül ő a gazdája
(`blocks`, `entity_run_pages`): más modul ezeken keresztül olvassa őket. A visszaadott érték
szerződés (`aaa2/contracts`) vagy a futásnapló egyszerű sorai, rögzített sorrendben."""
from __future__ import annotations

import duckdb

from aaa2.contracts import Block


def _blocks(con: duckdb.DuckDBPyConnection, where: str, parameters: list) -> list[Block]:
    cursor = con.execute(f"SELECT * FROM blocks {where} ORDER BY page_id, ordinal, block_id",
                         parameters)
    names = [column[0] for column in cursor.description]
    return [Block.from_row(dict(zip(names, row, strict=True))) for row in cursor.fetchall()]


def blocks(con: duckdb.DuckDBPyConnection) -> list[Block]:
    """Minden blokk, az oldal és a dokumentum-sorrend (`ordinal`) szerint."""
    return _blocks(con, "", [])


def page_blocks(con: duckdb.DuckDBPyConnection, page_id: int) -> list[Block]:
    """Egy oldal blokkjai a dokumentum sorrendjében."""
    return _blocks(con, "WHERE page_id = ?", [page_id])


def block_at(con: duckdb.DuckDBPyConnection, page_id: int, ordinal: int) -> Block | None:
    """Az oldal adott sorszámú blokkja."""
    found = _blocks(con, "WHERE page_id = ? AND ordinal = ?", [page_id, ordinal])
    return found[0] if found else None


def block(con: duckdb.DuckDBPyConnection, block_id: int) -> Block | None:
    found = _blocks(con, "WHERE block_id = ?", [block_id])
    return found[0] if found else None


def run_page_status_counts(con: duckdb.DuckDBPyConnection, run_id: int) -> list[tuple[str, int]]:
    """A futás oldalainak száma állapotonként, állapot szerint rendezve."""
    return con.execute(
        "SELECT status, count(*) FROM entity_run_pages WHERE run_id = ? GROUP BY status "
        "ORDER BY status", [run_id]).fetchall()


def unfinished_run_pages(con: duckdb.DuckDBPyConnection, run_id: int
                         ) -> list[tuple[int, str, str | None]]:
    """A futás hibás vagy megállított oldalai: (oldal, állapot, hiba), oldal szerint."""
    return con.execute(
        "SELECT page_id, status, error FROM entity_run_pages WHERE run_id = ? AND status IN "
        "('failed', 'verify_error', 'stopped') ORDER BY page_id", [run_id]).fetchall()


def done_run_records(con: duckdb.DuckDBPyConnection, run_id: int
                     ) -> list[tuple[int, str | None, str | None]]:
    """A futás kész oldalainak tárolt rekordjai: (oldal, az ellenőrzött rekord, a kinyerés
    rekordja) JSON-szövegként, oldal szerint."""
    return con.execute(
        "SELECT page_id, refined, extraction FROM entity_run_pages WHERE run_id = ? "
        "AND status = 'done' ORDER BY ALL", [run_id]).fetchall()
