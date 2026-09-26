"""Site-profil: szintetikus DB-vel és a három rögzített crawl-készleten (kk.coach: Polylang,
HU/EN; materia-tm.com: WPML, EN/HU/IT, budapesti cím; ngx-bootstrap: egynyelvű dokumentáció).
Felvétel: `pytest -m live -k record_site_profile`."""
import json

import pytest
import zstandard
from selectolax.parser import HTMLParser
from typer.testing import CliRunner

from aaa2.cli.main import app
from aaa2.db import connect as connect_module
from aaa2.db.connect import connect
from aaa2.engine.site_profile import (
    build_profile,
    page_tech_signals,
    site_languages,
    update_site_profile,
)
from aaa2.engine.target_country import Candidate
from tests.recorded import REFERENCE_SETS, record_crawl

HU_TEXT = "Ez egy magyar szöveg, amely azt mutatja, hogy a weboldal nem angol. " * 4
EN_TEXT = "This is the page that shows which language the website uses for their content. " * 4


def html(body="<p>x</p>", head="", lang=None):
    attribute = f' lang="{lang}"' if lang else ""
    return f"<html{attribute}><head>{head}</head><body>{body}</body></html>"


def page(url, *, status=200, error=None, page_html=None, main="", lang=None, hreflang=(),
         title=None, description=None, headings=(), schema=(), final_url=None):
    if page_html is None and status // 100 == 2:
        page_html = html(lang=lang)
    return {"url": url, "status": status, "error": error, "html": page_html, "main": main,
            "lang": lang, "hreflang": list(hreflang), "title": title, "description": description,
            "headings": list(headings), "schema": list(schema), "final_url": final_url}


def site_db(domain, seed, pages, path=":memory:"):
    con = connect(path)
    con.execute("INSERT INTO site (domain, seed_url) VALUES (?, ?)", [domain, seed])
    compressor = zstandard.ZstdCompressor()
    for p in pages:
        compressed = compressor.compress(p["html"].encode()) if p["html"] is not None else None
        (page_id,) = con.execute(
            "INSERT INTO pages (url, status, error, rendered_html, main_content, lang, hreflang, "
            "title, meta_description, final_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "RETURNING page_id",
            [p["url"], p["status"], p["error"], compressed, p["main"], p["lang"], p["hreflang"],
             p["title"], p["description"], p["final_url"]],
        ).fetchone()
        for ordinal, (level, text) in enumerate(p["headings"], 1):
            con.execute("INSERT INTO headings VALUES (?, ?, ?, ?)", [page_id, level, text, ordinal])
        for ordinal, item in enumerate(p["schema"], 1):
            con.execute("INSERT INTO schema_blocks VALUES (?, ?, ?, ?)",
                        [page_id, item.get("@type"), json.dumps(item), ordinal])
    return con


# ---------------------------------------------------------------------------
# nyelvek, tech-jelek
# ---------------------------------------------------------------------------


def test_site_languages_by_page_count_then_hreflang_only():
    langs = ["hu-HU", "en-US", "hu", None, "HU", "en_GB", " "]
    hreflang = ["hu", "en", "de-AT", "x-default"]
    assert site_languages(langs, hreflang) == ("hu", "en", "de")


def test_page_tech_signals():
    page_html = html(
        head='<meta name="generator" content=" WordPress 7.1.2 ">'
             '<meta name="Generator" content="WPML ver:4.7.4">'
             '<script src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>'
             '<script src="https://kk.coach/wp-includes/js/jquery.js?ver=3.7"></script>'
             '<script src="/relativ.js"></script>'
             '<link rel="stylesheet" href="/wp-content/themes/generatepress/style.css">'
             '<link rel="stylesheet" href="/wp-content/plugins/polylang/x.css">',
        body='<app-root ng-version="22.0.2"></app-root><script>self.__NEXT_DATA__={}</script>',
    )
    signals = page_tech_signals(HTMLParser(page_html), page_html, "kk.coach")
    assert signals == {
        "generator:WordPress 7.1.2", "generator:WPML ver:4.7.4",
        "script:www.googletagmanager.com",
        "path:/wp-includes/", "path:/wp-content/themes/generatepress/",
        "path:/wp-content/plugins/polylang/",
        "dom:ng-version=22.0.2", "dom:__NEXT_DATA__",
    }


# ---------------------------------------------------------------------------
# a profil egy szintetikus DB-n
# ---------------------------------------------------------------------------


def test_build_profile_counts_successful_pages_and_orders_signals():
    common = '<script src="https://cdn.example.com/a.js"></script>'
    con = site_db("bolt.hu", "https://bolt.hu/", [
        page("https://bolt.hu/", page_html=html(head=common + '<meta name="generator" content="X">'),
             lang="hu", main=HU_TEXT, hreflang=["hu|https://bolt.hu/", "en|https://bolt.hu/en/"]),
        page("https://bolt.hu/a/", page_html=html(head=common), lang="hu", main=HU_TEXT),
        page("https://bolt.hu/en/", lang="en", main=EN_TEXT),
        page("https://bolt.hu/404/", status=404, page_html=html(), lang="hu"),
        page("https://bolt.hu/fal/", error="wall", page_html=html(), lang="hu"),
        page("https://bolt.hu/regi/", status=301, final_url="https://bolt.hu/a/"),
    ])
    profile = build_profile(con)
    assert profile.page_count == 3
    assert profile.languages == ("hu", "en")
    assert (profile.target_country, profile.target_country_confidence) == ("HU", "medium")
    assert profile.target_country_candidates == (Candidate("HU", 1.0, ("tld",)),)
    assert profile.market_scope == "country_specific"
    assert profile.tech_signals == ("script:cdn.example.com", "generator:X")


def test_content_language_is_not_a_country():
    """Magyar tartalom `.com`-on, országjel nélkül: nyelv van, célország nincs."""
    con = site_db("pelda.com", "https://pelda.com/", [
        page("https://pelda.com/", lang="hu", main=HU_TEXT),
        page("https://pelda.com/b/", lang="hu-HU", main=HU_TEXT),
    ])
    profile = build_profile(con)
    assert profile.languages == ("hu",)
    assert (profile.target_country, profile.target_country_candidates) == (None, ())
    assert profile.market_scope == "not_country_specific"


def test_signals_are_collected_from_every_page():
    """A telefon a kontaktoldal meta descriptionjében, a cím schemában, a pénznem egy harmadik
    oldal címében: sitewide együtt adják."""
    con = site_db("pelda.com", "https://pelda.com/", [
        page("https://pelda.com/", lang="en", main=EN_TEXT),
        page("https://pelda.com/contact/", lang="en", description="Call us: +36 1 234 5678"),
        page("https://pelda.com/about/", lang="en", schema=[{
            "@type": "Organization", "address": {"@type": "PostalAddress", "addressCountry": "HU"}}]),
        page("https://pelda.com/prices/", lang="en", title="Prices in HUF"),
    ])
    profile = build_profile(con)
    assert (profile.target_country, profile.target_country_confidence) == ("HU", "medium")
    assert profile.target_country_candidates == (
        Candidate("HU", 1.0, ("phone", "schema", "currency")),)
    assert profile.market_scope == "country_specific"


MIXED_SITE = [
    page("https://plumber.co.uk/", lang="en-GB", title="Plumber",
         headings=[(1, "Emergency plumber in London")],
         main="Spare parts shipped worldwide. " + EN_TEXT),
    page("https://plumber.co.uk/prices/", lang="en-GB", main="Call-out from £49."),
]


def test_synthetic_mixed_site():
    """ccTLD + város a H1-ben + "worldwide" a main contentben: mixed, leírva, nem feloldva."""
    con = site_db("plumber.co.uk", "https://plumber.co.uk/", MIXED_SITE)
    profile = build_profile(con)
    assert (profile.market_scope, profile.market_scope_city) == ("mixed", "London")
    assert (profile.target_country, profile.target_country_confidence) == ("GB", "medium")
    assert profile.target_country_candidates == (Candidate("GB", 1.0, ("tld", "currency")),)


def test_market_scope_reads_home_pages_only():
    """A nemzetközi szó és a város csak a seed oldalon és a hreflang-alternatíváin számít."""
    seed = page("https://pelda.com/", lang="en", title="Trattoria",
                hreflang=["en|https://pelda.com/", "hu|https://pelda.com/hu", "it|https://x.it/"])
    hu_home = page("https://pelda.com/hu/", lang="hu", main="1073 Budapest, Dob u. 56")
    menu = page("https://pelda.com/menu/", lang="en", headings=[(3, "Order Online")],
                main="Order online. Our supplier is based in 20121 Milan.")
    con = site_db("pelda.com", "https://pelda.com/", [seed, hu_home, menu])
    profile = build_profile(con)
    assert (profile.market_scope, profile.market_scope_city) == ("local", "Budapest")


def test_market_scope_follows_seed_redirect():
    """A seed átirányít; a céloldal H3-a számít, a H4 már nem."""
    con = site_db("pelda.com", "https://pelda.com/", [
        page("https://pelda.com/", status=301, final_url="https://pelda.com/en/"),
        page("https://pelda.com/en/", lang="en",
             headings=[(4, "Offices in London"), (3, "Plumber in Dublin")]),
    ])
    assert build_profile(con).market_scope_city == "Dublin"


def test_update_site_profile_writes_site_row():
    con = site_db("x.co.uk", "https://x.co.uk/", [
        page("https://x.co.uk/", lang="en-GB", main="Worldwide delivery. " + EN_TEXT),
    ])
    update_site_profile(con)
    row = con.execute(
        "SELECT target_country, target_country_confidence, target_country_candidates, "
        "market_scope, market_scope_city, languages, page_count, tech_signals FROM site"
    ).fetchone()
    assert row[:2] == ("GB", "medium")
    assert json.loads(row[2]) == [{"country": "GB", "score": 1.0, "signals": ["tld"]}]
    assert row[3:] == ("country_specific", None, ["en"], 1, [])


def test_update_without_country_writes_empty_candidates():
    con = site_db("x.com", "https://x.com/", [page("https://x.com/", lang="en", main=EN_TEXT)])
    update_site_profile(con)
    row = con.execute("SELECT target_country, target_country_confidence, "
                      "target_country_candidates, market_scope FROM site").fetchone()
    assert (row[0], row[1], json.loads(row[2]), row[3]) == (None, None, [], "not_country_specific")


def test_cli_status_prints_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_module, "DATA_DIR", tmp_path)
    con = site_db("plumber.co.uk", "https://plumber.co.uk/", MIXED_SITE,
                  path=connect_module.db_path("plumber.co.uk"))
    update_site_profile(con)
    con.close()
    status = CliRunner().invoke(app, ["status", "plumber.co.uk"])
    assert status.exit_code == 0, status.output
    assert ("profil: célország GB (medium), piaci hatókör mixed (London), nyelvek en, "
            "2 sikeres oldal") in status.output
    assert "jelölt GB 1.00: tld, currency" in status.output


def test_profile_without_site_row_is_empty():
    profile = build_profile(connect(":memory:"))
    assert (profile.target_country, profile.languages, profile.page_count) == (None, (), 0)


# ---------------------------------------------------------------------------
# a három rögzített készlet
# ---------------------------------------------------------------------------

# A készletek a tests/recorded.py REFERENCE_SETS-ében.
# A felvevő route.fetch()-csel követi az átirányítást, így a felvett kk.coach-készletben a 4 régi,
# 301-es magyar URL is tartalmi oldal: 40 sikeres oldal (élesben 36), és a magyar oldalakból
# több van, mint az angolokból.
EXPECTED = {
    # Telefon és cím nincs a main contentben; a /hu/ ág, a forintárak és a hu_HU og:locale
    # szavaz Magyarországra, erős jel nélkül. Az angol szolgáltatásoldalak schemája
    # areaServed: Worldwide.
    "kk-coach-crawl": {
        "profile": ("HU", "low", "international_global", None, ("hu", "en"), 40),
        "candidates": ["HU", "US"],
        "signals": {"path:/wp-content/themes/generatepress/", "path:/wp-includes/",
                    "script:static.cloudflareinsights.com"},
    },
    # A +36-os telefon a kezdőoldalakon; az adatkezelési tájékoztató +1-es és +39-es
    # adatfeldolgozói és az /it/ ág csak jelöltek. A cím a kezdőoldal main contentjében:
    # 1073 Budapest.
    "materia-crawl": {
        "profile": ("HU", "medium", "local", "Budapest", ("en", "hu", "it"), 14),
        "candidates": ["HU", "IT", "US"],
        "signals": {"generator:WordPress 7.1.2",
                    "path:/wp-content/plugins/sitepress-multilingual-cms/",
                    "path:/wp-content/themes/Divi/", "script:cdn-cookieyes.com"},
    },
    # Nincs országjel; a "Global styling" az alert-oldal szakaszcíme, nem piaci állítás.
    "ngx-bootstrap-crawl": {
        "profile": (None, None, "not_country_specific", None, ("en",), 69),
        "candidates": [],
        "signals": {"dom:ng-version=22.0.2"},
    },
}


def site_profile_row(con):
    return con.execute(
        "SELECT target_country, target_country_confidence, market_scope, market_scope_city, "
        "languages, page_count, target_country_candidates, tech_signals FROM site"
    ).fetchone()


@pytest.mark.parametrize("name", list(REFERENCE_SETS))
def test_reference_site_profile(name, reference_crawl):
    con = reference_crawl(name)
    if con is None:
        pytest.skip(f"nincs felvétel: pytest -m live -k record_site_profile ({name})")
    country, confidence, scope, city, languages, page_count, candidates, signals = (
        site_profile_row(con))
    expected = EXPECTED[name]
    assert (country, confidence, scope, city, tuple(languages), page_count) == expected["profile"]
    assert [c["country"] for c in json.loads(candidates)] == expected["candidates"]
    assert expected["signals"] <= set(signals)


@pytest.mark.live
@pytest.mark.parametrize("name", ["kk-coach-crawl", "ngx-bootstrap-crawl"])
async def test_live_record_site_profile_sets(name):
    seed, options = REFERENCE_SETS[name]
    recording, con, summary = await record_crawl(name, seed, options)
    print(f"\n{name}: {summary} válasz={len(recording.responses)} profil={site_profile_row(con)}")
    assert summary.pages_done > 0
