"""A riport első köre: ténykimenet CSV-ben, értékelés és ajánlás nélkül.

A crawl begyűjtött, de eddig ki nem írt tényei három fájlban (`export_facts`):

- `<név>-view-links.csv`: a belső linkek soronként: honnan, hová, horgonyszöveg, pozíció
  (nav, body, aside, footer), nofollow. A crawl csak a belső linkeket tárolja; a külső linkekből
  oldalanként a darabszám van meg (az oldalnézetben).
- `<név>-view-structured-data.csv`: oldalanként a tárolt strukturált adat elemei: url, formátum
  (JSON-LD, microdata, RDFa, Open Graph), típus. Egy sor egy tárolt elem (a JSON-LD `@graph`
  csomópontjai külön elemek); értékelés nincs.
- `<név>-view-site-facts.csv`: a site tényei kulcs–érték sorokban: nyelvek, célország és
  megbízhatósága, piaci hatókör, technológia és a nyers technológiai jelek, robots.txt,
  HTTPS-átirányítás, záró perjel, crawl-számok (oldalak, státuszok megoszlása).

A modul csak a crawl-modul szerződésein át olvas (`aaa2.engine.queries`: `Site`, `Page`, `Link`,
`StructuredData`, `CrawlRun`); az entitás- és a gráfréteghez nem nyúl. Ami nincs tárolva, az
üres marad, a modul semmit nem gyűjt be újra."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import duckdb

from aaa2.engine import queries as crawl

SYNTAX_LABELS = {"json-ld": "JSON-LD", "microdata": "microdata", "rdfa": "RDFa",
                 "opengraph": "Open Graph"}
LINK_COLUMNS = ("honnan", "hová", "horgonyszöveg", "pozíció", "nofollow")
STRUCTURED_COLUMNS = ("url", "formátum", "típus")
FACT_COLUMNS = ("tény", "érték")


def yes_no(value: bool | None) -> str:
    """igen / nem; üres, ha az érték nincs tárolva."""
    return "" if value is None else "igen" if value else "nem"


def link_rows(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """A belső linkek a forrásoldal URL-je és a DOM-sorrend szerint."""
    urls = {page.page_id: page.url for page in crawl.pages(con)}
    rows = [(urls.get(link.from_page_id, ""), link.ordinal if link.ordinal is not None else -1,
             {"honnan": urls.get(link.from_page_id, ""), "hová": link.to_url,
              "horgonyszöveg": link.anchor or "", "pozíció": link.position,
              "nofollow": yes_no(bool(link.nofollow))})
            for link in crawl.links(con)]
    rows.sort(key=lambda row: (row[0], row[1], row[2]["hová"], row[2]["horgonyszöveg"],
                               row[2]["pozíció"], row[2]["nofollow"]))
    return [row for _, _, row in rows]


def structured_rows(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """A tárolt strukturált adat elemei az oldal URL-je, a formátum és a sorszám szerint."""
    urls = {page.page_id: page.url for page in crawl.pages(con)}
    items = [*crawl.json_ld(con), *crawl.structured_data(con)]
    rows = [(urls.get(item.page_id, ""), list(SYNTAX_LABELS).index(item.syntax),
             item.ordinal if item.ordinal is not None else -1,
             {"url": urls.get(item.page_id, ""), "formátum": SYNTAX_LABELS[item.syntax],
              "típus": item.type or ""}) for item in items]
    rows.sort(key=lambda row: (row[0], row[1], row[2], row[3]["típus"]))
    return [row for *_, row in rows]


def site_fact_rows(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """A site tényei kulcs–érték sorokban; a nem tárolt érték üres."""
    site = crawl.site(con)
    pages = crawl.pages(con)
    run = crawl.latest_crawl_run(con)
    facts: list[tuple[str, object]] = []
    if site is not None:
        scope = site.market_scope or ""
        if site.market_scope_city:
            scope += f" ({site.market_scope_city})"
        redirect = yes_no(site.https_redirect)
        if site.https_redirect_status is not None:
            redirect += f" ({site.https_redirect_status})"
        facts += [
            ("domain", site.domain), ("kezdő URL", site.seed_url),
            ("nyelvek", ", ".join(site.languages or [])),
            ("célország", site.target_country or ""),
            ("célország megbízhatósága", site.target_country_confidence or ""),
            ("piaci hatókör", scope),
            ("technológia", ", ".join(site.tech or [])),
            ("technológiai jelek", ", ".join(site.tech_signals or [])),
            ("megjelenítés módja", site.render_mode or ""),
            ("robots.txt státusz", "" if site.robots_status is None else site.robots_status),
            ("robots.txt", site.robots_txt or ""),
            ("HTTPS-átirányítás", redirect),
            ("záró perjel", yes_no(site.trailing_slash)),
        ]
    statuses = Counter("nincs válasz" if page.status is None else str(page.status)
                       for page in pages)
    facts += [("oldalak száma", len(pages)),
              ("megjelenített oldalak száma", sum(1 for page in pages if page.renderable)),
              ("noindex oldalak száma", sum(1 for page in pages if page.noindex))]
    facts += [(f"oldalak státusz szerint: {status}", count)
              for status, count in sorted(statuses.items())]
    if run is not None:
        facts += [("crawl: oldalkorlát", "" if run.max_pages is None else run.max_pages),
                  ("crawl: kész oldal", "" if run.pages_done is None else run.pages_done),
                  ("crawl: hibás oldal", "" if run.pages_failed is None else run.pages_failed),
                  ("crawl: kihagyott oldal",
                   "" if run.pages_skipped is None else run.pages_skipped)]
    return [{"tény": key, "érték": value} for key, value in facts]


def export_facts(con: duckdb.DuckDBPyConnection, out: Path, name: str) -> dict[str, Path]:
    """A három tényfájl (lásd a modul leírását); visszaad: kulcs → útvonal."""
    out.mkdir(parents=True, exist_ok=True)
    files = {"links": ("view-links", LINK_COLUMNS, link_rows(con)),
             "structured": ("view-structured-data", STRUCTURED_COLUMNS, structured_rows(con)),
             "site_facts": ("view-site-facts", FACT_COLUMNS, site_fact_rows(con))}
    paths = {}
    for key, (suffix, columns, rows) in files.items():
        path = out / f"{name}-{suffix}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns))
            writer.writeheader()
            writer.writerows(rows)
        paths[key] = path
    return paths
