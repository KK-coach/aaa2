"""Zárolt tesztoldalak: site-onként 3 URL, determinisztikusan, a tartalom megnézése nélkül.

    python -m tests.acceptance.locked_pages [--data-dir data/compare]

Szabály (`select`), site-onként, a crawl-adatbázis `pages` táblájából (csak az `url`,
`status`, `final_url`, `canonical`, `hreflang`, `word_count` oszlop; a `word_count` a
`main_content` szavainak száma):

1. csak a 2xx státuszú URL, legalább 150 szavas main contenttel;
2. kizárva a fejlesztési oldal és a fordításai: a fejlesztési URL, a `hreflang`-jében szereplő
   URL-ek, azok az URL-ek, amelyeknek a `hreflang`-je a fejlesztési URL-t tartalmazza, a
   lekérdezés nélkül vele azonos URL-ek (`?tab=` változatok), és amelyek ezek egyikére
   irányítanak át (`final_url`);
3. egy oldal egyszer: kimarad az URL, amelynek a `final_url`-je vagy a `canonical`-ja egy
   másik, a listában maradt URL;
4. a maradék az URL (UTF-8) `sha256`-ja szerint növekvő sorrendben, az első 3.

Az idegen site-okon (M2/7 B, `M27_SITES`) nincs fejlesztési oldal (a 2. pont üres). A webshopon
előbb minden kötelező oldaltípusból (`REQUIRED`: termék, kategória; `pages.page_types`, a
site-fájl mintáival) a legkisebb `sha256`-ú oldal, a maradék hely a többi oldalból a 4. pont
szerint. A tartalom ekkor sem számít: az oldaltípus a JSON-LD típusából és az URL-szerkezetből jön.

A kimenet: `tests/acceptance/locked_pages.json` (a szabály és az URL-lista; a meglévő site-ok
listája nem változik, az új site-ok hozzákerülnek).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import duckdb

from aaa2.db.connect import DATA_DIR
from aaa2.resolver.overrides import load_site_config
from aaa2.resolver.pages import page_types

OUT = Path(__file__).parent / "locked_pages.json"
PER_SITE = 3
MIN_WORDS = 150
DEVELOPMENT = {
    "kk-coach-crawl": "https://kk.coach/hu/megoldasok/meres/",
    "materia-crawl": "https://materia-tm.com/hu/etlap/",
    "ngx-bootstrap-crawl": "https://valor-software.com/ngx-bootstrap/components/accordion",
}
M27_SITES = {"marketinglens-crawl": "marketinglens.com", "duex-crawl": "duexhungary.hu"}
REQUIRED = {"duex-crawl": ("product", "category")}
M27_RULE = ("M2/7 B, idegen site-ok: nincs fejlesztési oldal; a webshopon előbb a termék- és a "
            "kategória-oldaltípus legkisebb sha256-ú oldala (pages.page_types: JSON-LD és a "
            "site-fájl URL-mintái), a maradék hely sha256 szerint")
RULE = [
    "csak a 2xx státuszú URL, legalább 150 szavas main contenttel (word_count)",
    ("kizárva a fejlesztési oldal és a fordításai (hreflang mindkét irányban), a lekérdezés "
     "nélkül vele azonos URL-ek (?tab= változatok) és az ezekre átirányító URL-ek"),
    ("egy oldal egyszer: kimarad az URL, amelynek a final_url-je vagy a canonical-ja egy másik, "
     "a listában maradt URL"),
    "a maradék az URL (UTF-8) sha256-ja szerint növekvő sorrendben, site-onként az első 3",
]


def _without_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _hreflang_urls(entries: Sequence[str] | None) -> set[str]:
    return {entry.split("|", 1)[1] for entry in entries or [] if "|" in entry}


def sha256(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def select(rows: Sequence[Mapping], development: str | None, n: int = PER_SITE,
           min_words: int = MIN_WORDS, required: Sequence[str] = ()) -> list[str]:
    """A szabály egy site soraira (`url`, `status`, `final_url`, `canonical`, `hreflang`,
    `word_count`, és ha van `required`, `type`)."""
    ok = [r for r in rows if r["status"] is not None and 200 <= r["status"] < 300
          and (r["word_count"] or 0) >= min_words]
    dev = next((r for r in ok if r["url"] == development), None)
    excluded = {development, *_hreflang_urls(dev["hreflang"] if dev else None)}
    excluded |= {r["url"] for r in ok if development in _hreflang_urls(r["hreflang"])}
    stems = {_without_query(u) for u in excluded}
    excluded |= {r["url"] for r in ok if _without_query(r["url"]) in stems}
    excluded |= {r["url"] for r in ok if r["final_url"] in excluded}
    left = {r["url"]: r for r in ok if r["url"] not in excluded}
    unique = [url for url, r in left.items()
              if not any(other != url and other in left
                         for other in (r["final_url"], r["canonical"]))]
    ordered = sorted(unique, key=sha256)
    kinds = {r["url"]: r.get("type") for r in rows}
    first = [next(url for url in ordered if kinds[url] == kind) for kind in required
             if any(kinds[url] == kind for url in ordered)]
    return (first + [url for url in ordered if url not in first])[:n]


def read_rows(db: Path, domain: str | None = None) -> list[dict]:
    """A `pages` sorai; ha van `domain`, az oldaltípussal (`type`) a site-fájl mintáival."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        cursor = con.execute("SELECT page_id, url, status, final_url, canonical, hreflang, "
                             "word_count FROM pages")
        names = [d[0] for d in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        if domain is not None:
            kinds = page_types(con, load_site_config(domain).page_types)
            for row in rows:
                row["type"] = kinds.get(row["page_id"])
        return rows
    finally:
        con.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--m27", action="store_true",
                        help="csak az idegen site-ok (M2/7 B) kerülnek a meglévő listához")
    args = parser.parse_args(argv)
    if args.m27:
        data = json.loads(args.out.read_text(encoding="utf-8"))
        sites = {}
        for name, domain in M27_SITES.items():
            rows = read_rows(args.data_dir / f"{name}.duckdb", domain)
            urls = select(rows, None, required=REQUIRED.get(name, ()))
            kinds = {r["url"]: r["type"] for r in rows}
            sites[name] = {"development": None, "urls": urls,
                           "types": [kinds[url] for url in urls]}
        rule = [*data["rule"], *([M27_RULE] if M27_RULE not in data["rule"] else [])]
        data = {"rule": rule, "sites": {**data["sites"], **sites}}
    else:
        sites = {}
        for name, development in DEVELOPMENT.items():
            sites[name] = {"development": development,
                           "urls": select(read_rows(args.data_dir / f"{name}.duckdb"),
                                          development)}
        data = {"rule": RULE, "sites": sites}
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    for name, site in sites.items():
        print(name, *site["urls"], sep="\n  ")


if __name__ == "__main__":
    main()
