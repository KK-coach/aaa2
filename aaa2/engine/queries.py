"""A crawl modul lekérdező függvényei: a crawl tábláit (`site`, `pages`, `links`,
`schema_blocks`, `structured_data`, `crawl_runs`, `crawl_queue`) más modul ezeken keresztül
olvassa, közvetlen SQL nélkül. A visszaadott érték szerződés (`aaa2/contracts`); kivétel a tárolt
renderelt DOM (`rendered_html`: tömörített bájtok, `rendered_dom`: szöveg), amely nem része a
`Page` szerződésnek.

A sorrend mindenhol rögzített (az elsődleges kulcs, illetve az oldal és a sorszám szerint)."""
from __future__ import annotations

import json
import re
from collections import Counter
from urllib.parse import urljoin, urlsplit

import duckdb
import zstandard

from aaa2.contracts import (
    CrawlRun,
    Link,
    Page,
    PageMeta,
    Site,
    SitemapFile,
    SitemapUrl,
    StructuredData,
)

# Az aktuális audit oldalkészlete: amit a legutóbbi crawl látott (feldolgozott, vagy a változatlan
# tartalom miatt kihagyott; a folytatás ugyanaz a crawl). A jelölés nélküli sor (nem crawlból
# származó adat) aktuálisnak számít. A történeti sorok megmaradnak, de a lekérdezők nem adják.
CURRENT = ("(seen_crawl_id IS NULL OR seen_crawl_id = "
           "(SELECT crawl_id FROM crawl_runs ORDER BY run_id DESC LIMIT 1))")
CURRENT_IDS = f"(SELECT page_id FROM pages WHERE {CURRENT})"
_PAGE = ("SELECT * EXCLUDE (rendered_html), rendered_html IS NOT NULL AS has_rendered_html "
         f"FROM pages WHERE {CURRENT}")


def _rows(con: duckdb.DuckDBPyConnection, query: str, parameters: list | None = None) -> list[dict]:
    cursor = con.execute(query, parameters or [])
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def site(con: duckdb.DuckDBPyConnection) -> Site | None:
    """A site profilja; None, ha még nincs crawl."""
    rows = _rows(con, "SELECT * FROM site ORDER BY domain")
    return Site.from_row(rows[0]) if rows else None


def pages(con: duckdb.DuckDBPyConnection) -> list[Page]:
    """Az aktuális készlet minden oldala (`CURRENT`), `page_id` szerint."""
    return [Page.from_row(row) for row in _rows(con, f"{_PAGE} ORDER BY page_id")]


def rendered_pages(con: duckdb.DuckDBPyConnection) -> list[Page]:
    """A sikeresen bejárt, tárolt DOM-mal bíró oldalak (`Page.renderable`), `page_id` szerint."""
    return [page for page in pages(con) if page.renderable]


def page_metas(con: duckdb.DuckDBPyConnection) -> list[PageMeta]:
    """Oldalanként a canonical és a hreflang, `page_id` szerint (a strukturált adat nélkül:
    azt a `json_ld` és a `structured_data` adja)."""
    return [PageMeta.from_row(row) for row in _rows(
        con, f"SELECT page_id, canonical, hreflang FROM pages WHERE {CURRENT} "
             "ORDER BY page_id")]


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


LANGUAGE_SEGMENT = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,4})?$", re.IGNORECASE)


def _stored_links(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """A tárolt linkek az aktuális készlet oldalairól; a történeti oldalra mutató link célja
    nincs a készletben (`to_page_id` üres)."""
    return _rows(con, "SELECT * REPLACE (CASE WHEN to_page_id IN "
                      f"{CURRENT_IDS} THEN to_page_id END AS to_page_id) FROM links "
                      f"WHERE from_page_id IN {CURRENT_IDS} "
                      "ORDER BY from_page_id, ordinal, to_url, anchor, position, to_page_id, "
                      "nofollow")


def links(con: duckdb.DuckDBPyConnection) -> list[Link]:
    """A belső linkek a forrásoldal és a DOM-sorrend (`ordinal`) szerint, a feloldott céloldallal:
    a tárolt céloldal (`resolution = stored`), vagy ha nincs, a kategóriaúttal bővített cím
    feloldása (`link_targets`, `resolution = inferred`). A gráf, a feloldó és a kimenet
    ugyanezt a célt használja; a `links` tábla nem változik."""
    rows = _stored_links(con)
    targets = _link_targets(con, rows)
    found = []
    for row in rows:
        if row["to_page_id"] is not None:
            row = {**row, "resolution": "stored"}
        elif row["to_url"] in targets:
            row = {**row, "to_page_id": targets[row["to_url"]], "resolution": "inferred"}
        found.append(Link.from_row(row))
    return found


def link_targets(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """A készleten kívüli belső linkcélok feloldása készletbeli oldalra: linkcél (URL) →
    `page_id`, azokra a linkekre, amelyeknek nincs tárolt céloldaluk.

    A webshop ugyanazt az oldalt kategóriaúttal bővített címen is linkeli
    (`/hirek_1/<slug>`, `/kezmuves-szappanok/<slug>`), a készletben az oldal a `/<slug>` címen
    áll (a bővített cím 200-as választ ad, a canonicalja a `/<slug>`; a sitemap-módú crawl a
    bővített címet nem járja be, így a canonicalja nincs tárolva). A feloldás a tárolt címekből
    készül: a cél lekérdezés nélküli útvonalának utolsó szegmense pontosan egy olyan készletbeli
    oldal címe, amely ugyanazon a hoston a gyökér alatt, egyetlen szegmensből áll, és a cél
    útvonala ennél hosszabb (üres szegmens nélkül: a dupla perjeles cím nem ilyen). Nem oldódik
    fel a cél, amelynek első útvonalszegmense nyelvkód (a site nyelvei vagy a hreflang nyelvei
    közül: az `/en/<slug>` más nyelvű oldal, nem a `/<slug>` változata). A rögzített két bolt
    felvételén a szabály minden találata egyezik a bővített cím canonicaljával (napvirág 225,
    serafim 271 cím). Amit a szabály nem fed le (más szegmensű canonical), az feloldatlan
    marad. A `links` tábla nem változik."""
    return _link_targets(con, _stored_links(con))


def _languages(con: duckdb.DuckDBPyConnection) -> set[str]:
    """A site nyelvkódjai (elsődleges alcímke, kisbetűvel): a site-profil nyelvei és az oldalak
    hreflang nyelvei."""
    profile = site(con)
    tags = list((profile.languages if profile else None) or [])
    for meta in page_metas(con):
        tags += [entry.split("|", 1)[0] for entry in meta.hreflang if "|" in entry]
    return {tag.strip().replace("_", "-").split("-", 1)[0].lower() for tag in tags if tag.strip()}


def _link_targets(con: duckdb.DuckDBPyConnection, rows: list[dict]) -> dict[str, int]:
    slugs: dict[tuple[str, str], list[int]] = {}
    for page in pages(con):
        parts = urlsplit(page.url)
        segments = parts.path.split("/")[1:]
        if not parts.query and len(segments) == 1 and segments[0]:
            slugs.setdefault((parts.netloc, segments[0]), []).append(page.page_id)
    languages: set[str] | None = None
    found: dict[str, int] = {}
    for row in rows:
        url = row["to_url"]
        if row["to_page_id"] is not None or url in found:
            continue
        parts = urlsplit(url)
        segments = parts.path.split("/")[1:]
        if parts.query or len(segments) < 2 or not all(segments):
            continue
        targets = slugs.get((parts.netloc, segments[-1]), [])
        if len(targets) != 1:
            continue
        if LANGUAGE_SEGMENT.match(segments[0]):
            if languages is None:
                languages = _languages(con)
            if segments[0].replace("_", "-").split("-", 1)[0].lower() in languages:
                continue
        found[url] = targets[0]
    return found


def json_ld(con: duckdb.DuckDBPyConnection) -> list[StructuredData]:
    """Az érvényes JSON-LD blokkok (a hibás JSON nélkül) az oldal és a sorszám szerint."""
    return [StructuredData.from_row({**row, "syntax": "json-ld"}) for row in _rows(
        con, "SELECT page_id, type, json, ordinal FROM schema_blocks "
             f"WHERE type IS DISTINCT FROM 'invalid' AND page_id IN {CURRENT_IDS} "
             "ORDER BY page_id, ordinal, type, json")]


def structured_data(con: duckdb.DuckDBPyConnection) -> list[StructuredData]:
    """A microdata-, RDFa- és Open Graph-elemek az oldal és a sorszám szerint."""
    return [StructuredData.from_row(row) for row in _rows(
        con, "SELECT page_id, syntax, type, json, ordinal FROM structured_data "
             f"WHERE page_id IN {CURRENT_IDS} ORDER BY page_id, ordinal, syntax, type, json")]


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
                      f"AND s.page_id IN {CURRENT_IDS} "
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


def crawl_runs(con: duckdb.DuckDBPyConnection) -> list[CrawlRun]:
    """A crawl-futások sorban."""
    return [CrawlRun.from_row(row) for row in _rows(con, "SELECT * FROM crawl_runs ORDER BY run_id")]


def queue_size(con: duckdb.DuckDBPyConnection) -> int:
    """A crawl-sor mérete (ezt korlátozza a `max_pages`)."""
    return con.execute("SELECT count(*) FROM crawl_queue").fetchone()[0]


def sitemap_files(con: duckdb.DuckDBPyConnection) -> list[SitemapFile]:
    """A lekért sitemap-fájlok pillanatképenként, a lekérés sorrendjében."""
    return [SitemapFile.from_row(row) for row in _rows(
        con, "SELECT * FROM sitemap_files ORDER BY snapshot, ordinal")]


def sitemap_urls(con: duckdb.DuckDBPyConnection) -> list[SitemapUrl]:
    """A sitemap címei pillanatképenként, a fájlbeli sorrendben."""
    return [SitemapUrl.from_row(row) for row in _rows(
        con, "SELECT * FROM sitemap_urls ORDER BY snapshot, ordinal")]


def queue_status_counts(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int]]:
    """A crawl-sor elemeinek száma állapotonként, állapot szerint rendezve."""
    return con.execute(
        "SELECT status, count(*) FROM crawl_queue GROUP BY status ORDER BY status").fetchall()
