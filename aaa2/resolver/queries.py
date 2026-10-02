"""A resolve modul lekérdező függvényei a saját tábláira, amelyeknek egyedül ő a gazdája
(`entity_aliases`, `entity_relations`, `merge_log`, `validation_calls`): más modul ezeken
keresztül olvassa őket. A visszaadott érték szerződés (`aaa2/contracts`) vagy egyszerű
összesítés, rögzített sorrendben."""
from __future__ import annotations

from datetime import datetime

import duckdb

from aaa2.contracts import Alias, Relation


def _rows(con: duckdb.DuckDBPyConnection, query: str) -> list[dict]:
    cursor = con.execute(query)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def aliases(con: duckdb.DuckDBPyConnection) -> list[Alias]:
    """Az entitások aliasai forrással, az entitás, az alias és a forrás szerint."""
    return [Alias.from_row(row) for row in _rows(
        con, "SELECT * FROM entity_aliases ORDER BY entity_id, alias, source, lang")]


def relations(con: duckdb.DuckDBPyConnection) -> list[Relation]:
    """Az entitások kapcsolatai a típus és a két entitás szerint."""
    return [Relation.from_row(row) for row in _rows(
        con, "SELECT * FROM entity_relations ORDER BY type, from_id, to_id, source")]


def relation_counts(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int]]:
    return con.execute("SELECT type, count(*) FROM entity_relations GROUP BY type "
                       "ORDER BY type").fetchall()


def merge_counts(con: duckdb.DuckDBPyConnection, run_id: int) -> list[tuple[str, int]]:
    """A futás összevonásai szabályonként."""
    return con.execute("SELECT rule, count(*) FROM merge_log WHERE run_id = ? GROUP BY rule "
                       "ORDER BY rule", [run_id]).fetchall()


def knowledge_errors_since(con: duckdb.DuckDBPyConnection, since: datetime
                           ) -> list[tuple[str, int]]:
    """A tudásbázis-kérések hibái szolgáltatásonként a megadott időpont óta."""
    return con.execute(
        "SELECT service, count(*) FROM validation_calls WHERE error IS NOT NULL "
        "AND called_at >= ? GROUP BY service ORDER BY service", [since]).fetchall()


def kg_calls_today(con: duckdb.DuckDBPyConnection) -> int:
    """A mai Knowledge Graph-hívások száma ezen a site-on."""
    return con.execute(
        "SELECT count(*) FROM validation_calls WHERE service = 'kg' AND "
        "CAST(called_at AS DATE) = current_date").fetchone()[0]
