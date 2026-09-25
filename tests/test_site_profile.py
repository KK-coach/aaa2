"""Site-profil: táblás esetek szintetikus DB-vel, és a három rögzített crawl-készlet
(kk.coach: Polylang, HU/EN; materia-tm.com: WPML, EN/HU/IT; ngx-bootstrap: egynyelvű).
Felvétel: `pytest -m live -k record_site_profile`."""
import pytest
import zstandard
from selectolax.parser import HTMLParser

from aaa2.db.connect import connect
from aaa2.engine.crawl import CrawlOptions
from aaa2.engine.site_profile import (
    build_profile,
    page_language,
    page_tech_signals,
    site_languages,
    target_country,
    update_site_profile,
)
from tests.recorded import record_crawl, replay_crawl

HU_TEXT = "Ez egy magyar szöveg, amely azt mutatja, hogy a weboldal nem angol. " * 4
EN_TEXT = "This is the page that shows which language the website uses for their content. " * 4


def html(body="<p>x</p>", head="", lang=None):
    attribute = f' lang="{lang}"' if lang else ""
    return f"<html{attribute}><head>{head}</head><body>{body}</body></html>"


def site_db(domain, seed, pages):
    """pages: (url, status, error, html, main_content, hreflang) sorok."""
    con = connect(":memory:")
    con.execute("INSERT INTO site (domain, seed_url) VALUES (?, ?)", [domain, seed])
    compressor = zstandard.ZstdCompressor()
    for url, status, error, page_html, main_content, hreflang in pages:
        compressed = compressor.compress(page_html.encode()) if page_html is not None else None
        con.execute(
            "INSERT INTO pages (url, status, error, rendered_html, main_content, hreflang) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [url, status, error, compressed, main_content, list(hreflang)],
        )
    return con


# ---------------------------------------------------------------------------
# oldalankénti nyelv
# ---------------------------------------------------------------------------

PAGE_LANGUAGES = [
    ("html lang", html(lang="hu-HU"), "", "hu-HU"),
    ("html lang előbb, mint meta",
     html(lang="en", head='<meta http-equiv="content-language" content="hu">'), "", "en"),
    ("content-language http-equiv", html(head='<meta http-equiv="Content-Language" content="de-AT">'),
     "", "de-AT"),
    ("content-language name", html(head='<meta name="content-language" content="cs">'), "", "cs"),
    ("og:locale aláhúzással", html(head='<meta property="og:locale" content="hu_HU">'), "", "hu-HU"),
    ("html lang előbb, mint og:locale",
     html(lang="hu", head='<meta property="og:locale" content="en_US">'), "", "hu"),
    ("detekció a main contentből", html(), HU_TEXT, "hu"),
    ("semmi", html(), "12345", None),
    ("üres lang attribútum után detekció", html(lang=" "), EN_TEXT, "en"),
]


@pytest.mark.parametrize(("page_html", "main", "expected"), [p[1:] for p in PAGE_LANGUAGES],
                         ids=[p[0] for p in PAGE_LANGUAGES])
def test_page_language(page_html, main, expected):
    assert page_language(HTMLParser(page_html), main) == expected


def test_site_languages_by_page_count_then_hreflang_only():
    tags = ["hu-HU", "en-US", "hu", None, "HU"]
    hreflang = ["hu", "en", "de-AT", "x-default"]
    assert site_languages(tags, hreflang) == ("hu", "en", "de")


# ---------------------------------------------------------------------------
# célország
# ---------------------------------------------------------------------------

COUNTRIES = [
    ("országkódos TLD", "bolt.hu", [], ("en",), ["en-US"], "HU"),
    ("co.uk", "shop.co.uk", [], ("en",), [], "GB"),
    ("generikus ccTLD nem dönt", "app.io", [], ("en",), ["en"], None),
    ("hreflang régió többségben", "x.com", ["en-GB", "en-GB", "de-AT", "x-default"], ("en", "de"),
     [], "GB"),
    ("hreflang régió döntetlen, nemzeti nyelv dönt", "x.com", ["en-GB", "hu-HU"], ("en", "hu"),
     [], "HU"),
    ("egy nemzeti nyelv", "kk.coach", ["en", "hu"], ("en", "hu"), ["en-US", "hu-HU"], "HU"),
    ("az olasz nem egyországos", "materia-tm.com", ["en", "hu", "it", "x-default"],
     ("en", "hu", "it"), ["en-US", "hu-HU", "it-IT"], "HU"),
    ("két nemzeti nyelv, vegyes régió", "x.com", [], ("hu", "cs"), ["hu-HU", "cs-CZ"], None),
    ("egységes oldalrégió", "x.com", [], ("en",), ["en-GB", "en-GB"], "GB"),
    ("vegyes oldalrégió", "x.com", [], ("en",), ["en-US", "en-GB"], None),
    ("régió nélküli oldal", "valor-software.com", [], ("en",), ["en", "en"], None),
    ("régiós és régió nélküli oldal vegyesen", "x.com", [], ("en",), ["en-GB", "en"], None),
]


@pytest.mark.parametrize(("domain", "hreflang", "languages", "tags", "expected"),
                         [c[1:] for c in COUNTRIES], ids=[c[0] for c in COUNTRIES])
def test_target_country(domain, hreflang, languages, tags, expected):
    assert target_country(domain, hreflang, languages, tags) == expected


# ---------------------------------------------------------------------------
# tech-jelek, oldalszám, írás
# ---------------------------------------------------------------------------


def test_page_tech_signals():
    page = html(
        head='<meta name="generator" content=" WordPress 7.1.2 ">'
             '<meta name="Generator" content="WPML ver:4.7.4">'
             '<script src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>'
             '<script src="https://kk.coach/wp-includes/js/jquery.js?ver=3.7"></script>'
             '<script src="/relativ.js"></script>'
             '<link rel="stylesheet" href="/wp-content/themes/generatepress/style.css">'
             '<link rel="stylesheet" href="/wp-content/plugins/polylang/x.css">',
        body='<app-root ng-version="22.0.2"></app-root><script>self.__NEXT_DATA__={}</script>',
    )
    signals = page_tech_signals(HTMLParser(page), page, "kk.coach")
    assert signals == {
        "generator:WordPress 7.1.2", "generator:WPML ver:4.7.4",
        "script:www.googletagmanager.com",
        "path:/wp-includes/", "path:/wp-content/themes/generatepress/",
        "path:/wp-content/plugins/polylang/",
        "dom:ng-version=22.0.2", "dom:__NEXT_DATA__",
    }


def test_build_profile_counts_successful_pages_and_orders_signals():
    common = '<script src="https://cdn.example.com/a.js"></script>'
    con = site_db("bolt.hu", "https://bolt.hu/", [
        ("https://bolt.hu/", 200, None, html(head=common + '<meta name="generator" content="X">',
                                             lang="hu"), HU_TEXT, ["hu", "en"]),
        ("https://bolt.hu/a/", 200, None, html(head=common, lang="hu"), HU_TEXT, []),
        ("https://bolt.hu/en/", 200, None, html(lang="en"), EN_TEXT, []),
        ("https://bolt.hu/404/", 404, None, html(lang="hu"), "", []),
        ("https://bolt.hu/fal/", 200, "wall", html(lang="hu"), "", []),
        ("https://bolt.hu/regi/", 301, None, None, None, []),
    ])
    profile = build_profile(con)
    assert profile.page_count == 3
    assert profile.languages == ("hu", "en")
    assert profile.target_country == "HU"
    assert profile.tech_signals == ("script:cdn.example.com", "generator:X")


def test_update_site_profile_writes_site_row():
    con = site_db("x.com", "https://x.com/", [
        ("https://x.com/", 200, None, html(lang="en-GB"), EN_TEXT, []),
    ])
    update_site_profile(con)
    row = con.execute(
        "SELECT target_country, languages, page_count, tech_signals FROM site").fetchone()
    assert row == ("GB", ["en"], 1, [])


def test_profile_without_site_row_is_empty():
    profile = build_profile(connect(":memory:"))
    assert (profile.target_country, profile.languages, profile.page_count) == (None, (), 0)


# ---------------------------------------------------------------------------
# a három rögzített készlet
# ---------------------------------------------------------------------------

REFERENCE_SETS = {
    "kk-coach-crawl": ("https://kk.coach/", CrawlOptions(concurrency=4)),
    "materia-crawl": ("https://materia-tm.com/", CrawlOptions(concurrency=3)),
    "ngx-bootstrap-crawl": ("https://valor-software.com/ngx-bootstrap/components",
                            CrawlOptions(concurrency=4, include="/ngx-bootstrap/")),
}
# A felvevő route.fetch()-csel követi az átirányítást, így a felvett kk.coach-készletben a 4 régi,
# 301-es magyar URL is tartalmi oldal: 40 sikeres oldal (élesben 36), és a magyar oldalakból
# több van, mint az angolokból.
EXPECTED = {
    "kk-coach-crawl": ("HU", ("hu", "en"), 40, {
        "path:/wp-content/themes/generatepress/", "path:/wp-includes/",
        "script:static.cloudflareinsights.com"}),
    "materia-crawl": ("HU", ("en", "hu", "it"), 14, {
        "generator:WordPress 7.1.2", "path:/wp-content/plugins/sitepress-multilingual-cms/",
        "path:/wp-content/themes/Divi/", "script:cdn-cookieyes.com"}),
    "ngx-bootstrap-crawl": (None, ("en",), 69, {"dom:ng-version=22.0.2"}),
}


def site_profile_row(con):
    return con.execute(
        "SELECT target_country, languages, page_count, tech_signals FROM site").fetchone()


@pytest.mark.parametrize("name", list(REFERENCE_SETS))
async def test_reference_site_profile(name):
    seed, options = REFERENCE_SETS[name]
    replayed = await replay_crawl(name, seed, options)
    if replayed is None:
        pytest.skip(f"nincs felvétel: pytest -m live -k record_site_profile ({name})")
    _, con = replayed
    country, languages, page_count, signals = site_profile_row(con)
    expected_country, expected_languages, expected_count, expected_signals = EXPECTED[name]
    assert (country, tuple(languages), page_count) == (
        expected_country, expected_languages, expected_count)
    assert expected_signals <= set(signals)


@pytest.mark.live
@pytest.mark.parametrize("name", ["kk-coach-crawl", "ngx-bootstrap-crawl"])
async def test_live_record_site_profile_sets(name):
    seed, options = REFERENCE_SETS[name]
    recording, con, summary = await record_crawl(name, seed, options)
    print(f"\n{name}: {summary} válasz={len(recording.responses)} profil={site_profile_row(con)}")
    assert summary.pages_done > 0
