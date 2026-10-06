"""Az aktuális audit oldalkészlete és a kihagyás feltételei két egymást követő crawlon: az
eltűnő oldal kikerül az elemzésből, a fejlécváltozás átmegy, a korábban sikertelen render újra
próbálkozik; a folytatás ugyanaz a crawl; a migráció a meglévő adatbázist a sorból tölti ki."""
from dataclasses import replace

import aaa2.db.connect as connect_module
from aaa2 import api
from aaa2.db.connect import connect
from aaa2.engine import queries
from aaa2.functions.findings import build_findings
from aaa2.functions.graph import build_graph
from tests.test_crawl import html, run, site, tools  # noqa: F401  (fixture-ök)


def small(mini, old=True):
    """Kis site: kezdőoldal, /b/ és (ha `old`) /old/, linkkel és sitemappel."""
    links = "<a href='/b/'>B</a>" + ("<a href='/old/'>Régi</a>" if old else "")
    urls = "".join(f"<url><loc>{{base}}{path}</loc></url>"
                   for path in ("/", "/b/", *(["/old/"] if old else [])))
    mini.pages = {
        "/robots.txt": (200, {"Content-Type": "text/plain"},
                        "User-agent: *\nSitemap: {base}/sitemap.xml\n"),
        "/sitemap.xml": (200, {"Content-Type": "application/xml"}, f"<urlset>{urls}</urlset>"),
        "/": (200, {}, html(f"<main><h1>Kezdő</h1>{links}</main>", head="<title>Kezdő</title>")),
        "/b/": (200, {}, html("<main><h1>B</h1><p>bé oldal</p></main>")),
    }
    if old:
        mini.pages["/old/"] = (200, {}, html("<main><h1>Régi</h1><p>régi oldal</p></main>"))


async def test_a_page_gone_from_the_site_leaves_the_current_page_set(site, tools):  # noqa: F811
    con = connect(":memory:")
    small(site)
    await run(con, site, tools)
    assert {page.url for page in queries.pages(con)} == {site.url(p) for p in ("/", "/b/", "/old/")}
    small(site, old=False)                 # az /old/ eltűnt a linkekből, a sitemapből, a szerverről
    summary = await run(con, site, tools)
    assert summary.pages_done + summary.pages_skipped == 2
    # az elemzés oldalkészlete a legutóbbi crawl által látott oldalak
    assert {page.url for page in queries.pages(con)} == {site.url(p) for p in ("/", "/b/")}


async def test_a_new_noindex_header_is_seen_when_the_html_is_unchanged(site, tools):  # noqa: F811
    con = connect(":memory:")
    small(site)
    await run(con, site, tools)
    status, _, body = site.pages["/b/"]
    site.pages["/b/"] = (status, {"X-Robots-Tag": "noindex"}, body)
    await run(con, site, tools)
    noindex = dict(con.execute("SELECT url, noindex FROM pages").fetchall())
    assert noindex[site.url("/b/")] is True


async def test_an_earlier_failed_render_is_tried_again(site, tools):  # noqa: F811
    con = connect(":memory:")
    small(site)
    renderer, _ = tools
    original = renderer.render
    fail = {"on": True}

    async def flaky(url, **kwargs):
        result = await original(url, **kwargs)
        if fail["on"] and url.endswith("/b/"):          # az első crawlban a /b/ renderje elbukik
            return replace(result, rendered_html=None, error="timeout")
        return result

    renderer.render = flaky
    try:
        await run(con, site, tools)
        first = con.execute("SELECT error, rendered_html IS NOT NULL, raw_html_hash IS NOT NULL "
                            "FROM pages WHERE url = ?", [site.url("/b/")]).fetchone()
        assert first == ("timeout", False, True)
        fail["on"] = False
        await run(con, site, tools)
    finally:
        renderer.render = original
    second = con.execute("SELECT error, rendered_html IS NOT NULL FROM pages WHERE url = ?",
                         [site.url("/b/")]).fetchone()
    assert second == (None, True)


async def test_a_gone_page_no_longer_counts_after_the_rebuild(site, tools, tmp_path):  # noqa: F811
    con = connect(":memory:")
    small(site)
    service = ('<script type="application/ld+json">{"@context": "https://schema.org", '
               '"@type": "Service", "name": "Régi Szolgáltatás"}</script>')
    site.pages["/old/"] = (200, {}, html("<main><h1>Régi Szolgáltatás</h1><p>A Régi "
                                         "Szolgáltatás leírása.</p></main>", head=service))
    target = api.Site(con, tmp_path / "x.duckdb", "x")

    def state():
        api.rebuild_entities(target, knowledge=False)
        build_graph(con)
        build_findings(con)
        nodes = {url.rsplit("/", 2)[-2] for (url,) in con.execute(
            "SELECT url FROM page_nodes").fetchall()}
        # az entitás, amelyet az elemzés lát: van említése (a súlyozás és a megállapítások ebből
        # dolgoznak)
        ranked = {name for (name,) in con.execute(
            "SELECT DISTINCT e.name FROM entities e JOIN page_entities pe USING (entity_id)"
        ).fetchall()}
        return nodes, ranked

    await run(con, site, tools)
    nodes, ranked = state()
    assert "old" in nodes and "Régi Szolgáltatás" in ranked
    small(site, old=False)
    await run(con, site, tools)
    nodes, ranked = state()
    assert "old" not in nodes and "Régi Szolgáltatás" not in ranked
    assert con.execute("SELECT count(*) FROM page_entities pe JOIN pages p USING (page_id) "
                       "WHERE p.url LIKE '%/old/'").fetchone() == (0,)
    # a történet megmarad: az oldal sora és a blokkjai, az előző crawl jelölésével
    kept = con.execute(
        "SELECT p.seen_crawl_id, (SELECT count(*) FROM blocks b WHERE b.page_id = p.page_id) "
        "FROM pages p WHERE p.url LIKE '%/old/'").fetchone()
    assert kept[0] == 1 and kept[1] > 0
    assert con.execute("SELECT DISTINCT seen_crawl_id FROM pages WHERE url NOT LIKE '%/old/'"
                       ).fetchall() == [(2,)]


async def test_a_link_to_a_historical_page_has_no_target_in_the_current_set(site, tools):  # noqa: F811
    con = connect(":memory:")
    small(site)
    await run(con, site, tools)
    # a /b/ továbbra is linkeli az /old/ címet, de az oldal már 404 (a kezdőoldal nem linkeli)
    small(site, old=False)
    site.pages["/b/"] = (200, {}, html("<main><h1>B</h1><a href='/old/'>Régi</a></main>"))
    await run(con, site, tools)
    current = {page.url: page for page in queries.pages(con)}
    old = current[site.url("/old/")]
    assert old.status == 404                       # az új crawl látta: aktuális, 404-gyel
    assert {link.to_url: link.to_page_id for link in queries.links(con)
            if link.to_url.endswith("/old/")} == {site.url("/old/"): old.page_id}


def test_the_migration_fills_existing_databases_from_the_queue():
    con = connect(":memory:")
    for notes in ("új: https://pelda.hu/", "resume: https://pelda.hu/", "új: https://pelda.hu/",
                  "resume: https://pelda.hu/"):
        con.execute("INSERT INTO crawl_runs (started_at, notes) VALUES (current_timestamp, ?)",
                    [notes])
    con.execute("UPDATE crawl_runs SET crawl_id = NULL")
    for path, run_id in (("/", 3), ("/a/", 4), ("/regi/", 1)):
        con.execute("INSERT INTO pages (url, status, run_id) VALUES (?, 200, ?)",
                    [f"https://pelda.hu{path}", run_id])
    for path in ("/", "/a/"):
        con.execute("INSERT INTO crawl_queue (url, status) VALUES (?, 'done')",
                    [f"https://pelda.hu{path}"])
    con.execute((connect_module.MIGRATIONS_DIR / "029_current_pages.sql").read_text(
        encoding="utf-8"))
    # a folytatás a folytatott crawlhoz tartozik
    assert con.execute("SELECT run_id, crawl_id FROM crawl_runs ORDER BY run_id").fetchall() == [
        (1, 1), (2, 1), (3, 3), (4, 3)]
    # a sorban álló oldalak a legutóbbi crawléi; a sorban nem álló azé, amelyik utoljára írta
    assert dict(con.execute("SELECT url, seen_crawl_id FROM pages").fetchall()) == {
        "https://pelda.hu/": 3, "https://pelda.hu/a/": 3, "https://pelda.hu/regi/": 1}
    assert {page.url for page in queries.pages(con)} == {"https://pelda.hu/", "https://pelda.hu/a/"}
