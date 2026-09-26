"""Az elfogadási összevető (tests/acceptance/compare.py) szintetikus DB-vel és SF-CSV-vel."""
import csv

import pytest

from aaa2.db.connect import connect
from tests.acceptance.compare import (
    ACCEPTED,
    SfLink,
    SfRow,
    _outside,
    compare,
    load_site,
    main,
    read_sf_csv,
)
from tests.acceptance.run_acceptance import compare_databases

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
    ("https://pelda.hu/%C3%A1rak/", 200, None, False, None, [], []),
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
    ("https://pelda.hu/%c3%a1rak/", 200, "", 0, 0, 0, 0),
]


def aaa_db(path=":memory:", pages=AAA_PAGES):
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
    for url, status, error, noindex, final_url, hreflang, links in pages:
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


def sf_csv(path, rows=SF_ROWS):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(SF_COLUMNS)
        for address, status, indexability, inl, uinl, outl, uoutl in rows:
            writer.writerow([address, "text/html; charset=UTF-8", status, "", "", indexability,
                             inl, uinl, outl, uoutl, ""])
    return path


@pytest.fixture
def result(tmp_path):
    return compare(read_sf_csv(sf_csv(tmp_path / "internal_html.csv")), load_site(aaa_db()))


def test_read_sf_csv(tmp_path):
    rows = read_sf_csv(sf_csv(tmp_path / "internal_html.csv"))
    assert rows[1] == SfRow("https://pelda.hu/a", 301, "", "text/html; charset=UTF-8",
                            "Redirected", "", {"outlinks": None, "unique_outlinks": None,
                                               "inlinks": 1, "unique_inlinks": 1})
    assert len(rows) == len(SF_ROWS)


def test_url_differences_are_explained(result):
    by_side = {(row["side"], row["url"]): row["explanation"] for row in result.url_rows}
    assert by_side == {
        ("normalizálás", "https://pelda.hu/a/"): "normalizálás",
        ("normalizálás", "https://pelda.hu/%C3%A1rak/"): "normalizálás",
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
            "explanation": "robots", "note": ""} in result.url_rows


def test_cdn_infrastructure_is_scope(tmp_path):
    """A Cloudflare e-mail-védelme (`/cdn-cgi/`) nem a site része: csak SF-ben szerepelhet."""
    rows = [*SF_ROWS, ("https://pelda.hu/cdn-cgi/l/email-protection", 404, "", 3, 3, "", "")]
    result = compare(read_sf_csv(sf_csv(tmp_path / "i.csv", rows)), load_site(aaa_db()))
    row = next(r for r in result.url_rows if "cdn-cgi" in str(r["url"]) and r["side"] == "csak_sf")
    assert (row["url"], row["explanation"]) == ("https://pelda.hu/cdn-cgi/l/email-protection",
                                                "scope")


def test_discovery_notes(tmp_path):
    """Csak SF: melyik linktípusból és honnan ismeri az SF; csak aaa: honnan ismeri az aaa."""
    links = [
        SfLink("https://pelda.hu/", "https://pelda.hu/gone", "HTML & Rendered HTML",
               "HTML Canonical"),
        SfLink("https://pelda.hu/a/", "https://pelda.hu/unknown/", "HTML"),
        SfLink("https://pelda.hu/b/", "https://pelda.hu/unknown/", "HTML"),
    ]
    result = compare(read_sf_csv(sf_csv(tmp_path / "i.csv")), load_site(aaa_db()),
                     outlinks=links)
    notes = {row["url"]: row["note"] for row in result.url_rows}
    assert notes["https://pelda.hu/gone/"] == "HTML Canonical ← https://pelda.hu/"
    assert notes["https://pelda.hu/unknown/"] == (
        "Hyperlink, csak nyers HTML ← https://pelda.hu/a/, https://pelda.hu/b/")
    assert notes["https://pelda.hu/orphan/"] == "felfedezve: sitemap"
    assert notes["https://pelda.hu/mystery/"] == ""


RESPONSE_CODES = [
    ("SF-nél robots-tiltott", "https://pelda.hu/mystery/", 0, "Blocked by robots.txt",
     "", "robots"),
    ("SF-nél átirányítás", "https://pelda.hu/mystery/", 301, "Moved Permanently", "", "redirect"),
    ("SF-nél nem HTML", "https://pelda.hu/mystery/", 200, "OK", "application/pdf",
     "nem HTML (application/pdf)"),
    ("SF-nél normalizálatlan alakban", "https://pelda.hu/mystery", 404, "Not Found", "text/html",
     "404"),
]


@pytest.mark.parametrize(("address", "status", "status_text", "content_type", "expected"),
                         [r[1:] for r in RESPONSE_CODES], ids=[r[0] for r in RESPONSE_CODES])
def test_response_codes_explain_only_aaa_url(tmp_path, address, status, status_text,
                                             content_type, expected):
    seen = [SfRow(address, status, status_text, content_type)]
    result = compare(read_sf_csv(sf_csv(tmp_path / "i.csv")), load_site(aaa_db()),
                     response_codes=seen)
    row = next(r for r in result.url_rows if r["url"] == "https://pelda.hu/mystery/")
    assert (row["sf_address"], row["sf_status"], row["explanation"]) == (address, status, expected)


def test_status_differences(result):
    assert result.status_rows == [{
        "url": "https://pelda.hu/c/", "sf_status": 404, "aaa_status": 200, "aaa_error": "",
        "aaa_final_url": "", "sf_redirect_url": ""}]


def test_link_counts_outside_tolerance(result):
    """A seed: 5 kimenő (4 különböző) a seed hostjára; a sub.pelda.hu-s link nem számít. A /b/-re
    az SF 3 bejövőt lát, az aaa 2-t."""
    assert [(r["url"], r["basis"], r["metric"], r["sf"], r["aaa"], r["diff_pct"])
            for r in result.link_rows] == [
        ("https://pelda.hu/b/", "sf_szám", "inlinks", 3, 2, -33.3),
        ("https://pelda.hu/b/", "sf_szám", "unique_inlinks", 3, 2, -33.3),
    ]


def link(source, destination, origin="HTML & Rendered HTML"):
    return SfLink(f"https://pelda.hu{source}", f"https://pelda.hu{destination}", origin)


SF_LINKS = [
    link("/", "/a/"), link("/", "/a/"), link("/", "/b/"), link("/", "/r/", "Rendered HTML"),
    link("/", "/noidx/"), link("/", "/regi-menu/", "HTML"), link("/", "/regi-menu/", "HTML"),
    link("/", "/cdn-cgi/l/email-protection", "HTML"),
    link("/", "/cdn-cgi/l/email-protection", "HTML & Rendered HTML"),
    link("/a/", "/"), link("/a/", "/b/"), link("/b/", "/"), link("/b/", "/c/"),
]


def test_link_level_comparison(tmp_path):
    """Linkszinten a renderelt SF-linkek számítanak: a /b/-n az SF egy /c/-re mutató linkkel
    többet lát; a seed két csak nyers HTML-ben lévő linkje külön oszlopba kerül; a
    `/cdn-cgi/` linkek egyik oldalon sem számítanak."""
    result = compare(read_sf_csv(sf_csv(tmp_path / "i.csv")), load_site(aaa_db()),
                     outlinks=SF_LINKS)
    level = [r for r in result.link_rows if r["basis"] == "linkszint"]
    assert [(r["url"], r["metric"], r["sf"], r["aaa"], r["csak_sf"], r["csak_aaa"])
            for r in level] == [
        ("https://pelda.hu/b/", "outlinks", 2, 1, "https://pelda.hu/c/×1", ""),
        ("https://pelda.hu/b/", "unique_outlinks", 2, 1, "https://pelda.hu/c/×1", ""),
    ]
    assert ("(`Link Origin: HTML`; az aaa renderelt DOM-jában nincsenek): 2 link 1 oldalon; "
            "célok: https://pelda.hu/regi-menu/×2.") in result.summary
    assert "- **Belső linkek ±5%, linkszinten (renderelt DOM):** NEM, 2 tűrésen kívüli sor." in (
        result.summary)


def test_raw_only_links_shown_on_outside_rows(tmp_path):
    links = [*SF_LINKS, link("/b/", "/regi-menu/", "HTML")]
    result = compare(read_sf_csv(sf_csv(tmp_path / "i.csv")), load_site(aaa_db()),
                     outlinks=links)
    row = next(r for r in result.link_rows
               if r["basis"] == "linkszint" and r["metric"] == "outlinks")
    assert row["sf_csak_nyers_html"] == "https://pelda.hu/regi-menu/×1"


def test_sitemap_chain_is_explained(tmp_path):
    """A csak sitemapből induló láncon (orphan → orphan2) elért oldal "sitemap"; ha egy közös
    oldal linkel rá, már nem."""
    con = aaa_db()
    (orphan_id,) = con.execute("SELECT page_id FROM pages WHERE url = 'https://pelda.hu/orphan/'"
                               ).fetchone()
    con.execute("INSERT INTO pages (url, status) VALUES ('https://pelda.hu/orphan2/', 200)")
    con.execute("INSERT INTO crawl_queue (url, priority, status, discovered_from) VALUES "
                "('https://pelda.hu/orphan2/', 20, 'done', 'https://pelda.hu/orphan/')")
    con.execute("INSERT INTO links (from_page_id, to_url, position, ordinal) "
                "VALUES (?, 'https://pelda.hu/orphan2/', 'body', 1)", [orphan_id])
    rows = read_sf_csv(sf_csv(tmp_path / "i.csv"))
    explained = {r["url"]: r["explanation"] for r in compare(rows, load_site(con)).url_rows}
    assert explained["https://pelda.hu/orphan2/"] == "sitemap"
    con.execute("INSERT INTO links (from_page_id, to_url, position, ordinal) VALUES "
                "((SELECT page_id FROM pages WHERE url = 'https://pelda.hu/b/'), "
                "'https://pelda.hu/orphan2/', 'body', 9)")
    explained = {r["url"]: r["explanation"] for r in compare(rows, load_site(con)).url_rows}
    assert explained["https://pelda.hu/orphan2/"] == "?"


@pytest.mark.parametrize(("sf", "aaa", "expected"), [
    (100, 105, False), (100, 106, True), (100, 94, True), (0, 0, False), (0, 1, True),
    (3, 4, True),
])
def test_tolerance(sf, aaa, expected):
    assert _outside(sf, aaa, 0.05) is expected


def test_summary(result):
    assert ("- **SF:** 12 HTML-sor, 11 különböző normalizált URL; 2 SF-címet írt át a "
            "normalizálás.") in result.summary
    assert "- **Csak SF:** 5 (hreflang 1, 404 1, noindex 1, ? 1, scope 1)." in result.summary
    assert ("- **Csak aaa:** 5 (render-hiba 1, ? 1, noindex 1, sitemap 1, redirect 1)."
            in result.summary)
    assert "- **URL-halmaz eltérés:** 62.5% (10 / 16); magyarázatlan: 5." in result.summary
    assert "- **Státuszkód-eltérés:** 1 közös URL-en." in result.summary
    assert ("(5 közös, mindkét oldalon 2xx oldal, ±5%), tűrésen kívüli sor: `outlinks` 0, "
            "`unique_outlinks` 0, `inlinks` 1, `unique_inlinks` 1.") in result.summary
    assert "- **Belső linkek ±5%, SF-számokból:** NEM, 2 tűrésen kívüli sor." in result.summary
    assert "linkszinten" not in result.summary
    assert "1.25 oldal/mp" in result.summary
    assert "render/fetch-hiba 9.1% (1)" in result.summary
    assert "(legfeljebb 2%, minden eltérés magyarázva): NEM teljesül." in result.summary
    assert "- **Státuszkódok egyeznek:** NEM, 1 eltérés." in result.summary
    assert not result.passed


def test_accepted_explanations_are_the_review_list():
    assert ACCEPTED == {"redirect", "404", "noindex", "robots", "scope", "normalizálás"}


def matching_pages(count):
    pages = [(f"https://pelda.hu/p{i}/", 200, None, False, None, [], []) for i in range(count)]
    return [("https://pelda.hu/", 200, None, False, None, [], [])] + pages


def matching_rows(pages):
    return [(url, 200, "", 0, 0, 0, 0) for url, *_ in pages]


@pytest.mark.parametrize(("extra_sf", "passed"), [
    ([], True),
    ([("https://pelda.hu/gone/", 404, "Client Error", 1, 1, "", "")], True),
    ([("https://pelda.hu/gone/", 404, "", 1, 1, "", ""),
      ("https://pelda.hu/gone2/", 404, "", 1, 1, "", ""),
      ("https://pelda.hu/gone3/", 404, "", 1, 1, "", "")], False),
    ([("https://pelda.hu/unknown/", 200, "", 1, 1, 0, 0)], False),
])
def test_pass_needs_small_and_explained_difference(tmp_path, extra_sf, passed):
    """100 közös oldal: 1 magyarázott eltérés (1/101) belefér; 3 magyarázott (3/103) már nem;
    1 magyarázatlan sosem."""
    pages = matching_pages(99)
    rows = matching_rows(pages) + extra_sf
    result = compare(read_sf_csv(sf_csv(tmp_path / "i.csv", rows)), load_site(aaa_db(pages=pages)))
    assert result.passed is passed


def test_main_writes_outputs_and_exit_code(tmp_path, capsys):
    db = tmp_path / "pelda.hu.duckdb"
    aaa_db(db).close()
    out = tmp_path / "out"
    code = main(["--sf-csv", str(sf_csv(tmp_path / "internal_html.csv")), "--db", str(db),
                 "--out", str(out)])
    assert code == 1
    assert sorted(p.name for p in out.iterdir()) == [
        "link_diff.csv", "status_diff.csv", "summary.md", "url_diff.csv"]
    assert "DB-fájl" in capsys.readouterr().out
    with (out / "url_diff.csv").open(encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 12


def test_main_passes(tmp_path):
    pages = matching_pages(3)
    db = tmp_path / "pelda.hu.duckdb"
    aaa_db(db, pages=pages).close()
    code = main(["--sf-csv", str(sf_csv(tmp_path / "i.csv", matching_rows(pages))),
                 "--db", str(db), "--out", str(tmp_path / "out")])
    assert code == 0


def test_compare_databases(tmp_path):
    reference, resumed = tmp_path / "a.duckdb", tmp_path / "b.duckdb"
    aaa_db(reference).close()
    con = aaa_db(resumed)
    con.execute("UPDATE pages SET status = 500 WHERE url = 'https://pelda.hu/c/'")
    con.execute("DELETE FROM links WHERE ordinal = 1 AND from_page_id = "
                "(SELECT page_id FROM pages WHERE url = 'https://pelda.hu/a/')")
    con.execute("DELETE FROM pages WHERE url = 'https://pelda.hu/mystery/'")
    con.close()
    differences = compare_databases(reference, resumed)
    assert [(d["url"], d["field"]) for d in differences] == [
        ("https://pelda.hu/a/", "links"),
        ("https://pelda.hu/c/", "status"),
        ("https://pelda.hu/mystery/", "oldal"),
    ]
    assert compare_databases(reference, reference) == []
