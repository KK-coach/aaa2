"""Screaming Frog `Internal:HTML` export és egy site DuckDB-jének összevetése.

Az SF-címeket az aaa normalizálja a site szabályaival (`site.seed_url`, `https_redirect`,
`trailing_slash`), a két halmaz ezen a kulcson találkozik. Ha több SF-sor ugyanarra a kulcsra
normalizálódik, a kulccsal azonos alakú sor képviseli (különben a 2xx-es, különben az első); a
többi `sf_variant`, nem eltérés.

Kimenet a `--out` könyvtárba:

- `url_diff.csv`: az egyik oldalon hiányzó URL-ek magyarázattal: redirect, 404, http NNN,
  nincs válasz, render-hiba, noindex, robots, scope (nem a seed hostja, vagy az include/exclude
  kizárja), sitemap (csak a sitemapből ismert, belső link nem mutat rá), hreflang, canonical
  (csak hreflang- vagy canonical-cél), limit (az aaa elérte a `max_pages`-t), `?`; és az
  `sf_variant` sorok;
- `status_diff.csv`: a közös URL-ek eltérő státuszkódjai;
- `link_diff.csv`: a közös, mindkét oldalon 2xx oldalak belső linkszámai, ha valamelyik a
  tűrésen kívül esik;
- `summary.md`: szám-összegzés.

Linkszámok az aaa-oldalon: csak a seed hostjára mutató, az include/exclude által átengedett
célok. Kimenő: a linksorok száma (`Outlinks`) és a különböző célok száma (`Unique Outlinks`).
Bejövő: a sikeres oldalakról érkező linksorok (`Inlinks`) és a különböző forrásoldalak
(`Unique Inlinks`). Tűrésen kívül: |aaa − SF| > tűrés × SF.

    python -m tests.acceptance.compare_sf --sf-csv internal_html.csv --db data/x.duckdb --out r/
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import duckdb

from aaa2.engine.frontier import PRIORITY, Robots
from aaa2.engine.normalize import UrlPolicy, is_internal, normalize

TOLERANCE = 0.05
LINK_METRICS = (
    ("outlinks", "Outlinks"),
    ("unique_outlinks", "Unique Outlinks"),
    ("inlinks", "Inlinks"),
    ("unique_inlinks", "Unique Inlinks"),
)


@dataclass(frozen=True)
class SfRow:
    url: str
    status: int | None
    indexability_status: str
    redirect_url: str
    counts: dict[str, int | None]


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
    sitemap_urls: set[str]
    linked_urls: set[str]
    hreflang_targets: set[str]
    canonical_targets: set[str]
    max_pages: int | None
    outlinks: dict[int, list[str]]
    inlinks: dict[str, list[int]]
    run: tuple | None
    rendered_bytes: int

    def in_sf_scope(self, url: str) -> bool:
        if (urlsplit(url).hostname or "") != self.policy.seed_host:
            return False
        if self.exclude and self.exclude.search(url):
            return False
        return not (self.include and not self.include.search(url))


@dataclass(frozen=True)
class Comparison:
    url_rows: list[dict[str, object]]
    status_rows: list[dict[str, object]]
    link_rows: list[dict[str, object]]
    summary: str


def read_sf_csv(path: Path) -> list[SfRow]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [
            SfRow(
                url=(record.get("Address") or "").strip(),
                status=_int(record.get("Status Code")),
                indexability_status=(record.get("Indexability Status") or "").strip(),
                redirect_url=(record.get("Redirect URL") or "").strip(),
                counts={metric: _int(record.get(column)) for metric, column in LINK_METRICS},
            )
            for record in csv.DictReader(handle)
            if (record.get("Address") or "").strip()
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
    sitemap_urls = {url for (url,) in con.execute(
        "SELECT url FROM crawl_queue WHERE priority = ?", [PRIORITY["sitemap"]]).fetchall()}
    outlinks: dict[int, list[str]] = defaultdict(list)
    inlinks: dict[str, list[int]] = defaultdict(list)
    linked_urls: set[str] = set()
    site = AaaSite(
        domain=domain, policy=policy,
        robots=Robots.parse(robots_txt) if robots_txt else None,
        include=re.compile(include) if include else None,
        exclude=re.compile(exclude) if exclude else None,
        pages=pages, sitemap_urls=sitemap_urls, linked_urls=linked_urls,
        hreflang_targets=hreflang_targets, canonical_targets=canonical_targets,
        max_pages=None, outlinks=outlinks, inlinks=inlinks, run=None, rendered_bytes=0,
    )
    for from_page_id, to_url in con.execute(
        "SELECT l.from_page_id, l.to_url FROM links l JOIN pages p ON p.page_id = l.from_page_id "
        "WHERE p.status BETWEEN 200 AND 299 AND p.error IS NULL ORDER BY l.from_page_id, l.ordinal"
    ).fetchall():
        linked_urls.add(to_url)
        if site.in_sf_scope(to_url):
            outlinks[from_page_id].append(to_url)
            inlinks[to_url].append(from_page_id)
    site.run = con.execute(
        "SELECT run_id, max_pages, pages_done, pages_failed, pages_skipped, pages_per_sec, "
        "started_at, finished_at FROM crawl_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    site.max_pages = site.run[1] if site.run else None
    (site.rendered_bytes,) = con.execute(
        "SELECT coalesce(sum(octet_length(rendered_html)), 0) FROM pages").fetchone()
    return site


def compare(
    sf_rows: list[SfRow], site: AaaSite, *, tolerance: float = TOLERANCE,
    db_bytes: int | None = None,
) -> Comparison:
    groups: dict[str, list[SfRow]] = defaultdict(list)
    for row in sf_rows:
        key = normalize(row.url, site.policy)
        if key is not None:
            groups[key].append(row)
    representative = {key: _representative(key, rows) for key, rows in groups.items()}

    url_rows: list[dict[str, object]] = []
    for key, rows in sorted(groups.items()):
        for row in rows:
            if row is not representative[key]:
                url_rows.append(_url_row("sf_variant", key, row.url, row.status, None,
                                         f"normalizálás → {key}"))
    sf_only = sorted(set(groups) - set(site.pages))
    aaa_only = sorted(set(site.pages) - set(groups))
    for key in sf_only:
        row = representative[key]
        url_rows.append(_url_row("csak_sf", key, row.url, row.status, None,
                                 explain_sf_only(row, key, site)))
    for key in aaa_only:
        page = site.pages[key]
        url_rows.append(_url_row("csak_aaa", key, "", None, page.status,
                                 explain_aaa_only(page, site)))

    status_rows: list[dict[str, object]] = []
    link_rows: list[dict[str, object]] = []
    compared_link_pages = 0
    outside = Counter()
    for key in sorted(set(groups) & set(site.pages)):
        row, page = representative[key], site.pages[key]
        if row.status != page.status:
            status_rows.append({
                "url": key, "sf_status": row.status, "aaa_status": page.status,
                "aaa_error": page.error or "", "aaa_final_url": page.final_url or "",
                "sf_redirect_url": row.redirect_url,
            })
        if not (_ok(row.status) and _ok(page.status) and page.error is None):
            continue
        compared_link_pages += 1
        aaa_counts = {
            "outlinks": len(site.outlinks.get(page.page_id, [])),
            "unique_outlinks": len(set(site.outlinks.get(page.page_id, []))),
            "inlinks": len(site.inlinks.get(key, [])),
            "unique_inlinks": len(set(site.inlinks.get(key, []))),
        }
        for metric, _ in LINK_METRICS:
            sf_value = row.counts.get(metric)
            if sf_value is None:
                continue
            if _outside(sf_value, aaa_counts[metric], tolerance):
                outside[metric] += 1
                link_rows.append({
                    "url": key, "metric": metric, "sf": sf_value, "aaa": aaa_counts[metric],
                    "diff_pct": _pct(sf_value, aaa_counts[metric]),
                })

    summary = _summary(
        site, sf_rows, groups, sf_only, aaa_only, url_rows, status_rows, compared_link_pages,
        outside, tolerance, db_bytes,
    )
    return Comparison(url_rows, status_rows, link_rows, summary)


def explain_sf_only(row: SfRow, key: str, site: AaaSite) -> str:
    status_reason = _status_reason(row.status)
    if status_reason:
        return status_reason
    if "noindex" in row.indexability_status.lower():
        return "noindex"
    if site.robots and not site.robots.allowed(key):
        return "robots"
    if not is_internal(key, site.policy) or not site.in_sf_scope(key):
        return "scope"
    if key in site.hreflang_targets:
        return "hreflang"
    if key in site.canonical_targets:
        return "canonical"
    if site.max_pages and len(site.pages) >= site.max_pages:
        return "limit"
    return "?"


def explain_aaa_only(page: AaaPage, site: AaaSite) -> str:
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
    if page.url in site.sitemap_urls and page.url not in site.linked_urls:
        return "sitemap"
    return "?"


def write_outputs(result: Comparison, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(out / "url_diff.csv", result.url_rows,
               ["side", "url", "sf_address", "sf_status", "aaa_status", "explanation"])
    _write_csv(out / "status_diff.csv", result.status_rows,
               ["url", "sf_status", "aaa_status", "aaa_error", "aaa_final_url", "sf_redirect_url"])
    _write_csv(out / "link_diff.csv", result.link_rows, ["url", "metric", "sf", "aaa", "diff_pct"])
    (out / "summary.md").write_text(result.summary, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--sf-csv", type=Path, required=True, help="SF internal_html.csv")
    parser.add_argument("--db", type=Path, required=True, help="a site DuckDB-je")
    parser.add_argument("--out", type=Path, required=True, help="kimeneti könyvtár")
    parser.add_argument("--include", help="az aaa crawl include-regexe")
    parser.add_argument("--exclude", help="az aaa crawl exclude-regexe")
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    args = parser.parse_args(argv)
    con = duckdb.connect(str(args.db), read_only=True)
    try:
        site = load_site(con, args.include, args.exclude)
    finally:
        con.close()
    result = compare(read_sf_csv(args.sf_csv), site, tolerance=args.tolerance,
                     db_bytes=args.db.stat().st_size)
    write_outputs(result, args.out)
    sys.stdout.write(result.summary)
    return 0


def _summary(
    site: AaaSite, sf_rows: list[SfRow], groups: dict[str, list[SfRow]], sf_only: list[str],
    aaa_only: list[str], url_rows: list[dict[str, object]], status_rows: list[dict[str, object]],
    compared_link_pages: int, outside: Counter[str], tolerance: float, db_bytes: int | None,
) -> str:
    union = len(set(groups) | set(site.pages))
    reasons_sf = Counter(r["explanation"] for r in url_rows if r["side"] == "csak_sf")
    reasons_aaa = Counter(r["explanation"] for r in url_rows if r["side"] == "csak_aaa")
    unexplained = reasons_sf["?"] + reasons_aaa["?"]
    variants = sum(1 for r in url_rows if r["side"] == "sf_variant")
    pages = len(site.pages)
    errors = sum(1 for page in site.pages.values() if page.error)
    http_errors = sum(1 for page in site.pages.values() if (page.status or 0) >= 400)
    lines = [
        f"## {site.domain}: Screaming Frog és aaa",
        "",
        (f"- **SF:** {len(sf_rows)} sor, {len(groups)} különböző normalizált URL "
         f"({variants} normalizálási változat)."),
        f"- **aaa:** {pages} sor a `pages` táblában.",
        f"- **Közös:** {len(set(groups) & set(site.pages))}.",
        f"- **Csak SF:** {len(sf_only)}{_reasons(reasons_sf)}.",
        f"- **Csak aaa:** {len(aaa_only)}{_reasons(reasons_aaa)}.",
        (f"- **URL-halmaz eltérés:** {_share(len(sf_only) + len(aaa_only), union)} "
         f"({len(sf_only) + len(aaa_only)} / {union}); magyarázatlan: "
         f"{_share(unexplained, union)} ({unexplained})."),
        f"- **Státuszkód-eltérés:** {len(status_rows)} közös URL-en.",
        (f"- **Belső linkek** ({compared_link_pages} közös, mindkét oldalon 2xx oldal, "
         f"±{tolerance:.0%} tűrés), tűrésen kívül:"),
    ]
    lines += [f"  - `{metric}`: {outside[metric]}" for metric, _ in LINK_METRICS]
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
    return "\n".join(lines) + "\n"


def _representative(key: str, rows: list[SfRow]) -> SfRow:
    for row in rows:
        if row.url == key:
            return row
    return next((row for row in rows if _ok(row.status)), rows[0])


def _url_row(side: str, url: str, sf_address: str, sf_status: int | None,
             aaa_status: int | None, explanation: str) -> dict[str, object]:
    return {"side": side, "url": url, "sf_address": sf_address, "sf_status": sf_status,
            "aaa_status": aaa_status, "explanation": explanation}


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


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
