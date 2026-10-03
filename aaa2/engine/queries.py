"""A crawl modul lekérdező függvényei: a crawl tábláit (`site`, `pages`, `links`,
`schema_blocks`, `structured_data`, `crawl_runs`, `crawl_queue`) más modul ezeken keresztül
olvassa, közvetlen SQL nélkül. A visszaadott érték szerződés (`aaa2/contracts`); kivétel a tárolt
renderelt DOM (`rendered_html`: tömörített bájtok, `rendered_dom`: szöveg), amely nem része a
`Page` szerződésnek.

A sorrend mindenhol rögzített (az elsődleges kulcs, illetve az oldal és a sorszám szerint)."""
from __future__ import annotations

import json
from collections import Counter
from urllib.parse import urljoin

import duckdb
import zstandard

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


def rendered_dom(con: duckdb.DuckDBPyConnection, page_id: int) -> str | None:
    """Az oldal tárolt renderelt DOM-ja szövegként (`rendered_html` kicsomagolva); None, ha
    nincs."""
    blob = rendered_html(con, page_id)
    if blob is None:
        return None
    return zstandard.ZstdDecompressor().decompress(blob).decode("utf-8", "replace")


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


# A microdata-elem ezen tulajdonságai URL-ek: a relatív érték az oldal URL-jéhez képest feloldva.
_URL_PROPERTIES = ("@id", "url", "item")


def schema_items(con: duckdb.DuckDBPyConnection) -> list[StructuredData]:
    """A schema.org-elemek, amelyekből a szabálykör és a feloldás dolgozik: a JSON-LD blokkok
    (`json_ld`), utánuk a microdata-elemek ugyanabban az alakban, az oldal és a sorszám szerint.

    A microdata-elem átalakítása: a relatív `@id`, `url` és `item` érték az oldal URL-jéhez
    képest abszolút lesz (a beágyazott elemekben is); az oldal egyetlen legfelső szintű
    `Product` eleme, ha nincs `url`-je és `@id`-je, az oldal URL-jét kapja `url`-nek (a microdata
    az oldalon látható dolgot jelöli, a termékoldal ritkán ad külön URL-t). Az RDFa és az Open
    Graph nem része: az előbbire nincs mért készlet, az utóbbi nem schema.org."""
    items = json_ld(con)
    rows = _rows(con, "SELECT s.page_id, s.type, s.json, s.ordinal, p.url FROM structured_data s "
                      "JOIN pages p USING (page_id) WHERE s.syntax = 'microdata' "
                      "ORDER BY s.page_id, s.ordinal, s.type, s.json")
    if not rows:
        return items
    parsed = [(row, json.loads(row["json"])) for row in rows]
    products = Counter(row["page_id"] for row, data in parsed if _is_product(data))
    for row, data in parsed:
        data = _absolute_urls(data, row["url"])
        if _is_product(data) and products[row["page_id"]] == 1 \
                and not data.get("url") and not data.get("@id"):
            data = {**data, "url": row["url"]}
        items.append(StructuredData(page_id=row["page_id"], syntax="microdata", type=row["type"],
                                    data=data, ordinal=row["ordinal"]))
    return items


def _is_product(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    kinds = data.get("@type")
    return any(str(kind).rstrip("/").rsplit("/", 1)[-1] == "Product"
               for kind in (kinds if isinstance(kinds, list) else [kinds]))


def _absolute_urls(value: object, base: str) -> object:
    if isinstance(value, list):
        return [_absolute_urls(child, base) for child in value]
    if not isinstance(value, dict):
        return value
    return {key: urljoin(base, child) if key in _URL_PROPERTIES and isinstance(child, str)
            and child.strip() else _absolute_urls(child, base)
            for key, child in value.items()}


def latest_crawl_run(con: duckdb.DuckDBPyConnection) -> CrawlRun | None:
    rows = _rows(con, "SELECT * FROM crawl_runs ORDER BY run_id DESC LIMIT 1")
    return CrawlRun.from_row(rows[0]) if rows else None


def queue_status_counts(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int]]:
    """A crawl-sor elemeinek száma állapotonként, állapot szerint rendezve."""
    return con.execute(
        "SELECT status, count(*) FROM crawl_queue GROUP BY status ORDER BY status").fetchall()
