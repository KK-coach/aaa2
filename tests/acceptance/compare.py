"""Screaming Frog `Internal:HTML` export és egy site DuckDB-jének összevetése.

Az SF-címeket az aaa normalizálja a site szabályaival (`site.seed_url`, `https_redirect`,
`trailing_slash`; trailing slash, percent-encoding, query), a két halmaz ezen a kulcson
találkozik. Minden SF-sor, amelynek címét a normalizálás átírja, `normalizálás` sorként kerül a
kimenetbe; ez nem eltérés. Ha több SF-sor ugyanarra a kulcsra esik, a kulccsal azonos alakú
képviseli (különben a 2xx-es, különben az első).

Opcionális SF-exportok:

- `--sf-response-codes` (`Response Codes:All`): a csak az aaa-ban szereplő URL-ek magyarázatához;
  ami ott szerepel, azt az SF is látta, csak nem HTML-oldalként;
- `--sf-outlinks` (`Links:All Outlinks` bulk export): linkszintű összevetés. Az SF JS-renderes
  crawlja a nyers HTML linkjeit is számolja (`Link Origin: HTML`); az aaa a renderelt DOM-ot
  tárolja. Linkszinten a renderelt linkek (`Rendered HTML`, `HTML & Rendered HTML`) vetődnek
  össze, a csak nyers HTML-ben lévők külön oszlopban, célonként.

Kimenet a `--out` könyvtárba:

- `url_diff.csv`: az egyik oldalon hiányzó URL-ek magyarázattal, és a `normalizálás` sorok.
  Elfogadott magyarázat: redirect, 404, noindex, robots, scope (nem a seed hostja, vagy az
  include/exclude kizárja), normalizálás. Minden más magyarázatlan eltérés, a megnevezésével:
  `http NNN`, nincs válasz, render-hiba, nem HTML, sitemap (csak sitemapből induló láncon
  érhető el, SF által ismert oldal nem linkel rá), hreflang, canonical (csak hreflang- vagy
  canonical-cél), limit (az aaa elérte a `max_pages`-t), `?`;
- `status_diff.csv`: a közös URL-ek eltérő státuszkódjai;
- `link_diff.csv`: a közös, mindkét oldalon 2xx oldalak belső linkjei, ha a tűrésen kívül esnek:
  az SF `Internal:HTML` számaiból, és ha van `--sf-outlinks`, linkszinten is, a hiányzó és a
  többlet célokkal (forrásokkal);
- `summary.md`: szám-összegzés és az elfogadási feltételek.

Linkek az aaa-oldalon: csak a seed hostjára mutató, az include/exclude által átengedett célok.
Kimenő: a linksorok száma (`Outlinks`) és a különböző célok száma (`Unique Outlinks`). Bejövő: a
linksorok (`Inlinks`) és a különböző forrásoldalak (`Unique Inlinks`), csak olyan forrásból,
amelyet mindkét oldal crawlolt. Tűrésen kívül: |aaa − SF| > tűrés × SF.

Kilépési kód: 0, ha az URL-halmaz eltérése legfeljebb `--max-diff` (2%) és minden eltérés
magyarázott; különben 1.

    python -m tests.acceptance.compare --sf-csv internal_html.csv \\
        --sf-response-codes response_codes_all.csv --sf-outlinks all_outlinks.csv \\
        --db data/x.duckdb --out r/
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import duckdb

from aaa2.engine.frontier import PRIORITY, Robots
from aaa2.engine.normalize import UrlPolicy, is_infrastructure, is_internal, normalize

TOLERANCE = 0.05
MAX_DIFF = 0.02
ACCEPTED = frozenset({"redirect", "404", "noindex", "robots", "scope", "normalizálás"})
LINK_METRICS = (
    ("outlinks", "Outlinks"),
    ("unique_outlinks", "Unique Outlinks"),
    ("inlinks", "Inlinks"),
    ("unique_inlinks", "Unique Inlinks"),
)
LINK_FIELDS = ["url", "basis", "metric", "sf", "aaa", "diff_pct", "csak_sf", "csak_aaa",
               "sf_csak_nyers_html"]
MAX_CHAIN = 50


@dataclass(frozen=True)
class SfRow:
    url: str
    status: int | None
    status_text: str = ""
    content_type: str = ""
    indexability_status: str = ""
    redirect_url: str = ""
    counts: dict[str, int | None] = field(default_factory=dict)


@dataclass(frozen=True)
class SfLink:
    source: str
    destination: str
    origin: str
    type: str = "Hyperlink"

    @property
    def rendered(self) -> bool:
        return "rendered" in self.origin.lower()


@dataclass(frozen=True)
class AaaPage:
    page_id: int
    url: str
    status: int | None
    error: str | None
    noindex: bool
    final_url: str | None


@dataclass
class AaaSite:
    domain: str
    policy: UrlPolicy
    robots: Robots | None
    include: re.Pattern[str] | None
    exclude: re.Pattern[str] | None
    pages: dict[str, AaaPage]
    queue: dict[str, tuple[int, str | None]]
    link_sources: dict[str, set[str]]
    hreflang_targets: set[str]
    canonical_targets: set[str]
    max_pages: int | None
    outlinks: dict[str, list[str]]
    run: tuple | None
    rendered_bytes: int

    def in_sf_scope(self, url: str) -> bool:
        if (urlsplit(url).hostname or "") != self.policy.seed_host or is_infrastructure(url):
            return False
        if self.exclude and self.exclude.search(url):
            return False
        return not (self.include and not self.include.search(url))

    def sitemap_rooted(self, url: str) -> bool:
        """A felfedezési lánc (`crawl_queue.discovered_from`) sitemap-URL-ből indul."""
        for _ in range(MAX_CHAIN):
            priority, parent = self.queue.get(url, (None, None))
            if parent is None:
                return priority == PRIORITY["sitemap"]
            url = parent
        return False


@dataclass(frozen=True)
class Comparison:
    url_rows: list[dict[str, object]]
    status_rows: list[dict[str, object]]
    link_rows: list[dict[str, object]]
    summary: str
    passed: bool


def read_sf_csv(path: Path) -> list[SfRow]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [
            SfRow(
                url=(record.get("Address") or "").strip(),
                status=_int(record.get("Status Code")),
                status_text=(record.get("Status") or "").strip(),
                content_type=(record.get("Content Type") or "").strip(),
                indexability_status=(record.get("Indexability Status") or "").strip(),
                redirect_url=(record.get("Redirect URL") or "").strip(),
                counts={metric: _int(record.get(column)) for metric, column in LINK_METRICS},
            )
            for record in csv.DictReader(handle)
            if (record.get("Address") or "").strip()
        ]


def read_sf_outlinks(path: Path) -> list[SfLink]:
    """Az SF `Links:All Outlinks` exportjának sorai (hiperlink, canonical, hreflang, erőforrás)."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [
            SfLink(record["Source"].strip(), record["Destination"].strip(),
                   (record.get("Link Origin") or "").strip(), (record.get("Type") or "").strip())
            for record in csv.DictReader(handle)
        ]


def load_site(
    con: duckdb.DuckDBPyConnection, include: str | None = None, exclude: str | None = None
) -> AaaSite:
    domain, seed_url, https_redirect, trailing_slash, robots_txt = con.execute(
        "SELECT domain, seed_url, https_redirect, trailing_slash, robots_txt FROM site"
    ).fetchone()
    policy = UrlPolicy.from_seed(
        seed_url, https_redirect=bool(https_redirect), trailing_slash=trailing_slash)
    pages: dict[str, AaaPage] = {}
    hreflang_targets: set[str] = set()
    canonical_targets: set[str] = set()
    for page_id, url, status, error, noindex, final_url, canonical, hreflang in con.execute(
        "SELECT page_id, url, status, error, noindex, final_url, canonical, hreflang FROM pages"
    ).fetchall():
        pages[url] = AaaPage(page_id, url, status, error, bool(noindex), final_url)
        for pair in hreflang or ():
            if target := normalize(pair.split("|", 1)[-1], policy):
                hreflang_targets.add(target)
        if canonical and (target := normalize(canonical, policy)):
            canonical_targets.add(target)
    queue = {url: (priority, parent) for url, priority, parent in con.execute(
        "SELECT url, priority, discovered_from FROM crawl_queue").fetchall()}
    site = AaaSite(
        domain=domain, policy=policy,
        robots=Robots.parse(robots_txt) if robots_txt else None,
        include=re.compile(include) if include else None,
        exclude=re.compile(exclude) if exclude else None,
        pages=pages, queue=queue, link_sources=defaultdict(set),
        hreflang_targets=hreflang_targets, canonical_targets=canonical_targets,
        max_pages=None, outlinks=defaultdict(list), run=None, rendered_bytes=0,
    )
    for from_url, to_url in con.execute(
        "SELECT p.url, l.to_url FROM links l JOIN pages p ON p.page_id = l.from_page_id "
        "WHERE p.status BETWEEN 200 AND 299 AND p.error IS NULL ORDER BY l.from_page_id, l.ordinal"
    ).fetchall():
        site.link_sources[to_url].add(from_url)
        if site.in_sf_scope(to_url):
            site.outlinks[from_url].append(to_url)
    site.run = con.execute(
        "SELECT run_id, max_pages, pages_done, pages_failed, pages_skipped, pages_per_sec, "
        "started_at, finished_at FROM crawl_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    site.max_pages = site.run[1] if site.run else None
    (site.rendered_bytes,) = con.execute(
        "SELECT coalesce(sum(octet_length(rendered_html)), 0) FROM pages").fetchone()
    return site


def compare(
    sf_rows: list[SfRow], site: AaaSite, *, response_codes: list[SfRow] | None = None,
    outlinks: list[SfLink] | None = None, tolerance: float = TOLERANCE,
    max_diff: float = MAX_DIFF, db_bytes: int | None = None,
) -> Comparison:
    groups = _group(sf_rows, site.policy)
    representative = {key: _representative(key, rows) for key, rows in groups.items()}
    seen_by_sf = {key: _representative(key, rows)
                  for key, rows in _group(response_codes or [], site.policy).items()}

    found_by = _sf_discovery(outlinks or [], site.policy)
    url_rows: list[dict[str, object]] = []
    for key, rows in sorted(groups.items()):
        for row in rows:
            if row.url != key:
                url_rows.append(_url_row("normalizálás", key, row.url, row.status, None,
                                         "normalizálás"))
    sf_keys = set(groups)
    sf_only = sorted(sf_keys - set(site.pages))
    aaa_only = sorted(set(site.pages) - sf_keys)
    for key in sf_only:
        row = representative[key]
        url_rows.append(_url_row("csak_sf", key, row.url, row.status, None,
                                 explain_sf_only(row, key, site), found_by.get(key, "")))
    for key in aaa_only:
        page = site.pages[key]
        seen = seen_by_sf.get(key)
        url_rows.append(_url_row("csak_aaa", key, seen.url if seen else "",
                                 seen.status if seen else None, page.status,
                                 explain_aaa_only(page, site, seen, sf_keys),
                                 _aaa_discovery(key, site)))

    common = sorted(sf_keys & set(site.pages))
    status_rows = [
        {"url": key, "sf_status": representative[key].status, "aaa_status": site.pages[key].status,
         "aaa_error": site.pages[key].error or "", "aaa_final_url": site.pages[key].final_url or "",
         "sf_redirect_url": representative[key].redirect_url}
        for key in common if representative[key].status != site.pages[key].status
    ]
    ok_pages = [key for key in common if _ok(representative[key].status)
                and _ok(site.pages[key].status) and site.pages[key].error is None]
    count_rows = _count_link_rows(ok_pages, set(common), representative, site, tolerance)
    level_rows: list[dict[str, object]] | None = None
    raw_only: dict[str, Counter[str]] = {}
    if outlinks is not None:
        level_rows, raw_only = _level_link_rows(ok_pages, set(common), outlinks, site, tolerance)

    union = len(sf_keys | set(site.pages))
    differing = len(sf_only) + len(aaa_only)
    unexplained = sum(1 for r in url_rows
                      if r["side"] in ("csak_sf", "csak_aaa") and r["explanation"] not in ACCEPTED)
    passed = union > 0 and differing / union <= max_diff and unexplained == 0
    summary = _summary(
        site, sf_rows, groups, sf_only, aaa_only, url_rows, status_rows, len(ok_pages),
        count_rows, level_rows, raw_only, tolerance, max_diff, db_bytes, unexplained, passed,
    )
    return Comparison(url_rows, status_rows, count_rows + (level_rows or []), summary, passed)


def explain_sf_only(row: SfRow, key: str, site: AaaSite) -> str:
    """Az aaa scope-ján kívüli URL "scope", a státuszától függetlenül: az aaa le sem kéri."""
    if not is_internal(key, site.policy) or not site.in_sf_scope(key):
        return "scope"
    status_reason = _status_reason(row.status)
    if status_reason:
        return status_reason
    if "noindex" in row.indexability_status.lower():
        return "noindex"
    if site.robots and not site.robots.allowed(key):
        return "robots"
    if key in site.hreflang_targets:
        return "hreflang"
    if key in site.canonical_targets:
        return "canonical"
    if site.max_pages and len(site.pages) >= site.max_pages:
        return "limit"
    return "?"


def explain_aaa_only(
    page: AaaPage, site: AaaSite, seen: SfRow | None = None, sf_keys: set[str] = frozenset(),
) -> str:
    if page.error:
        return "render-hiba"
    status_reason = _status_reason(page.status)
    if status_reason:
        return status_reason
    if page.noindex:
        return "noindex"
    if site.robots and not site.robots.allowed(page.url):
        return "robots"
    if not site.in_sf_scope(page.url):
        return "scope"
    if seen is not None:
        if "robots" in seen.status_text.lower():
            return "robots"
        seen_reason = _status_reason(seen.status)
        if seen_reason:
            return seen_reason
        if "html" not in seen.content_type.lower():
            return f"nem HTML ({seen.content_type or 'ismeretlen típus'})"
    if site.sitemap_rooted(page.url) and not site.link_sources.get(page.url, set()) & sf_keys:
        return "sitemap"
    return "?"


def write_outputs(result: Comparison, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(out / "url_diff.csv", result.url_rows,
               ["side", "url", "sf_address", "sf_status", "aaa_status", "explanation", "note"])
    _write_csv(out / "status_diff.csv", result.status_rows,
               ["url", "sf_status", "aaa_status", "aaa_error", "aaa_final_url", "sf_redirect_url"])
    _write_csv(out / "link_diff.csv", result.link_rows, LINK_FIELDS)
    (out / "summary.md").write_text(result.summary, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--sf-csv", type=Path, required=True, help="SF internal_html.csv")
    parser.add_argument("--sf-response-codes", type=Path, help="SF response_codes_all.csv")
    parser.add_argument("--sf-outlinks", type=Path, help="SF all_outlinks.csv (bulk export)")
    parser.add_argument("--db", type=Path, required=True, help="a site DuckDB-je")
    parser.add_argument("--out", type=Path, required=True, help="kimeneti könyvtár")
    parser.add_argument("--include", help="az aaa crawl include-regexe")
    parser.add_argument("--exclude", help="az aaa crawl exclude-regexe")
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    parser.add_argument("--max-diff", type=float, default=MAX_DIFF)
    parser.add_argument("--quiet", action="store_true", help="az összegzést ne írja ki")
    args = parser.parse_args(argv)
    con = duckdb.connect(str(args.db), read_only=True)
    try:
        site = load_site(con, args.include, args.exclude)
    finally:
        con.close()
    result = compare(
        read_sf_csv(args.sf_csv), site,
        response_codes=read_sf_csv(args.sf_response_codes) if args.sf_response_codes else None,
        outlinks=read_sf_outlinks(args.sf_outlinks) if args.sf_outlinks else None,
        tolerance=args.tolerance, max_diff=args.max_diff, db_bytes=args.db.stat().st_size,
    )
    write_outputs(result, args.out)
    if not args.quiet:
        sys.stdout.write(result.summary)
    return 0 if result.passed else 1


def _count_link_rows(
    ok_pages: list[str], common: set[str], representative: dict[str, SfRow], site: AaaSite,
    tolerance: float,
) -> list[dict[str, object]]:
    """Az SF `Internal:HTML` számai az aaa linkjeivel szemben."""
    inlinks: dict[str, list[str]] = defaultdict(list)
    for source in common:
        for target in site.outlinks.get(source, []):
            inlinks[target].append(source)
    rows = []
    for key in ok_pages:
        out = site.outlinks.get(key, [])
        aaa = {"outlinks": len(out), "unique_outlinks": len(set(out)),
               "inlinks": len(inlinks[key]), "unique_inlinks": len(set(inlinks[key]))}
        for metric, _ in LINK_METRICS:
            sf_value = representative[key].counts.get(metric)
            if sf_value is not None and _outside(sf_value, aaa[metric], tolerance):
                rows.append(_link_row(key, "sf_szám", metric, sf_value, aaa[metric]))
    return rows


def _level_link_rows(
    ok_pages: list[str], common: set[str], outlinks: list[SfLink], site: AaaSite,
    tolerance: float,
) -> tuple[list[dict[str, object]], dict[str, Counter[str]]]:
    """Linkszintű összevetés: az SF renderelt hiperlinkjei az aaa linkjeivel szemben; a csak
    nyers HTML-ben lévő SF-linkek célonként külön."""
    sf_out: dict[str, Counter[str]] = defaultdict(Counter)
    sf_in: dict[str, Counter[str]] = defaultdict(Counter)
    raw_out: dict[str, Counter[str]] = defaultdict(Counter)
    raw_in: dict[str, Counter[str]] = defaultdict(Counter)
    for link in outlinks:
        if link.type != "Hyperlink":
            continue
        source = normalize(link.source, site.policy)
        target = normalize(link.destination, site.policy)
        if source is None or target is None or not site.in_sf_scope(target):
            continue
        out, into = (sf_out, sf_in) if link.rendered else (raw_out, raw_in)
        out[source][target] += 1
        if source in common:
            into[target][source] += 1
    aaa_in: dict[str, Counter[str]] = defaultdict(Counter)
    for source in common:
        for target in site.outlinks.get(source, []):
            aaa_in[target][source] += 1
    rows = []
    for key in ok_pages:
        aaa_out = Counter(site.outlinks.get(key, []))
        pairs = {
            "outlinks": (sf_out[key], aaa_out, raw_out[key], False),
            "unique_outlinks": (sf_out[key], aaa_out, raw_out[key], True),
            "inlinks": (sf_in[key], aaa_in[key], raw_in[key], False),
            "unique_inlinks": (sf_in[key], aaa_in[key], raw_in[key], True),
        }
        for metric, (sf, aaa, raw, unique) in pairs.items():
            sf_value, aaa_value = (len(sf), len(aaa)) if unique else (sf.total(), aaa.total())
            if _outside(sf_value, aaa_value, tolerance):
                rows.append(_link_row(key, "linkszint", metric, sf_value, aaa_value,
                                      _brief_counter(sf - aaa), _brief_counter(aaa - sf),
                                      _brief_counter(raw)))
    return rows, {key: raw_out[key] for key in ok_pages if raw_out[key]}


def _summary(
    site: AaaSite, sf_rows: list[SfRow], groups: dict[str, list[SfRow]], sf_only: list[str],
    aaa_only: list[str], url_rows: list[dict[str, object]], status_rows: list[dict[str, object]],
    link_pages: int, count_rows: list[dict[str, object]],
    level_rows: list[dict[str, object]] | None, raw_only: dict[str, Counter[str]],
    tolerance: float, max_diff: float, db_bytes: int | None, unexplained: int, passed: bool,
) -> str:
    union = len(set(groups) | set(site.pages))
    differing = len(sf_only) + len(aaa_only)
    reasons_sf = Counter(r["explanation"] for r in url_rows if r["side"] == "csak_sf")
    reasons_aaa = Counter(r["explanation"] for r in url_rows if r["side"] == "csak_aaa")
    normalized = sum(1 for r in url_rows if r["side"] == "normalizálás")
    pages = len(site.pages)
    errors = sum(1 for page in site.pages.values() if page.error)
    http_errors = sum(1 for page in site.pages.values() if (page.status or 0) >= 400)
    lines = [
        f"## {site.domain}: Screaming Frog és aaa",
        "",
        (f"- **SF:** {len(sf_rows)} HTML-sor, {len(groups)} különböző normalizált URL; "
         f"{normalized} SF-címet írt át a normalizálás."),
        f"- **aaa:** {pages} sor a `pages` táblában.",
        f"- **Közös:** {len(set(groups) & set(site.pages))}.",
        f"- **Csak SF:** {len(sf_only)}{_reasons(reasons_sf)}.",
        f"- **Csak aaa:** {len(aaa_only)}{_reasons(reasons_aaa)}.",
        (f"- **URL-halmaz eltérés:** {_share(differing, union)} ({differing} / {union}); "
         f"magyarázatlan: {unexplained}."),
        f"- **Státuszkód-eltérés:** {len(status_rows)} közös URL-en.",
        (f"- **Belső linkek, az SF `Internal:HTML` számaiból** ({link_pages} közös, mindkét "
         f"oldalon 2xx oldal, ±{tolerance:.0%}), tűrésen kívüli sor: {_by_metric(count_rows)}."),
    ]
    if level_rows is not None:
        raw_links = sum(counter.total() for counter in raw_only.values())
        lines += [
            (f"- **Belső linkek linkszinten** (az SF renderelt hiperlinkjei, `Links:All "
             f"Outlinks`), tűrésen kívüli sor: {_by_metric(level_rows)}."),
            (f"- **Csak az SF nyers HTML-jében lévő linkek** (`Link Origin: HTML`; az aaa "
             f"renderelt DOM-jában nincsenek): {raw_links} link {len(raw_only)} oldalon"
             + (f"; célok: {_brief_counter(sum(raw_only.values(), Counter()))}"
                if raw_links else "") + "."),
        ]
    if site.run:
        run_id, _, done, failed, skipped, rate, started, finished = site.run
        lines.append(
            f"- **aaa crawl #{run_id}:** {done} kész, {failed} hibás, {skipped} kihagyva; "
            f"{rate or 0:.2f} oldal/mp; indult {started:%Y-%m-%d %H:%M:%S}"
            + (f", kész {finished:%H:%M:%S}" if finished else "") + ".")
    lines.append(
        f"- **aaa hiba%:** render/fetch-hiba {_share(errors, pages)} ({errors}), "
        f"4xx/5xx {_share(http_errors, pages)} ({http_errors}).")
    storage = f"DB-fájl {db_bytes / pages / 1024:.1f} KiB/oldal, " if db_bytes and pages else ""
    lines.append(
        f"- **aaa tárhely:** {storage}renderelt HTML (zstd) "
        f"{(site.rendered_bytes / pages / 1024) if pages else 0:.1f} KiB/oldal.")
    lines += [
        "",
        "### Elfogadás",
        "",
        (f"- **URL-halmaz** (legfeljebb {max_diff:.0%}, minden eltérés magyarázva): "
         + ("teljesül." if passed else "NEM teljesül.")),
        ("- **Státuszkódok egyeznek:** "
         + ("igen." if not status_rows else f"NEM, {len(status_rows)} eltérés.")),
        (f"- **Belső linkek ±{tolerance:.0%}, SF-számokból:** "
         + ("igen." if not count_rows else f"NEM, {len(count_rows)} tűrésen kívüli sor.")),
    ]
    if level_rows is not None:
        lines.append(f"- **Belső linkek ±{tolerance:.0%}, linkszinten (renderelt DOM):** "
                     + ("igen." if not level_rows else f"NEM, {len(level_rows)} tűrésen kívüli sor."))
    return "\n".join(lines) + "\n"


def _group(rows: list[SfRow], policy: UrlPolicy) -> dict[str, list[SfRow]]:
    groups: dict[str, list[SfRow]] = defaultdict(list)
    for row in rows:
        key = normalize(row.url, policy)
        if key is not None:
            groups[key].append(row)
    return groups


def _representative(key: str, rows: list[SfRow]) -> SfRow:
    for row in rows:
        if row.url == key:
            return row
    return next((row for row in rows if _ok(row.status)), rows[0])


def _url_row(side: str, url: str, sf_address: str, sf_status: int | None,
             aaa_status: int | None, explanation: str, note: str = "") -> dict[str, object]:
    return {"side": side, "url": url, "sf_address": sf_address, "sf_status": sf_status,
            "aaa_status": aaa_status, "explanation": explanation, "note": note}


def _sf_discovery(outlinks: list[SfLink], policy: UrlPolicy) -> dict[str, str]:
    """Célonként, milyen linkből ismeri az SF: típus (hiperlinknél: csak nyers HTML-ből, ha
    így) és legfeljebb három forrás."""
    sources: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for link in outlinks:
        target = normalize(link.destination, policy)
        if target is None:
            continue
        kind = link.type
        if kind == "Hyperlink" and not link.rendered:
            kind = "Hyperlink, csak nyers HTML"
        if link.source not in sources[target][kind]:
            sources[target][kind].append(link.source)
    return {
        target: "; ".join(
            f"{kind} ← {', '.join(found[:3])}" + (f" (+{len(found) - 3})" if len(found) > 3 else "")
            for kind, found in sorted(kinds.items()))
        for target, kinds in sources.items()
    }


def _aaa_discovery(url: str, site: AaaSite) -> str:
    priority, parent = site.queue.get(url, (None, None))
    if parent is None:
        return "felfedezve: sitemap" if priority == PRIORITY["sitemap"] else ""
    return f"felfedezve: {parent}"


def _link_row(url: str, basis: str, metric: str, sf: int, aaa: int, only_sf: str = "",
              only_aaa: str = "", raw: str = "") -> dict[str, object]:
    return {"url": url, "basis": basis, "metric": metric, "sf": sf, "aaa": aaa,
            "diff_pct": _pct(sf, aaa), "csak_sf": only_sf, "csak_aaa": only_aaa,
            "sf_csak_nyers_html": raw}


def _status_reason(status: int | None) -> str | None:
    if not status:
        return "nincs válasz"
    if 300 <= status < 400:
        return "redirect"
    if status == 404:
        return "404"
    if status >= 400:
        return f"http {status}"
    return None


def _outside(sf_value: int, aaa_value: int, tolerance: float) -> bool:
    return abs(aaa_value - sf_value) > tolerance * sf_value


def _pct(sf_value: int, aaa_value: int) -> float:
    if sf_value == 0:
        return 0.0 if aaa_value == 0 else math.inf
    return round((aaa_value - sf_value) / sf_value * 100, 1)


def _ok(status: int | None) -> bool:
    return status is not None and 200 <= status < 300


def _int(value: str | None) -> int | None:
    value = (value or "").strip()
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "—"


def _reasons(counter: Counter[str]) -> str:
    if not counter:
        return ""
    return " (" + ", ".join(f"{reason} {n}" for reason, n in counter.most_common()) + ")"


def _by_metric(rows: list[dict[str, object]]) -> str:
    counts = Counter(row["metric"] for row in rows)
    return ", ".join(f"`{metric}` {counts[metric]}" for metric, _ in LINK_METRICS)


def _brief_counter(counter: Counter[str], limit: int = 5) -> str:
    items = counter.most_common()
    text = "; ".join(f"{url}×{n}" for url, n in items[:limit])
    return text + (f"; +{len(items) - limit} további" if len(items) > limit else "")


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
