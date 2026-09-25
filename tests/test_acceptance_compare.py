"""Az elfogadási összehasonlító (tests/acceptance/compare_sf.py) szintetikus DB-vel és SF-CSV-vel."""
import csv

import pytest

from aaa2.db.connect import connect
from tests.acceptance.compare_sf import (
    SfRow,
    _outside,
    compare,
    load_site,
    main,
    read_sf_csv,
)

SF_COLUMNS = ["Address", "Content Type", "Status Code", "Status", "Indexability",
              "Indexability Status", "Inlinks", "Unique Inlinks", "Outlinks", "Unique Outlinks",
              "Redirect URL"]

# (url, status, error, noindex, final_url, hreflang, belső linkek)
AAA_PAGES = [
    ("https://pelda.hu/", 200, None, False, None, ["de|https://pelda.hu/de/"],
     ["/a/", "/a/", "/b/", "/r/", "/noidx/", "https://sub.pelda.hu/x/"]),
    ("https://pelda.hu/a/", 200, None, False, None, [], ["/", "/b/"]),
    ("https://pelda.hu/b/", 200, None, False, None, [], ["/"]),
    ("https://pelda.hu/r/", 301, None, False, "https://pelda.hu/b/", [], []),
    ("https://pelda.hu/noidx/", 200, None, True, None, [], []),
    ("https://pelda.hu/orphan/", 200, None, False, None, [], []),
    ("https://pelda.hu/private/", 200, None, False, None, [], []),
    ("https://pelda.hu/mystery/", 200, None, False, None, [], []),
    ("https://pelda.hu/hibas/", None, "timeout", False, None, [], []),
    ("https://pelda.hu/c/", 200, None, False, None, [], []),
]

# (Address, Status Code, Indexability Status, Inlinks, Unique Inlinks, Outlinks, Unique Outlinks)
SF_ROWS = [
    ("https://pelda.hu/", 200, "", 2, 2, 5, 4),
    ("https://pelda.hu/a", 301, "Redirected", 1, 1, "", ""),
    ("https://pelda.hu/a/", 200, "", 2, 1, 2, 2),
    ("https://pelda.hu/b/", 200, "", 3, 3, 1, 1),
    ("https://pelda.hu/c/", 404, "Client Error", 1, 1, "", ""),
    ("https://pelda.hu/gone/", 404, "Client Error", 1, 1, "", ""),
    ("https://sub.pelda.hu/x/", 200, "", 1, 1, 0, 0),
    ("https://pelda.hu/de/", 200, "", 0, 0, 0, 0),
    ("https://pelda.hu/unknown/", 200, "", 1, 1, 0, 0),
    ("https://pelda.hu/sf-noindex/", 200, "noindex", 1, 1, 0, 0),
    ("https://pelda.hu/private/", 200, "", 0, 0, 0, 0),
]


def aaa_db(path=":memory:"):
    con = connect(path)
    con.execute(
        "INSERT INTO site (domain, seed_url, trailing_slash, https_redirect, robots_txt) "
        "VALUES ('pelda.hu', 'https://pelda.hu/', TRUE, FALSE, "
        "'User-agent: *\nDisallow: /private/\n')")
    con.execute(
        "INSERT INTO crawl_runs (run_id, started_at, finished_at, max_pages, concurrency, "
        "pages_done, pages_failed, pages_skipped, pages_per_sec) "
        "VALUES (1, TIMESTAMP '2026-09-26 10:00:00', TIMESTAMP '2026-09-26 10:00:08', "
        "5000, 6, 9, 1, 0, 1.25)")
    for url, status, error, noindex, final_url, hreflang, links in AAA_PAGES:
        (page_id,) = con.execute(
            "INSERT INTO pages (url, status, error, noindex, final_url, hreflang, rendered_html) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING page_id",
            [url, status, error, noindex, final_url, hreflang,
             b"x" * 1024 if status == 200 else None]).fetchone()
        for ordinal, target in enumerate(links, 1):
            to_url = target if target.startswith("http") else f"https://pelda.hu{target}"
            con.execute("INSERT INTO links (from_page_id, to_url, position, ordinal) "
                        "VALUES (?, ?, 'body', ?)", [page_id, to_url, ordinal])
    con.execute("INSERT INTO crawl_queue (url, priority, status) VALUES "
                "('https://pelda.hu/orphan/', 10, 'done'), ('https://pelda.hu/b/', 10, 'done')")
    return con


def sf_csv(path):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(SF_COLUMNS)
        for address, status, indexability, inl, uinl, outl, uoutl in SF_ROWS:
            writer.writerow([address, "text/html; charset=UTF-8", status, "", "", indexability,
                             inl, uinl, outl, uoutl, ""])
    return path


@pytest.fixture
def result(tmp_path):
    return compare(read_sf_csv(sf_csv(tmp_path / "internal_html.csv")), load_site(aaa_db()))


def test_read_sf_csv(tmp_path):
    rows = read_sf_csv(sf_csv(tmp_path / "internal_html.csv"))
    assert rows[1] == SfRow("https://pelda.hu/a", 301, "Redirected", "", {
        "outlinks": None, "unique_outlinks": None, "inlinks": 1, "unique_inlinks": 1})
    assert len(rows) == len(SF_ROWS)


def test_url_differences_are_explained(result):
    by_side = {(row["side"], row["url"]): row["explanation"] for row in result.url_rows}
    assert by_side == {
        ("sf_variant", "https://pelda.hu/a/"): "normalizálás → https://pelda.hu/a/",
        ("csak_sf", "https://pelda.hu/gone/"): "404",
        ("csak_sf", "https://sub.pelda.hu/x/"): "scope",
        ("csak_sf", "https://pelda.hu/de/"): "hreflang",
        ("csak_sf", "https://pelda.hu/unknown/"): "?",
        ("csak_sf", "https://pelda.hu/sf-noindex/"): "noindex",
        ("csak_aaa", "https://pelda.hu/r/"): "redirect",
        ("csak_aaa", "https://pelda.hu/noidx/"): "noindex",
        ("csak_aaa", "https://pelda.hu/orphan/"): "sitemap",
        ("csak_aaa", "https://pelda.hu/mystery/"): "?",
        ("csak_aaa", "https://pelda.hu/hibas/"): "render-hiba",
    }


def test_robots_explains_only_sf_url(tmp_path):
    con = aaa_db()
    con.execute("DELETE FROM pages WHERE url = 'https://pelda.hu/private/'")
    result = compare(read_sf_csv(sf_csv(tmp_path / "i.csv")), load_site(con))
    assert {"side": "csak_sf", "url": "https://pelda.hu/private/",
            "sf_address": "https://pelda.hu/private/", "sf_status": 200, "aaa_status": None,
            "explanation": "robots"} in result.url_rows


def test_status_differences(result):
    assert result.status_rows == [{
        "url": "https://pelda.hu/c/", "sf_status": 404, "aaa_status": 200, "aaa_error": "",
        "aaa_final_url": "", "sf_redirect_url": ""}]


def test_link_counts_outside_tolerance(result):
    """A seed: 5 kimenő (4 különböző) a seed hostjára; a sub.pelda.hu-s link nem számít. A /b/-re
    az SF 3 bejövőt lát, az aaa 2-t."""
    assert result.link_rows == [
        {"url": "https://pelda.hu/b/", "metric": "inlinks", "sf": 3, "aaa": 2, "diff_pct": -33.3},
        {"url": "https://pelda.hu/b/", "metric": "unique_inlinks", "sf": 3, "aaa": 2,
         "diff_pct": -33.3},
    ]


@pytest.mark.parametrize(("sf", "aaa", "expected"), [
    (100, 105, False), (100, 106, True), (100, 94, True), (0, 0, False), (0, 1, True),
    (3, 4, True),
])
def test_tolerance(sf, aaa, expected):
    assert _outside(sf, aaa, 0.05) is expected


def test_summary(result):
    assert "- **SF:** 11 sor, 10 különböző normalizált URL (1 normalizálási változat)." in (
        result.summary)
    assert "- **Csak SF:** 5 (hreflang 1, 404 1, noindex 1, ? 1, scope 1)." in result.summary
    assert ("- **Csak aaa:** 5 (render-hiba 1, ? 1, noindex 1, sitemap 1, redirect 1)."
            in result.summary)
    assert ("- **URL-halmaz eltérés:** 66.7% (10 / 15); magyarázatlan: 13.3% (2)."
            in result.summary)
    assert "- **Státuszkód-eltérés:** 1 közös URL-en." in result.summary
    assert "  - `inlinks`: 1" in result.summary and "  - `outlinks`: 0" in result.summary
    assert "1.25 oldal/mp" in result.summary
    assert "render/fetch-hiba 10.0% (1)" in result.summary


def test_main_writes_outputs(tmp_path, capsys):
    db = tmp_path / "pelda.hu.duckdb"
    aaa_db(db).close()
    out = tmp_path / "out"
    code = main(["--sf-csv", str(sf_csv(tmp_path / "internal_html.csv")), "--db", str(db),
                 "--out", str(out)])
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "link_diff.csv", "status_diff.csv", "summary.md", "url_diff.csv"]
    assert "DB-fájl" in capsys.readouterr().out
    with (out / "url_diff.csv").open(encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 11
