"""Frontier: felmérés hálózat nélkül (httpx.MockTransport), sor memóriabeli vagy fájl-DuckDB-ben."""
import gzip

import httpx
import pytest

from aaa2.db.connect import connect
from aaa2.engine.frontier import (
    PRIORITY,
    Discovery,
    Frontier,
    Robots,
    discover,
    probe_https_redirect,
    read_sitemaps,
)

SEED = "https://kk.coach/"

ROBOTS = """\
# kk.coach
User-agent: Googlebot
Disallow: /

User-agent: *
Disallow: /wp-admin/
Allow: /wp-admin/admin-ajax.php
Disallow: /*?s=
Disallow: /privat$

Sitemap: https://kk.coach/sitemap_index.xml
"""

SITEMAP_INDEX = """\
<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://kk.coach/page-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://kk.coach/post-sitemap.xml.gz</loc></sitemap>
  <sitemap><loc>https://kk.coach/sitemap_index.xml</loc></sitemap>
</sitemapindex>
"""

PAGE_SITEMAP = """\
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url><loc>https://kk.coach/</loc></url>
  <url>
    <loc>https://kk.coach/rolam/</loc>
    <xhtml:link rel="alternate" hreflang="en" href="https://kk.coach/en/about/"/>
  </url>
  <url>
    <loc><![CDATA[https://kk.coach/szolgaltatasok/]]></loc>
    <image:image><image:loc>https://kk.coach/img/a.jpg</image:loc></image:image>
  </url>
  <url><loc> https://kk.coach/kereses/?a=1&amp;b=2 </loc></url>
</urlset>
"""

POST_SITEMAP = """\
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://kk.coach/blog/elso/</loc></url>
  <url><loc>https://kk.coach/rolam/</loc></url>
</urlset>
"""

SITEMAP_URLS = [
    "https://kk.coach/",
    "https://kk.coach/rolam/",
    "https://kk.coach/szolgaltatasok/",
    "https://kk.coach/kereses/?a=1&b=2",
    "https://kk.coach/blog/elso/",
]


def mock_client(routes):
    """URL → válasz: str/bytes = 200 body, int = státusz, (státusz, fejlécek), vagy kivétel."""

    def handler(request):
        spec = routes.get(str(request.url), 404)
        if isinstance(spec, Exception):
            raise spec
        if isinstance(spec, int):
            return httpx.Response(spec)
        if isinstance(spec, tuple):
            return httpx.Response(spec[0], headers=spec[1])
        return httpx.Response(200, content=spec.encode() if isinstance(spec, str) else spec)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


SITE_ROUTES = {
    "http://kk.coach/": (301, {"location": "https://kk.coach/"}),
    "https://kk.coach/robots.txt": ROBOTS,
    "https://kk.coach/sitemap_index.xml": SITEMAP_INDEX,
    "https://kk.coach/page-sitemap.xml": PAGE_SITEMAP,
    "https://kk.coach/post-sitemap.xml.gz": gzip.compress(POST_SITEMAP.encode()),
}


# ---------------------------------------------------------------------------
# sitemap
# ---------------------------------------------------------------------------


async def test_read_sitemaps_index_gzip_cdata_entities_dedup():
    async with mock_client(SITE_ROUTES) as client:
        urls = await read_sitemaps(client, ["https://kk.coach/sitemap_index.xml"])
    assert urls == SITEMAP_URLS


async def test_read_sitemaps_max_urls():
    async with mock_client(SITE_ROUTES) as client:
        urls = await read_sitemaps(client, ["https://kk.coach/sitemap_index.xml"], max_urls=2)
    assert urls == SITEMAP_URLS[:2]


async def test_read_sitemaps_max_files():
    async with mock_client(SITE_ROUTES) as client:
        urls = await read_sitemaps(client, ["https://kk.coach/sitemap_index.xml"], max_files=2)
    assert urls == SITEMAP_URLS[:4]


async def test_read_sitemaps_missing_broken_and_html():
    routes = {
        "https://kk.coach/broken.xml.gz": b"\x1f\x8b nem gzip",
        "https://kk.coach/soft404.xml": "<html><body>Nincs ilyen oldal</body></html>",
        "https://kk.coach/error.xml": 500,
        "https://kk.coach/down.xml": httpx.ConnectError("le van állva"),
    }
    roots = [f"https://kk.coach/{name}" for name in
             ("missing.xml", "broken.xml.gz", "soft404.xml", "error.xml", "down.xml")]
    async with mock_client(routes) as client:
        assert await read_sitemaps(client, roots) == []


# ---------------------------------------------------------------------------
# https-próba
# ---------------------------------------------------------------------------

PROBES = [
    ("301 https-re", (301, {"location": "https://kk.coach/"}), (True, 301)),
    ("308 https-re", (308, {"location": "https://kk.coach/"}), (True, 308)),
    ("302 https-re", (302, {"location": "https://kk.coach/"}), (True, 302)),
    ("307 https-re", (307, {"location": "https://kk.coach/"}), (True, 307)),
    ("301 https www-re", (301, {"location": "https://www.kk.coach/"}), (True, 301)),
    ("303 nem átirányítás https-re", (303, {"location": "https://kk.coach/"}), (False, 303)),
    ("nincs átirányítás", 200, (False, 200)),
    ("302 http-re", (302, {"location": "http://www.kk.coach/"}), (False, 302)),
    ("301 relatív cél, marad http", (301, {"location": "/hu/"}), (False, 301)),
    ("307 más domainre", (307, {"location": "https://example.com/"}), (False, 307)),
    ("hálózati hiba", httpx.ConnectError("nincs kapcsolat"), (False, None)),
]


@pytest.mark.parametrize(("response", "expected"), [p[1:] for p in PROBES], ids=[p[0] for p in PROBES])
async def test_probe_https_redirect(response, expected):
    async with mock_client({"http://kk.coach/": response}) as client:
        assert await probe_https_redirect(client, SEED) == expected


async def test_probe_uses_seed_path():
    routes = {"http://kk.coach/hu/": (302, {"location": "https://kk.coach/hu/"})}
    async with mock_client(routes) as client:
        assert await probe_https_redirect(client, "https://kk.coach/hu/") == (True, 302)


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

ROBOTS_CASES = [
    ("szabad oldal", ROBOTS, "/rolam/", True),
    ("tiltott könyvtár", ROBOTS, "/wp-admin/", False),
    ("tiltott könyvtár alatt", ROBOTS, "/wp-admin/options.php", False),
    ("hosszabb Allow nyer", ROBOTS, "/wp-admin/admin-ajax.php", True),
    ("wildcard query-ben", ROBOTS, "/?s=kereses", False),
    ("wildcard mélyebb path-on", ROBOTS, "/blog/?s=x", False),
    ("$ pontos vég", ROBOTS, "/privat", False),
    ("$ után folytatás szabad", ROBOTS, "/privat/x", True),
    ("más user-agent csoport nem számít", ROBOTS, "/", True),
    ("egyenlő hossz: Allow nyer", "User-agent: *\nDisallow: /a\nAllow: /a\n", "/a", True),
    ("üres Disallow mindent enged", "User-agent: *\nDisallow:\n", "/barmi", True),
    ("fájl-vég minta", "User-agent: *\nDisallow: /*.pdf$\n", "/doc.pdf", False),
    ("fájl-vég minta query-vel szabad", "User-agent: *\nDisallow: /*.pdf$\n", "/doc.pdf?x=1", True),
    ("nem-ASCII minta a kódolt URL-re", "User-agent: *\nDisallow: /könyv/\n", "/k%C3%B6nyv/x", False),
    ("kisbetűs escape a mintában", "User-agent: *\nDisallow: /k%c3%b6nyv/\n", "/k%C3%B6nyv/x", False),
    ("kis-nagybetű a path-ban számít", "User-agent: *\nDisallow: /Admin\n", "/admin", True),
    ("kulcs kis-nagybetűtől független", "USER-AGENT: *\nDISALLOW: /x\n", "/x", False),
    ("közös csoport több agenttel", "User-agent: bot\nUser-agent: *\nDisallow: /x\n", "/x", False),
    ("új agent-csoport lezárja az előzőt",
     "User-agent: *\nDisallow: /a\nUser-agent: bot\nDisallow: /b\n", "/b", True),
    ("két *-csoport összeadódik",
     "User-agent: *\nDisallow: /a\n\nUser-agent: *\nDisallow: /b\n", "/b", False),
    ("ismeretlen sor nem zár csoportot",
     "User-agent: bot\nCrawl-delay: 5\nUser-agent: *\nDisallow: /x\n", "/x", False),
    ("komment a sor végén", "User-agent: * # mind\nDisallow: /x # titok\n", "/x", False),
]


@pytest.mark.parametrize(
    ("text", "path", "expected"), [c[1:] for c in ROBOTS_CASES], ids=[c[0] for c in ROBOTS_CASES]
)
def test_robots_allowed(text, path, expected):
    assert Robots.parse(text).allowed(f"https://kk.coach{path}") is expected


def test_robots_sitemaps():
    text = ROBOTS + "sitemap: https://kk.coach/extra.xml\nSitemap:\n"
    assert Robots.parse(text).sitemaps == (
        "https://kk.coach/sitemap_index.xml",
        "https://kk.coach/extra.xml",
    )


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


async def test_discover_from_robots_sitemap():
    async with mock_client(SITE_ROUTES) as client:
        found = await discover(client, SEED)
    assert found.https_redirect is True
    assert found.https_redirect_status == 301
    assert (found.robots_status, found.robots_txt) == (200, ROBOTS)
    assert found.robots is not None and not found.robots.allowed("https://kk.coach/wp-admin/")
    assert list(found.sitemap_urls) == SITEMAP_URLS


async def test_discover_falls_back_to_sitemap_xml():
    routes = {"https://kk.coach/sitemap.xml": POST_SITEMAP}
    async with mock_client(routes) as client:
        found = await discover(client, SEED)
    assert found == Discovery(
        https_redirect=False,
        https_redirect_status=404,
        robots=None,
        robots_status=404,
        robots_txt=None,
        sitemap_urls=("https://kk.coach/blog/elso/", "https://kk.coach/rolam/"),
    )


async def test_discover_sitemap_override_wins():
    routes = {**SITE_ROUTES, "https://kk.coach/custom.xml": POST_SITEMAP}
    async with mock_client(routes) as client:
        found = await discover(client, SEED, sitemap="https://kk.coach/custom.xml")
    assert found.sitemap_urls == ("https://kk.coach/blog/elso/", "https://kk.coach/rolam/")


async def test_discover_without_robots_still_uses_robots_sitemaps():
    async with mock_client(SITE_ROUTES) as client:
        found = await discover(client, SEED, respect_robots=False)
    assert found.robots is None
    assert list(found.sitemap_urls) == SITEMAP_URLS


async def test_discover_robots_status_without_text():
    routes = {"https://kk.coach/robots.txt": 503, "https://kk.coach/sitemap.xml": POST_SITEMAP}
    async with mock_client(routes) as client:
        found = await discover(client, SEED)
    assert (found.robots_status, found.robots_txt, found.robots) == (503, None, None)
    async with mock_client({"https://kk.coach/robots.txt": httpx.ConnectError("le")}) as client:
        down = await discover(client, SEED)
    assert (down.robots_status, down.robots_txt) == (None, None)


async def test_discover_robots_follows_redirect():
    routes = {
        "http://kk.coach/robots.txt": (301, {"location": "https://kk.coach/robots.txt"}),
        "https://kk.coach/robots.txt": "User-agent: *\nDisallow: /x\n",
    }
    async with mock_client(routes) as client:
        found = await discover(client, "http://kk.coach/")
    assert found.robots is not None and not found.robots.allowed("https://kk.coach/x")


# ---------------------------------------------------------------------------
# Frontier.start
# ---------------------------------------------------------------------------

SEED_LINKS = [
    ("https://kk.coach/rolam/", "nav"),
    ("https://kk.coach/szolgaltatasok/", "nav"),
    ("http://kk.coach/blog/", "nav"),
    ("https://kk.coach/blog/elso/", "body"),
    ("https://www.kk.coach/kapcsolat/#urlap", "body"),
    ("https://kk.coach/rolam/?utm_source=nav", "body"),
    ("https://example.com/partner/", "body"),
    ("https://cdn.kk.coach/app.js", "body"),
    ("mailto:hello@kk.coach", "body"),
    ("https://kk.coach/adatvedelem/", "footer"),
    ("https://kk.coach/wp-admin/", "footer"),
    ("https://kk.coach/uj-oldal/", "aside"),
]

DISCOVERY = Discovery(
    https_redirect=True,
    https_redirect_status=301,
    robots=Robots.parse(ROBOTS),
    sitemap_urls=tuple(SITEMAP_URLS),
)


def queue(con):
    return con.execute(
        "SELECT url, depth, priority, status, discovered_from FROM crawl_queue "
        "ORDER BY depth, priority, url"
    ).fetchall()


def site_row(con):
    return con.execute(
        "SELECT domain, seed_url, trailing_slash, https_redirect, https_redirect_status FROM site"
    ).fetchall()


def test_start_fills_queue_in_order():
    con = connect(":memory:")
    Frontier.start(con, SEED, DISCOVERY, SEED_LINKS)
    q = "queued"
    assert queue(con) == [
        ("https://kk.coach/", 0, PRIORITY["seed"], q, None),
        ("https://kk.coach/blog/elso/", 1, PRIORITY["sitemap"], q, None),
        ("https://kk.coach/kereses/?a=1&b=2", 1, PRIORITY["sitemap"], q, None),
        ("https://kk.coach/rolam/", 1, PRIORITY["sitemap"], q, None),
        ("https://kk.coach/szolgaltatasok/", 1, PRIORITY["sitemap"], q, None),
        ("https://kk.coach/blog/", 1, PRIORITY["nav"], q, SEED),
        ("https://kk.coach/kapcsolat/", 1, PRIORITY["body"], q, SEED),
        ("https://kk.coach/uj-oldal/", 1, PRIORITY["aside"], q, SEED),
        ("https://kk.coach/adatvedelem/", 1, PRIORITY["footer"], q, SEED),
    ]
    assert site_row(con) == [("kk.coach", SEED, True, True, 301)]


def test_next_batch_hands_out_queue_order():
    con = connect(":memory:")
    frontier = Frontier.start(con, SEED, DISCOVERY, SEED_LINKS)
    assert [item.url for item in frontier.next_batch(100)] == [row[0] for row in queue(con)]


def test_start_keeps_www_seed_form():
    con = connect(":memory:")
    frontier = Frontier.start(
        con, "http://WWW.kk.coach", Discovery(https_redirect=True),
        [("https://kk.coach/rolam/", "nav")],
    )
    assert frontier.seed_url == "https://www.kk.coach/"
    assert [row[0] for row in queue(con)] == ["https://www.kk.coach/", "https://www.kk.coach/rolam/"]


def test_start_normalizes_non_root_seed_with_decision():
    con = connect(":memory:")
    links = [("https://kk.coach/blog/a/", "body"), ("https://kk.coach/blog/b/", "body")]
    frontier = Frontier.start(con, "https://kk.coach/blog", Discovery(), links)
    assert frontier.seed_url == "https://kk.coach/blog/"
    assert queue(con)[0][:3] == ("https://kk.coach/blog/", 0, PRIORITY["seed"])


def test_start_resets_previous_queue():
    con = connect(":memory:")
    Frontier.start(con, SEED, DISCOVERY, SEED_LINKS)
    Frontier.start(con, SEED, Discovery(), [("https://kk.coach/masik/", "nav")])
    assert [row[0] for row in queue(con)] == [SEED, "https://kk.coach/masik/"]
    assert site_row(con) == [("kk.coach", SEED, True, False, None)]


def test_start_rolls_back_on_error():
    con = connect(":memory:")
    Frontier.start(con, SEED, DISCOVERY, SEED_LINKS)
    before = queue(con)
    broken_links = [("https://kk.coach/a/", "nav"), ("https://kk.coach/b/", ["nav"])]
    with pytest.raises(TypeError):
        Frontier.start(con, SEED, Discovery(), broken_links)
    assert queue(con) == before
    assert site_row(con) == [("kk.coach", SEED, True, True, 301)]


# ---------------------------------------------------------------------------
# trailing-slash döntés
# ---------------------------------------------------------------------------


def _links(*paths, position="body"):
    return [(f"https://kk.coach{p}", position) for p in paths]


def _sitemap(*paths):
    return Discovery(sitemap_urls=tuple(f"https://kk.coach{p}" for p in paths))


TRAILING = [
    ("linkek többsége slash-es", _links("/a/", "/b/", "/c"), _sitemap("/x", "/y"), True),
    ("linkek többsége slash nélküli", _links("/a", "/b", "/c/"), _sitemap("/x/", "/y/"), False),
    ("link-döntetlen: a sitemap dönt", _links("/a/", "/b"), _sitemap("/x/", "/y/", "/z"), True),
    ("link-döntetlen, sitemap is döntetlen", _links("/a/", "/b"), _sitemap("/x/", "/y"), None),
    ("link-döntetlen, nincs sitemap", _links("/a/", "/b"), Discovery(), None),
    ("nincs link: a sitemap dönt", [], _sitemap("/x", "/y", "/z/"), False),
    ("ismétlődő link egyszer szavaz", _links(*["/a"] * 30, "/b/", "/c/"), Discovery(), True),
    ("külső link nem szavaz",
     [(f"https://example.com/p{i}", "body") for i in range(10)] + _links("/b/"), Discovery(), True),
    ("csak az első 50 különböző link",
     _links(*[f"/n{i}" for i in range(50)], *[f"/s{i}/" for i in range(60)]), Discovery(), False),
    ("sitemap: csak a belső URL-ek",
     [], Discovery(sitemap_urls=("https://example.com/a", "https://example.com/b",
                                 "https://kk.coach/c/")), True),
]


@pytest.mark.parametrize(
    ("links", "discovery", "expected"), [t[1:] for t in TRAILING], ids=[t[0] for t in TRAILING]
)
def test_trailing_slash_decision(links, discovery, expected):
    con = connect(":memory:")
    frontier = Frontier.start(con, SEED, discovery, links)
    assert frontier.policy.trailing_slash is expected
    assert site_row(con)[0][2] is expected


def test_trailing_slash_decision_applies_to_sitemap_urls():
    con = connect(":memory:")
    Frontier.start(con, SEED, _sitemap("/x/", "/y/"), _links("/a", "/b", "/c/"))
    assert [row[0] for row in queue(con)] == [
        SEED, "https://kk.coach/x", "https://kk.coach/y",
        "https://kk.coach/a", "https://kk.coach/b", "https://kk.coach/c",
    ]


# ---------------------------------------------------------------------------
# szűrők és korlát
# ---------------------------------------------------------------------------

FILTERS = [
    ("include", "/blog/", None,
     [SEED, "https://kk.coach/blog/elso/", "https://kk.coach/blog/"]),
    ("exclude", None, "/blog/",
     [SEED, "https://kk.coach/kereses/?a=1&b=2", "https://kk.coach/rolam/",
      "https://kk.coach/szolgaltatasok/", "https://kk.coach/kapcsolat/",
      "https://kk.coach/uj-oldal/", "https://kk.coach/adatvedelem/"]),
    ("exclude nyer az include ellen", "/blog/", "/elso/", [SEED, "https://kk.coach/blog/"]),
    ("a normalizált URL-re illeszt", r"\?a=1&b=2$", None, [SEED, "https://kk.coach/kereses/?a=1&b=2"]),
]


@pytest.mark.parametrize(
    ("include", "exclude", "expected"), [f[1:] for f in FILTERS], ids=[f[0] for f in FILTERS]
)
def test_include_exclude(include, exclude, expected):
    con = connect(":memory:")
    Frontier.start(con, SEED, DISCOVERY, SEED_LINKS, include=include, exclude=exclude)
    assert [row[0] for row in queue(con)] == expected


def test_seed_bypasses_filters_and_robots():
    con = connect(":memory:")
    discovery = Discovery(robots=Robots.parse("User-agent: *\nDisallow: /\n"))
    Frontier.start(con, SEED, discovery, _links("/a/"), include="/semmi/", exclude="kk")
    assert [row[0] for row in queue(con)] == [SEED]


def test_robots_only_applies_to_seed_host():
    con = connect(":memory:")
    discovery = Discovery(robots=Robots.parse("User-agent: *\nDisallow: /x/\n"))
    Frontier.start(con, SEED, discovery, [
        ("https://kk.coach/x/", "body"),
        ("https://www.kk.coach/x/a/", "body"),
        ("https://en.kk.coach/x/", "body"),
    ])
    assert [row[0] for row in queue(con)] == [SEED, "https://en.kk.coach/x/"]


def test_max_pages_is_a_hard_limit():
    con = connect(":memory:")
    frontier = Frontier.start(con, SEED, DISCOVERY, SEED_LINKS, max_pages=3)
    assert [row[0] for row in queue(con)] == [
        SEED, "https://kk.coach/rolam/", "https://kk.coach/szolgaltatasok/",
    ]
    (item,) = frontier.next_batch(1)
    assert frontier.add_links(item, _links("/uj/")) == 0
    assert len(queue(con)) == 3


# ---------------------------------------------------------------------------
# BFS, kiadás, állapot
# ---------------------------------------------------------------------------


def test_add_links_depth_priority_and_rediscovery():
    con = connect(":memory:")
    frontier = Frontier.start(con, SEED, Discovery(), _links("/a/", position="footer"))
    seed, a = frontier.next_batch(2)
    assert a.url == "https://kk.coach/a/"
    frontier.mark_done(seed.url)
    added = frontier.add_links(a, [
        ("https://kk.coach/b/", "body"),
        ("https://kk.coach/b/#x", "nav"),
        ("https://kk.coach/", "nav"),
        ("https://kk.coach/a/", "nav"),
    ])
    assert added == 1
    rows = {row[0]: row[1:4] for row in queue(con)}
    assert rows["https://kk.coach/b/"] == (2, PRIORITY["nav"], "queued")
    assert rows[SEED] == (0, PRIORITY["seed"], "done")
    assert rows["https://kk.coach/a/"] == (1, PRIORITY["nav"], "queued")


def test_rediscovery_never_raises_depth_or_priority():
    con = connect(":memory:")
    frontier = Frontier.start(con, SEED, Discovery(), _links("/a/", position="nav"))
    seed, _ = frontier.next_batch(2)
    frontier.add_links(seed, _links("/a/", position="footer"))
    assert {row[0]: row[1:3] for row in queue(con)}["https://kk.coach/a/"] == (1, PRIORITY["nav"])


def test_rediscovery_leaves_finished_rows_alone():
    con = connect(":memory:")
    frontier = Frontier.start(con, SEED, Discovery(), _links("/a/", "/b/", position="footer"))
    seed, a, b = frontier.next_batch(3)
    frontier.mark_done(a.url)
    frontier.mark_failed(b.url, "404")
    frontier.add_links(seed, _links("/a/", "/b/", position="nav"))
    rows = {row[0]: row[1:4] for row in queue(con)}
    assert rows[a.url] == (1, PRIORITY["footer"], "done")
    assert rows[b.url] == (1, PRIORITY["footer"], "failed")


def test_start_stores_robots_file():
    con = connect(":memory:")
    text = "User-agent: *\nDisallow: /x/\n"
    Frontier.start(con, SEED, Discovery(robots_status=200, robots_txt=text))
    assert con.execute("SELECT robots_status, robots_txt FROM site").fetchone() == (200, text)


def test_claim_marks_redirect_target_done():
    con = connect(":memory:")
    discovery = Discovery(robots=Robots.parse("User-agent: *\nDisallow: /tiltott/\n"))
    frontier = Frontier.start(con, SEED, discovery, _links("/a/", "/b/"), max_pages=3)
    assert frontier.claim("https://kk.coach/b", depth=1) == "https://kk.coach/b/"
    assert frontier.claim("https://kk.coach/b/", depth=1) is None
    assert frontier.claim("https://kk.coach/uj/", depth=2, discovered_from=SEED) == "https://kk.coach/uj/"
    assert frontier.claim("https://example.com/", depth=1) is None
    assert frontier.claim("https://kk.coach/tiltott/", depth=1) is None
    _, a_item = frontier.next_batch(2)
    assert a_item.url == "https://kk.coach/a/"
    assert frontier.claim("https://kk.coach/a/", depth=1) is None
    rows = {row[0]: row[1:4] for row in queue(con)}
    assert rows["https://kk.coach/b/"][2] == "done"
    assert rows["https://kk.coach/uj/"] == (2, PRIORITY["body"], "done")
    assert rows["https://kk.coach/a/"][2] == "queued"
    assert len(rows) == 4


def test_leases_and_status():
    con = connect(":memory:")
    frontier = Frontier.start(con, SEED, DISCOVERY, SEED_LINKS)
    first = frontier.next_batch(3)
    second = frontier.next_batch(3)
    assert not {i.url for i in first} & {i.url for i in second}
    frontier.mark_done(first[0].url)
    frontier.mark_failed(first[1].url, "timeout")
    statuses = {row[0]: row[3] for row in queue(con)}
    assert statuses[first[0].url] == "done"
    assert statuses[first[1].url] == "failed"
    (error,) = con.execute(
        "SELECT error FROM crawl_queue WHERE url = ?", [first[1].url]
    ).fetchone()
    assert error == "timeout"
    rest = frontier.next_batch(100)
    assert first[2].url not in {i.url for i in rest}
    assert len(rest) == 9 - 6


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def test_resume_continues_from_queued_rows(tmp_path):
    path = tmp_path / "kk.coach.duckdb"
    con = connect(path)
    frontier = Frontier.start(con, SEED, DISCOVERY, SEED_LINKS)
    done, failed, in_flight = frontier.next_batch(3)
    frontier.mark_done(done.url)
    frontier.mark_failed(failed.url, "500")
    con.close()

    con = connect(path)
    resumed = Frontier.resume(con, robots=DISCOVERY.robots)
    assert resumed.policy.trailing_slash is True
    assert resumed.policy.https_redirect is True
    assert resumed.seed_url == SEED
    batch = resumed.next_batch(100)
    assert batch[0] == in_flight
    assert {i.url for i in batch} == {
        row[0] for row in queue(con) if row[3] == "queued"
    }
    assert done.url not in {i.url for i in batch}

    source = batch[0]
    assert resumed.add_links(source, [
        ("http://www.kk.coach/uj", "body"),
        (done.url, "nav"),
        ("https://kk.coach/wp-admin/", "nav"),
    ]) == 1
    assert "https://kk.coach/uj/" in {row[0] for row in queue(con)}
    con.close()


def test_resume_needs_a_started_crawl():
    with pytest.raises(ValueError, match="site tábla üres"):
        Frontier.resume(connect(":memory:"))


def test_resume_needs_queued_rows():
    con = connect(":memory:")
    frontier = Frontier.start(con, SEED, Discovery())
    for item in frontier.next_batch(10):
        frontier.mark_done(item.url)
    with pytest.raises(ValueError, match="nincs várakozó"):
        Frontier.resume(con)


# ---------------------------------------------------------------------------
# végig: discover → start
# ---------------------------------------------------------------------------


async def test_discover_then_start_without_network():
    async with mock_client(SITE_ROUTES) as client:
        found = await discover(client, SEED)
    live, reference = connect(":memory:"), connect(":memory:")
    frontier = Frontier.start(live, "http://kk.coach", found, SEED_LINKS)
    Frontier.start(reference, SEED, DISCOVERY, SEED_LINKS)
    assert frontier.seed_url == SEED
    assert len(queue(live)) == 9
    assert queue(live) == queue(reference)
    assert site_row(live) == site_row(reference)
