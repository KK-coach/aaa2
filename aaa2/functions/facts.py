"""A riport első köre: ténykimenet CSV-ben, értékelés és ajánlás nélkül.

A crawl begyűjtött, de eddig ki nem írt tényei négy fájlban (`export_facts`):

- `<név>-view-links.csv`: a belső linkek soronként: honnan, hová (a link eredeti célja),
  feloldott céloldal (a készletbeli URL), feloldás módja (tárolt / következtetett),
  horgonyszöveg, pozíció (nav, body, aside, footer), nofollow. A crawl csak a belső linkeket tárolja; a külső linkekből
  oldalanként a darabszám van meg (az oldalnézetben).
- `<név>-view-structured-data.csv`: oldalanként a tárolt strukturált adat elemei: url, formátum
  (JSON-LD, microdata, RDFa, Open Graph), típus. Egy sor egy tárolt elem (a JSON-LD `@graph`
  csomópontjai külön elemek); értékelés nincs.
- `<név>-view-site-facts.csv`: a site tényei kulcs–érték sorokban: nyelvek, célország és
  megbízhatósága, piaci hatókör, technológia és a nyers technológiai jelek, robots.txt,
  HTTPS-átirányítás, záró perjel, crawl-számok (oldalak, státuszok megoszlása), a crawl módja,
  és a sitemap tényei (van-e, honnan került elő, hivatkozza-e a robots.txt, hány címet ad,
  hány különböző oldal lett belőle, a lekérés ideje).
- `<név>-view-sitemap.csv`: normalizált címenként egy sor: a sitemap és a crawl összevetése
  (`sitemap_rows`). Megállapítás nincs benne; az árva oldal, a hiányos sitemap és a nem oda
  való cím a sorokból kiolvasható.

A modul csak a crawl-modul szerződésein át olvas (`aaa2.engine.queries`: `Site`, `Page`, `Link`,
`StructuredData`, `CrawlRun`); az entitás- és a gráfréteghez nem nyúl. Ami nincs tárolva, az
üres marad, a modul semmit nem gyűjt be újra."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import duckdb

from aaa2.engine import queries as crawl
from aaa2.resolver.pages import canonical_key

SYNTAX_LABELS = {"json-ld": "JSON-LD", "microdata": "microdata", "rdfa": "RDFa",
                 "opengraph": "Open Graph"}
LINK_COLUMNS = ("honnan", "hová", "feloldott céloldal", "feloldás módja", "horgonyszöveg",
                "pozíció", "nofollow")
RESOLUTION_LABELS = {"stored": "tárolt", "inferred": "következtetett", None: ""}
STRUCTURED_COLUMNS = ("url", "formátum", "típus")
FACT_COLUMNS = ("tény", "érték")
SITEMAP_COLUMNS = ("cím", "helyzet", "sitemap-címek száma", "eltérő nyers alakok",
                   "a sitemapben a crawl idején", "a sitemapben ma", "bejártuk", "státusz",
                   "noindex", "canonical máshová mutat", "hivatkozó oldalak", "lastmod", "bejárás")
SOURCE_LABELS = {"given": "megadott cím", "robots": "robots.txt", "default": "alapútvonal",
                 "queue": "a crawl-sorból visszaállítva"}
MODE_LABELS = {"links": "link alapú", "sitemap": "sitemap-mód", None: ""}
SEED_UNKNOWN = "bejártuk, a sitemapben: nem ismert (kezdő URL)"
IN_AND_CRAWLED = "sitemapben van, bejártuk"
IN_NOT_CRAWLED = "sitemapben van, nem jártuk be"
CRAWLED_NOT_IN = "bejártuk, nincs a sitemapben"
LINKED_ONLY = "hivatkozott, nem bejárt, nincs a sitemapben"
PARTIAL = "részleges"


def yes_no(value: bool | None) -> str:
    """igen / nem; üres, ha az érték nincs tárolva."""
    return "" if value is None else "igen" if value else "nem"


def link_rows(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """A belső linkek a forrásoldal URL-je és a DOM-sorrend szerint. A „hová” a link eredeti
    célja; a „feloldott céloldal” a készletbeli oldal URL-je, amelyre a link mutat; üres, ha
    a cél nincs a készletben. A „feloldás módja”: `tárolt` (a crawl tárolta: a link célja a
    készletbeli cím) vagy `következtetett` (a kategóriaúttal bővített cím feloldása a tárolt
    címekből, `crawl.link_targets`): a következtetett cél nem ugyanolyan tény, mint a tárolt.
    A nem tárolt nofollow üres."""
    urls = {page.page_id: page.url for page in crawl.pages(con)}
    rows = []
    for link in crawl.links(con):
        target = link.to_page_id
        rows.append((urls.get(link.from_page_id, ""),
                     link.ordinal if link.ordinal is not None else -1,
                     {"honnan": urls.get(link.from_page_id, ""), "hová": link.to_url,
                      "feloldott céloldal": urls.get(target, "") if target is not None else "",
                      "feloldás módja": RESOLUTION_LABELS[link.resolution],
                      "horgonyszöveg": link.anchor or "", "pozíció": link.position,
                      "nofollow": yes_no(link.nofollow)}))
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
    facts += sitemap_facts(con)
    return [{"tény": key, "érték": value} for key, value in facts]


def crawl_state(con: duckdb.DuckDBPyConnection) -> tuple[str | None, bool]:
    """(a crawl módja, megállt-e az oldalkorláton vagy idő előtt) a crawl-modul teljességi
    állapotából (`crawl.completeness`)."""
    state = crawl.completeness(con)
    return state.mode, state.limited


def sitemap_facts(con: duckdb.DuckDBPyConnection) -> list[tuple[str, object]]:
    """A crawl módja és a sitemap tényei. A sitemap számai a legfrissebb tárolt pillanatképből
    jönnek (utólagos lekérés, ha van; különben a crawl idején tárolt), a pillanatkép
    megnevezésével; a crawl idején a sitemapből jött címek száma külön sor."""
    site = crawl.site(con)
    mode, limited = crawl_state(con)
    files, urls = crawl.sitemap_files(con), crawl.sitemap_urls(con)
    snapshots = {item.snapshot for item in [*files, *urls]}
    current = "refetch" if "refetch" in snapshots else "crawl" if "crawl" in snapshots else None
    mine = [item for item in urls if item.snapshot == current]
    my_files = [item for item in files if item.snapshot == current]
    found = [item for item in my_files if item.found]
    source = next((item.source for item in [*my_files, *mine]), None)
    at_crawl = [item for item in urls if item.snapshot == "crawl"]
    robots_lines = [line for line in (site.robots_txt or "").splitlines()
                    if line.strip().lower().startswith("sitemap:")] if site else []
    fetched = next((item.fetched_at for item in [*my_files, *mine] if item.fetched_at), None)
    if current is None:
        has = ""
    elif source == "queue":
        has = "igen" if mine else ""
    else:
        has = "igen" if found else "nem"
    pages = {item.url for item in mine if item.url and item.internal}
    state = crawl.completeness(con)
    return [
        ("crawl módja", MODE_LABELS.get(mode, mode or "")),
        ("a crawl az oldalkorláton állt meg", yes_no(limited) if state.crawled else ""),
        ("crawl: a korlát miatt kimaradt címek",
         "" if state.skipped_by_limit is None else state.skipped_by_limit),
        ("crawl: a megállás oka", state.stopped or ""),
        ("crawl: hatókör (include)", state.include or ""),
        ("crawl: hatókör (exclude)", state.exclude or ""),
        ("crawl: a bejárás teljessége", "; ".join(state.states)),
        ("sitemap", has),
        ("sitemap: honnan került elő", SOURCE_LABELS.get(source, "")),
        ("sitemap: a robots.txt hivatkozza",
         "" if site is None or site.robots_txt is None else yes_no(bool(robots_lines))),
        ("sitemap: fájlok", " | ".join(item.url for item in found)),
        ("sitemap: címek száma", len(mine) if current else ""),
        ("sitemap: különböző oldalak", len(pages) if current else ""),
        ("sitemap: a site-on kívüli címek",
         sum(1 for item in mine if not item.internal or not item.url) if current else ""),
        ("sitemap: az adat forrása",
         {"refetch": "utólagos lekérés", "crawl": "a crawl része", None: ""}[current]
         if source != "queue" else "a crawl-sorból visszaállítva"),
        ("sitemap: a lekérés ideje", fetched.strftime("%Y-%m-%d %H:%M") if fetched else ""),
        ("sitemap: címek a crawl idején (különböző oldal)",
         len({item.url for item in at_crawl if item.url}) if at_crawl else ""),
    ]


def sitemap_rows(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """A sitemap és a crawl összevetése, normalizált címenként egy sor, cím szerint.

    - `helyzet`: sitemapben van, bejártuk / sitemapben van, nem jártuk be / bejártuk, nincs a
      sitemapben / hivatkozott, nem bejárt, nincs a sitemapben. Az utolsó csak sitemap-módban
      készül: ott a crawl nem követi a sitemapen kívüli linkeket, ezek a címek csak a tárolt
      linkekből ismertek, státusz és noindex nélkül. Link alapú crawlnál a „bejártuk, nincs a
      sitemapben” irány teljes (a crawl minden belső linket követ), sitemap-módban nem.
    - `sitemap-címek száma`: hány sitemap-cím esik erre a normalizált címre (a legfrissebb
      pillanatképben); `eltérő nyers alakok`: a nyers címek, ha valamelyik nem a normalizált alak.
    - `a sitemapben a crawl idején` / `ma`: a `crawl`, ill. a `refetch` pillanatkép szerint;
      üres, ha az a pillanatkép nincs tárolva. Ha a crawl idejének sitemapje a crawl-sorból van
      visszaállítva, a kezdő URL-ről nem tudjuk, szerepelt-e (a sorban seedként áll): ott az
      érték üres, és ha mai lekérés sincs, a helyzete „nem ismert”.
    - `bejártuk`, `státusz`, `noindex`, `canonical máshová mutat`, `hivatkozó oldalak`: a crawl
      tárolt oldalaiból és linkjeiből; a nem bejárt címnél üres (a hivatkozó oldalak száma a
      tárolt linkekből akkor is megvan).
    - `bejárás`: `részleges`, ha a crawl az oldalkorláton vagy idő előtt állt meg: ilyenkor a
      bejárásfüggő oszlopok (bejártuk, státusz, noindex, canonical, hivatkozó oldalak) nem
      teljesek, a „nem jártuk be” nem jelenti, hogy az oldal nem érhető el.
    A site-on kívüli és a nem normalizálható sitemap-címek nem sorok (számuk a site-tények
    között áll)."""
    mode, limited = crawl_state(con)
    urls = crawl.sitemap_urls(con)
    snapshots = {item.snapshot for item in urls} | {f.snapshot for f in crawl.sitemap_files(con)}
    profile = crawl.site(con)
    # a sorból visszaállított pillanatképben a kezdő URL helye nem ismert
    unknown = profile.seed_url if profile is not None and any(
        item.snapshot == "crawl" and item.source == "queue" for item in urls) else None
    current = "refetch" if "refetch" in snapshots else "crawl"
    in_snapshot: dict[str, set[str]] = {"crawl": set(), "refetch": set()}
    raw: dict[str, list[str]] = {}
    lastmod: dict[str, str] = {}
    for item in urls:
        if not item.url or not item.internal:
            continue
        in_snapshot[item.snapshot].add(item.url)
        if item.snapshot == current:
            raw.setdefault(item.url, []).append(item.raw_url or item.url)
            if item.lastmod and item.url not in lastmod:
                lastmod[item.url] = item.lastmod
    pages = {page.url: page for page in crawl.pages(con)}
    by_id = {page.page_id: page.url for page in pages.values()}
    canonical = {meta.page_id: meta.canonical for meta in crawl.page_metas(con)}
    referrers: dict[str, set[int]] = {}
    for link in crawl.links(con):
        target = by_id.get(link.to_page_id) if link.to_page_id is not None else link.to_url
        if target and by_id.get(link.from_page_id) != target:
            referrers.setdefault(target, set()).add(link.from_page_id)
    in_any = in_snapshot["crawl"] | in_snapshot["refetch"]
    linked = set(referrers) - set(pages) - in_any if mode == "sitemap" else set()
    rows = []
    for url in sorted(in_any | set(pages) | linked):
        page = pages.get(url)
        if url in in_any:
            state = IN_AND_CRAWLED if page is not None else IN_NOT_CRAWLED
        elif url == unknown and page is not None:
            state = SEED_UNKNOWN
        else:
            state = CRAWLED_NOT_IN if page is not None else LINKED_ONLY
        forms = raw.get(url, [])
        elsewhere = ""
        if page is not None:
            target = canonical.get(page.page_id)
            elsewhere = "" if not target else yes_no(canonical_key(target) != canonical_key(url))
        rows.append({
            "cím": url, "helyzet": state, "sitemap-címek száma": len(forms),
            "eltérő nyers alakok": " | ".join(forms) if any(form != url for form in forms) else "",
            "a sitemapben a crawl idején": yes_no(url in in_snapshot["crawl"])
            if "crawl" in snapshots and url != unknown else "",
            "a sitemapben ma": yes_no(url in in_snapshot["refetch"])
            if "refetch" in snapshots else "",
            "bejártuk": yes_no(page is not None),
            "státusz": "" if page is None or page.status is None else page.status,
            "noindex": "" if page is None else yes_no(page.noindex),
            "canonical máshová mutat": elsewhere,
            "hivatkozó oldalak": len(referrers.get(url, ())),
            "lastmod": lastmod.get(url, ""),
            "bejárás": PARTIAL if limited else "teljes"})
    return rows


def export_facts(con: duckdb.DuckDBPyConnection, out: Path, name: str) -> dict[str, Path]:
    """A négy tényfájl (lásd a modul leírását); visszaad: kulcs → útvonal."""
    out.mkdir(parents=True, exist_ok=True)
    files = {"links": ("view-links", LINK_COLUMNS, link_rows(con)),
             "structured": ("view-structured-data", STRUCTURED_COLUMNS, structured_rows(con)),
             "site_facts": ("view-site-facts", FACT_COLUMNS, site_fact_rows(con)),
             "sitemap": ("view-sitemap", SITEMAP_COLUMNS, sitemap_rows(con))}
    paths = {}
    for key, (suffix, columns, rows) in files.items():
        path = out / f"{name}-{suffix}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns))
            writer.writeheader()
            writer.writerows(rows)
        paths[key] = path
    return paths
