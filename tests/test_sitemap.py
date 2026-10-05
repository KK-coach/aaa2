"""A sitemap tárolása és ténykimenete (aaa2/engine/frontier.py, aaa2/functions/facts.py)
szintetikus site-on, hálózat nélkül."""
import httpx

from aaa2.db.connect import connect
from aaa2.engine import queries as crawl
from aaa2.engine.frontier import (
    Frontier,
    SitemapFile,
    discover,
    find_sitemap,
    record_missing_mode,
    refetch_sitemap,
    restore_sitemap_from_queue,
)
from aaa2.functions.facts import (
    CRAWLED_NOT_IN,
    IN_AND_CRAWLED,
    IN_NOT_CRAWLED,
    LINKED_ONLY,
    SEED_UNKNOWN,
    SITEMAP_COLUMNS,
    site_fact_rows,
    sitemap_rows,
)
from tests.test_entities_rules import html, site
from tests.test_entities_site import NOON

BASE = "https://pelda.hu"
SITEMAP = f"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{BASE}/</loc><lastmod>2026-09-01</lastmod></url>
  <url><loc>{BASE}/#top</loc></url>
  <url><loc>{BASE}/meres/</loc><lastmod>2026-09-15T10:00:00+02:00</lastmod></url>
  <url><loc>{BASE}/lista/?b=2&amp;a=1</loc></url>
  <url><loc>{BASE}/arva/</loc></url>
  <url><loc>https://masik.hu/kulso/</loc></url>
</urlset>"""


def client(routes):
    def handler(request):
        body = routes.get(str(request.url))
        return httpx.Response(200, content=body.encode()) if body else httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def crawled():
    """Tárolt crawl: kezdőoldal, mérés, lista és egy nem a sitemapben álló oldal; a mérés egy be
    nem járt belső címre is linkel."""
    con = site({
        "/": html("Pelda", "<main><h1>Pelda</h1><p><a href='/meres/'>Mérés</a> "
                           "<a href='/kimaradt/'>Kimaradt</a></p></main>"),
        "/meres/": html("Mérés", "<main><h1>Mérés</h1><p><a href='/nincs-bejarva/'>x</a></p></main>"),
        "/lista/?a=1&b=2": html("Lista", "<main><h1>Lista</h1></main>"),
        "/kimaradt/": html("Kimaradt", "<main><h1>Kimaradt</h1></main>")})
    con.execute("UPDATE site SET robots_txt = ?, https_redirect = true, trailing_slash = true",
                [f"User-agent: *\nSitemap: {BASE}/sitemap.xml\n"])
    con.execute("INSERT INTO crawl_runs (started_at, max_pages, notes) VALUES (?, 5000, 'új')",
                [NOON])
    for url, priority in ((f"{BASE}/", 0), (f"{BASE}/meres/", 10), (f"{BASE}/lista/?a=1&b=2", 10),
                          (f"{BASE}/kimaradt/", 30)):
        con.execute("INSERT INTO crawl_queue (url, priority, status) VALUES (?, ?, 'done')",
                    [url, priority])
    return con


async def test_default_paths_are_tried_in_order_and_the_first_sitemap_wins():
    routes = {f"{BASE}/sitemap_index.xml": SITEMAP, f"{BASE}/wp-sitemap.xml": SITEMAP}
    async with client(routes) as http:
        source, entries, files = await find_sitemap(http, f"{BASE}/")
    assert source == "default"
    assert files == [SitemapFile(f"{BASE}/sitemap.xml", False),
                     SitemapFile(f"{BASE}/sitemap_index.xml", True, False, 6)]
    assert [(e.raw_url, e.lastmod) for e in entries][:3] == [
        (f"{BASE}/", "2026-09-01"), (f"{BASE}/#top", None),
        (f"{BASE}/meres/", "2026-09-15T10:00:00+02:00")]
    # a robots.txt Sitemap-sora megelőzi az alapútvonalakat, a megadott cím mindkettőt
    async with client({f"{BASE}/egyedi.xml": SITEMAP}) as http:
        assert (await find_sitemap(http, f"{BASE}/", robots_sitemaps=[f"{BASE}/egyedi.xml"]))[0] \
            == "robots"
        assert (await find_sitemap(http, f"{BASE}/", sitemap=f"{BASE}/egyedi.xml"))[0] == "given"


async def test_a_new_crawl_stores_the_sitemap_with_its_source():
    routes = {f"{BASE}/robots.txt": f"Sitemap: {BASE}/sitemap.xml\n", f"{BASE}/sitemap.xml": SITEMAP}
    async with client(routes) as http:
        discovery = await discover(http, f"{BASE}/")
    con = connect(":memory:")
    Frontier.start(con, f"{BASE}/", discovery)
    files, urls = crawl.sitemap_files(con), crawl.sitemap_urls(con)
    assert [(f.snapshot, f.url, f.source, f.found, f.urls) for f in files] == [
        ("crawl", f"{BASE}/sitemap.xml", "robots", True, 6)]
    assert len(urls) == 6 and {u.source for u in urls} == {"robots"}
    by_raw = {u.raw_url: u for u in urls}
    # ugyanaz a normalizálás, mint a crawl címeinél: a töredék lehull, a paraméterek rendeződnek
    assert by_raw[f"{BASE}/#top"].url == by_raw[f"{BASE}/"].url
    assert by_raw[f"{BASE}/lista/?b=2&a=1"].url == f"{BASE}/lista/?a=1&b=2"
    assert by_raw["https://masik.hu/kulso/"].internal is False
    assert by_raw[f"{BASE}/meres/"].lastmod == "2026-09-15T10:00:00+02:00"
    assert by_raw[f"{BASE}/"].fetched_at == discovery.sitemap_fetched_at


def test_the_stored_queue_gives_back_the_sitemap_urls_of_an_earlier_crawl():
    con = crawled()
    assert restore_sitemap_from_queue(con) == 2
    assert [(u.snapshot, u.source, u.url, u.raw_url, u.lastmod) for u in crawl.sitemap_urls(con)] == [
        ("crawl", "queue", f"{BASE}/lista/?a=1&b=2", None, None),
        ("crawl", "queue", f"{BASE}/meres/", None, None)]
    assert restore_sitemap_from_queue(con) == 0            # egyszer: a tárolt sorok maradnak
    assert record_missing_mode(con, sitemap_only=True) == 1
    assert crawl.crawl_runs(con)[0].mode == "sitemap"
    assert record_missing_mode(con, sitemap_only=False) == 0     # a rögzített mód marad


async def test_site_facts_and_the_sitemap_view_after_a_later_fetch():
    con = crawled()
    restore_sitemap_from_queue(con)
    record_missing_mode(con, sitemap_only=True)
    async with client({f"{BASE}/sitemap.xml": SITEMAP}) as http:
        assert await refetch_sitemap(con, http) == ("robots", 1, 6)
    facts = {row["tény"]: row["érték"] for row in site_fact_rows(con)}
    assert facts["crawl módja"] == "sitemap-mód"
    assert facts["a crawl az oldalkorláton állt meg"] == "nem"
    assert (facts["sitemap"], facts["sitemap: honnan került elő"]) == ("igen", "robots.txt")
    assert facts["sitemap: a robots.txt hivatkozza"] == "igen"
    assert facts["sitemap: fájlok"] == f"{BASE}/sitemap.xml"
    # hat cím; a site-on öt, ebből a kezdőoldal két alakban: négy különböző oldal
    assert (facts["sitemap: címek száma"], facts["sitemap: különböző oldalak"],
            facts["sitemap: a site-on kívüli címek"]) == (6, 4, 1)
    assert facts["sitemap: az adat forrása"] == "utólagos lekérés"
    assert facts["sitemap: címek a crawl idején (különböző oldal)"] == 2
    rows = {row["cím"]: row for row in sitemap_rows(con)}
    assert list(next(iter(rows.values()))) == list(SITEMAP_COLUMNS)
    assert {url.replace(BASE, ""): row["helyzet"] for url, row in rows.items()} == {
        "/": IN_AND_CRAWLED, "/meres/": IN_AND_CRAWLED, "/lista/?a=1&b=2": IN_AND_CRAWLED,
        "/arva/": IN_NOT_CRAWLED, "/kimaradt/": CRAWLED_NOT_IN, "/nincs-bejarva/": LINKED_ONLY}
    home = rows[f"{BASE}/"]
    assert (home["sitemap-címek száma"], home["eltérő nyers alakok"]) == (
        2, f"{BASE}/ | {BASE}/#top")
    # a sorból visszaállított pillanatképben a kezdő URL helye nem ismert
    assert (home["a sitemapben a crawl idején"], home["a sitemapben ma"]) == ("", "igen")
    assert rows[f"{BASE}/lista/?a=1&b=2"]["eltérő nyers alakok"] == f"{BASE}/lista/?b=2&a=1"
    meres = rows[f"{BASE}/meres/"]
    assert (meres["a sitemapben a crawl idején"], meres["bejártuk"], meres["státusz"],
            meres["noindex"], meres["hivatkozó oldalak"], meres["lastmod"], meres["bejárás"]) == (
        "igen", "igen", 200, "nem", 1, "2026-09-15T10:00:00+02:00", "teljes")
    # a hivatkozott, be nem járt cím: státusz és noindex nélkül, a hivatkozó oldalak számával
    linked = rows[f"{BASE}/nincs-bejarva/"]
    assert (linked["bejártuk"], linked["státusz"], linked["noindex"],
            linked["hivatkozó oldalak"]) == ("nem", "", "", 1)
    assert rows[f"{BASE}/arva/"]["hivatkozó oldalak"] == 0


def test_link_mode_lists_no_uncrawled_link_targets_and_a_capped_crawl_is_partial():
    con = crawled()
    restore_sitemap_from_queue(con)
    record_missing_mode(con, sitemap_only=False)
    rows = {row["cím"]: row for row in sitemap_rows(con)}
    assert f"{BASE}/nincs-bejarva/" not in rows                 # link alapú crawl: nincs ilyen sor
    assert rows[f"{BASE}/"]["a sitemapben ma"] == ""            # nincs utólagos lekérés
    assert rows[f"{BASE}/"]["helyzet"] == SEED_UNKNOWN
    assert {row["bejárás"] for row in rows.values()} == {"teljes"}
    con.execute("UPDATE crawl_runs SET max_pages = 4")          # a sor elérte a korlátot
    assert {row["bejárás"] for row in sitemap_rows(con)} == {"részleges"}
    facts = {row["tény"]: row["érték"] for row in site_fact_rows(con)}
    assert facts["a crawl az oldalkorláton állt meg"] == "igen"
    assert facts["sitemap: az adat forrása"] == "a crawl-sorból visszaállítva"


def test_a_site_without_stored_sitemap_data_has_empty_sitemap_facts():
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1></main>")})
    facts = {row["tény"]: row["érték"] for row in site_fact_rows(con)}
    assert (facts["sitemap"], facts["sitemap: címek száma"], facts["crawl módja"]) == ("", "", "")
    assert [row["helyzet"] for row in sitemap_rows(con)] == [CRAWLED_NOT_IN]


async def test_a_site_without_any_sitemap_says_so():
    async with client({}) as http:
        discovery = await discover(http, f"{BASE}/")
    con = connect(":memory:")
    Frontier.start(con, f"{BASE}/", discovery)
    assert [(f.url.replace(BASE, ""), f.found) for f in crawl.sitemap_files(con)] == [
        ("/sitemap.xml", False), ("/sitemap_index.xml", False), ("/wp-sitemap.xml", False)]
    facts = {row["tény"]: row["érték"] for row in site_fact_rows(con)}
    assert (facts["sitemap"], facts["sitemap: honnan került elő"], facts["sitemap: címek száma"]) \
        == ("nem", "alapútvonal", 0)
