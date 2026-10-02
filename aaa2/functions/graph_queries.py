"""A gráf modul lekérdező függvényei: a gráf tábláit (`page_nodes`, `edges`,
`page_main_entity`, `entity_weights`) más modul ezeken keresztül olvassa. A visszaadott érték
szerződés (`aaa2/contracts`), rögzített sorrendben."""
from __future__ import annotations

import duckdb

from aaa2.contracts import Edge, EntityWeight, MainEntity, PageNode


def _rows(con: duckdb.DuckDBPyConnection, query: str, parameters: list | None = None) -> list[dict]:
    cursor = con.execute(query, parameters or [])
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def page_nodes(con: duckdb.DuckDBPyConnection) -> list[PageNode]:
    """Az oldal-csomópontok, `page_id` szerint."""
    return [PageNode.from_row(row) for row in _rows(
        con, "SELECT * FROM page_nodes ORDER BY page_id")]


def main_entities(con: duckdb.DuckDBPyConnection) -> list[MainEntity]:
    """Az oldalak fő és másodlagos entitásai, az oldal és a rangsor szerint."""
    return [MainEntity.from_row(row) for row in _rows(
        con, "SELECT * FROM page_main_entity ORDER BY page_id, rank, entity_id")]


def entity_weights(con: duckdb.DuckDBPyConnection) -> list[EntityWeight]:
    """Az entitások súlya, `entity_id` szerint."""
    return [EntityWeight.from_row(row) for row in _rows(
        con, "SELECT * FROM entity_weights ORDER BY entity_id")]


def edges(con: duckdb.DuckDBPyConnection, kind: str | None = None) -> list[Edge]:
    """Az élek (`kind`: csak ez az éltípus), `edge_id` szerint."""
    if kind is None:
        return [Edge.from_row(row) for row in _rows(con, "SELECT * FROM edges ORDER BY edge_id")]
    return [Edge.from_row(row) for row in _rows(
        con, "SELECT * FROM edges WHERE type = ? ORDER BY edge_id", [kind])]
