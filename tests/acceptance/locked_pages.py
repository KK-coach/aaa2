"""Zárolt tesztoldalak: site-onként 3 URL, determinisztikusan, a tartalom megnézése nélkül.

    python -m tests.acceptance.locked_pages [--data-dir data/compare]

Szabály (`select`), site-onként, a crawl-adatbázis `pages` táblájából (csak az `url`,
`status`, `final_url`, `canonical`, `hreflang` oszlop):

1. csak a 2xx státuszú URL;
2. kizárva a fejlesztési oldal és a fordításai: a fejlesztési URL, a `hreflang`-jében szereplő
   URL-ek, azok az URL-ek, amelyeknek a `hreflang`-je a fejlesztési URL-t tartalmazza, a
   lekérdezés nélkül vele azonos URL-ek (`?tab=` változatok), és amelyek ezek egyikére
   irányítanak át (`final_url`);
3. egy oldal egyszer: kimarad az URL, amelynek a `final_url`-je vagy a `canonical`-ja egy
   másik, a listában maradt URL;
4. a maradék az URL (UTF-8) `sha256`-ja szerint növekvő sorrendben, az első 3.

A kimenet: `tests/acceptance/locked_pages.json` (a szabály és az URL-lista).
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

OUT = Path(__file__).parent / "locked_pages.json"
PER_SITE = 3
DEVELOPMENT = {
    "kk-coach-crawl": "https://kk.coach/hu/megoldasok/meres/",
    "materia-crawl": "https://materia-tm.com/hu/etlap/",
    "ngx-bootstrap-crawl": "https://valor-software.com/ngx-bootstrap/components/accordion",
}
RULE = [
    "csak a 2xx státuszú URL",
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


def select(rows: Sequence[Mapping], development: str, n: int = PER_SITE) -> list[str]:
    """A szabály egy site soraira (`url`, `status`, `final_url`, `canonical`, `hreflang`)."""
    ok = [r for r in rows if r["status"] is not None and 200 <= r["status"] < 300]
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
    return sorted(unique, key=sha256)[:n]


def read_rows(db: Path) -> list[dict]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        cursor = con.execute("SELECT url, status, final_url, canonical, hreflang FROM pages")
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    finally:
        con.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    sites = {}
    for name, development in DEVELOPMENT.items():
        sites[name] = {"development": development,
                       "urls": select(read_rows(args.data_dir / f"{name}.duckdb"), development)}
    args.out.write_text(json.dumps({"rule": RULE, "sites": sites}, ensure_ascii=False,
                                   indent=1) + "\n", encoding="utf-8")
    for name, site in sites.items():
        print(name, *site["urls"], sep="\n  ")


if __name__ == "__main__":
    main()
