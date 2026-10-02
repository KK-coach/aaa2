"""A crawl modul lekérdező függvényei: a crawl tábláit (`site`, `pages`, `links`,
`schema_blocks`, `structured_data`, `crawl_runs`, `crawl_queue`) más modul ezeken keresztül
olvassa, közvetlen SQL nélkül. A visszaadott érték szerződés (`aaa2/contracts`); kivétel a tárolt
renderelt DOM (tömörített bájtok), amely nem része a `Page` szerződésnek.

A sorrend mindenhol rögzített (az elsődleges kulcs, illetve az oldal és a sorszám szerint)."""
from __future__ import annotations

import duckdb

from aaa2.contracts import CrawlRun, Link, Page, PageMeta, Site, StructuredData

_PAGE = "SELECT * EXCLUDE (rendered_html), rendered_html IS NOT NULL AS has_rendered_html FROM pages"


def _rows(con: duckdb.DuckDBPyConnection, query: str, parameters: list | None = None) -> list[dict]:
    cursor = con.execute(query, parameters or [])
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def site(con: duckdb.DuckDBPyConnection) -> Site | None:
    """A site profilja; None, ha még nincs crawl."""
    rows = _rows(con, "SELECT * FROM site ORDER BY domain")
    return Site.from_row(rows[0]) if rows else None


def pages(con: duckdb.DuckDBPyConnection) -> list[Page]:
    """Minden oldal, `page_id` szerint."""
    return [Page.from_row(row) for row in _rows(con, f"{_PAGE} ORDER BY page_id")]


def rendered_pages(con: duckdb.DuckDBPyConnection) -> list[Page]:
    """A sikeresen bejárt, tárolt DOM-mal bíró oldalak (`Page.renderable`), `page_id` szerint."""
    return [page for page in pages(con) if page.renderable]


def page_metas(con: duckdb.DuckDBPyConnection) -> list[PageMeta]:
    """Oldalanként a canonical és a hreflang, `page_id` szerint (a strukturált adat nélkül:
    azt a `json_ld` és a `structured_data` adja)."""
    return [PageMeta.from_row(row) for row in _rows(
        con, "SELECT page_id, canonical, hreflang FROM pages ORDER BY page_id")]


def rendered_html(con: duckdb.DuckDBPyConnection, page_id: int) -> bytes | None:
    """Az oldal tárolt renderelt DOM-ja tömörítve (zstd); None, ha nincs."""
    row = con.execute("SELECT rendered_html FROM pages WHERE page_id = ?", [page_id]).fetchone()
    return row[0] if row else None


def rendered(con: duckdb.DuckDBPyConnection, page_id: int) -> tuple[str | None, bytes | None]:
    """Az oldal title-je és tárolt renderelt DOM-ja (tömörítve)."""
    row = con.execute("SELECT title, rendered_html FROM pages WHERE page_id = ?",
                      [page_id]).fetchone()
    return (row[0], row[1]) if row else (None, None)


def links(con: duckdb.DuckDBPyConnection) -> list[Link]:
    """A belső linkek a forrásoldal és a DOM-sorrend (`ordinal`) szerint."""
    return [Link.from_row(row) for row in _rows(
        con, "SELECT * FROM links ORDER BY from_page_id, ordinal, to_url, anchor, position, "
             "to_page_id, nofollow")]


def json_ld(con: duckdb.DuckDBPyConnection) -> list[StructuredData]:
    """Az érvényes JSON-LD blokkok (a hibás JSON nélkül) az oldal és a sorszám szerint."""
    return [StructuredData.from_row({**row, "syntax": "json-ld"}) for row in _rows(
        con, "SELECT page_id, type, json, ordinal FROM schema_blocks "
             "WHERE type IS DISTINCT FROM 'invalid' ORDER BY page_id, ordinal, type, json")]


def structured_data(con: duckdb.DuckDBPyConnection) -> list[StructuredData]:
    """A microdata-, RDFa- és Open Graph-elemek az oldal és a sorszám szerint."""
    return [StructuredData.from_row(row) for row in _rows(
        con, "SELECT page_id, syntax, type, json, ordinal FROM structured_data "
             "ORDER BY page_id, ordinal, syntax, type, json")]


def latest_crawl_run(con: duckdb.DuckDBPyConnection) -> CrawlRun | None:
    rows = _rows(con, "SELECT * FROM crawl_runs ORDER BY run_id DESC LIMIT 1")
    return CrawlRun.from_row(rows[0]) if rows else None


def queue_status_counts(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int]]:
    """A crawl-sor elemeinek száma állapotonként, állapot szerint rendezve."""
    return con.execute(
        "SELECT status, count(*) FROM crawl_queue GROUP BY status ORDER BY status").fetchall()
