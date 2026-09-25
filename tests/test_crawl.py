"""Crawl: egy szintetikus mini-site helyi HTTP-szerveren (127.0.0.1), hálózat nélkül.

A mini-site: robots.txt tiltással és sitemappel, átirányítás belső és külső célra, a
normalizált alakon 404 (a másik alakon 200), 500-as oldal, JSON-LD @graph-fal, X-Robots-Tag
noindex, és egy csak mélyebbről elérhető oldal.
"""
import asyncio
import hashlib
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import zstandard
from typer.testing import CliRunner

import aaa2.db.connect as connect_module
import aaa2.engine.crawl as crawl_module
from aaa2.cli.main import app
from aaa2.db.connect import connect
from aaa2.engine.crawl import NORMALIZED_404, CrawlOptions, crawl
from aaa2.engine.render import Renderer

HTML = "text/html; charset=utf-8"


def html(body, head=""):
    return f"<html lang='hu'><head>{head}</head><body>{body}</body></html>"


class MiniSite:
    """Útvonal → (státusz, fejlécek, body). A `{base}` és `{other}` a futó szerver címei."""

    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{port}"
        self.other = f"http://localhost:{port}"
        self.hits: list[str] = []
        self.pages = self._pages()

    def url(self, path):
        return f"{self.base}{path}"

    def body(self, path):
        return self.pages[path][2].replace("{base}", self.base).replace("{other}", self.other).encode()

    def _pages(self):
        ld = json.dumps({"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage", "name": "E"}, {"@type": "Organization", "name": "Mini"}]})
        return {
            "/robots.txt": (200, {"Content-Type": "text/plain"},
                            "User-agent: *\nDisallow: /tiltott/\nSitemap: {base}/sitemap.xml\n"),
            "/sitemap.xml": (200, {"Content-Type": "application/xml"},
                             ("<urlset><url><loc>{base}/</loc></url><url><loc>{base}/a/</loc></url>"
                              "<url><loc>{base}/b/</loc></url><url><loc>{base}/regi/</loc></url>"
                              "</urlset>")),
            "/": (200, {}, html(
                "<header><nav><a href='/a/'>A</a><a href='/b/'>B</a><a href='/c'>C</a></nav></header>"
                "<main><h1>Kezdőlap</h1><a href='/d/'>D</a><a href='/e/'>E</a>"
                "<a href='/tiltott/x/'>tiltott</a><a href='/ki/'>ki</a>"
                "<a href='{other}/kulso'>külső</a></main>"
                "<footer><a href='/regi/'>régi</a><a href='/rejtett-regi/'>rejtett</a>"
                "<a href='/szakad/'>szakad</a></footer>",
                head="<title>Kezdő</title>")),
            "/a/": (200, {}, html("<main><h1>A</h1><a href='/'>haza</a><a href='/a/#top'>fel</a>"
                                  "<a href='/f/'>F</a></main>")),
            "/b/": (200, {}, html("<main><h1>B</h1><p>bé oldal</p></main>")),
            "/c/": (404, {}, html("<h1>Nincs ilyen</h1>")),
            "/c": (200, {}, html("<main><h1>C</h1></main>")),
            "/d/": (500, {}, html("<h1>Szerverhiba</h1>")),
            "/e/": (200, {"X-Robots-Tag": "noindex"}, html(
                "<main><h1>E</h1><h2>Alcím</h2><a href='/'>haza</a></main>",
                head=f'<script type="application/ld+json">{ld}</script>')),
            "/f/": (200, {}, html("<main><h1>F</h1><a href='/a/'>A</a></main>")),
            "/regi/": (301, {"Location": "/b/"}, ""),
            "/rejtett-regi/": (301, {"Location": "/rejtett/"}, ""),
            "/rejtett/": (200, {}, html("<main><h1>Csak átirányításból</h1></main>")),
            "/szakad/": None,
            "/ki/": (302, {"Location": "{other}/kulso"}, ""),
            "/kulso": (200, {}, html("<p>másik host</p>")),
            "/tiltott/x/": (200, {}, html("<p>nem szabadna</p>")),
        }

    def _handler(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?", 1)[0]
                site.hits.append(path)
                if path in site.pages and site.pages[path] is None:
                    self.close_connection = True
                    return
                status, headers, _ = site.pages.get(path, (404, {}, ""))
                body = site.body(path) if path in site.pages else html("<h1>404</h1>").encode()
                self.send_response(status)
                for key, value in {"Content-Type": HTML, **headers}.items():
                    self.send_header(key, value.replace("{other}", site.other))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return Handler

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site():
    with MiniSite() as mini:
        yield mini


@pytest.fixture
async def tools():
    async with Renderer(concurrency=3, render_timeout=5.0, backoff=0) as renderer, \
            httpx.AsyncClient(timeout=5.0) as client:
        yield renderer, client


async def run(con, site, tools, **options):
    renderer, client = tools
    options.setdefault("concurrency", 3)
    return await crawl(con, site.url("/"), CrawlOptions(**options), client=client, renderer=renderer)


def pages(con):
    return {row[0]: row[1:] for row in con.execute(
        "SELECT url, status, error, final_url, page_id, fetched_at, run_id FROM pages").fetchall()}


def queue(con):
    return dict(con.execute("SELECT url, status FROM crawl_queue").fetchall())


EXPECTED_PAGES = {
    "/", "/a/", "/b/", "/c/", "/d/", "/e/", "/f/", "/regi/", "/ki/", "/rejtett-regi/",
    "/rejtett/", "/szakad/",
}


# ---------------------------------------------------------------------------
# teljes crawl
# ---------------------------------------------------------------------------


async def test_fresh_crawl_of_mini_site(site, tools):
    con = connect(":memory:")
    summary = await run(con, site, tools)
    rows = pages(con)
    assert set(rows) == {site.url(path) for path in EXPECTED_PAGES}
    assert rows[site.url("/")][:3] == (200, None, site.url("/"))
    assert rows[site.url("/c/")][:2] == (404, NORMALIZED_404)
    assert rows[site.url("/d/")][:2] == (500, None)
    assert rows[site.url("/regi/")][:3] == (301, None, site.url("/b/"))
    assert rows[site.url("/ki/")][:3] == (302, None, f"{site.other}/kulso")
    assert rows[site.url("/rejtett-regi/")][:3] == (301, None, site.url("/rejtett/"))
    assert rows[site.url("/rejtett/")][:3] == (200, None, site.url("/rejtett/"))
    assert rows[site.url("/szakad/")][0] is None
    assert rows[site.url("/szakad/")][1].startswith("network_error")
    assert "/tiltott/x/" not in site.hits

    states = queue(con)
    assert states[site.url("/szakad/")] == "failed"
    assert states[site.url("/rejtett/")] == "done"
    assert {s for url, s in states.items() if url != site.url("/szakad/")} == {"done"}
    assert site.url("/tiltott/x/") not in queue(con)

    (hash_, compressed, method) = con.execute(
        "SELECT raw_html_hash, rendered_html, main_content_method FROM pages WHERE url = ?",
        [site.url("/")]).fetchone()
    assert hash_ == hashlib.sha256(site.body("/")).hexdigest()
    assert "Kezdőlap" in zstandard.ZstdDecompressor().decompress(compressed).decode()
    assert method == "fallback_body"  # a mini-oldal main-je 100 szó alatti

    nav = con.execute(
        "SELECT l.to_url, l.position, l.to_page_id = p.page_id FROM links l "
        "JOIN pages s ON s.page_id = l.from_page_id LEFT JOIN pages p ON p.url = l.to_url "
        "WHERE s.url = ? ORDER BY l.ordinal", [site.url("/")]).fetchall()
    assert nav[:3] == [(site.url("/a/"), "nav", True), (site.url("/b/"), "nav", True),
                       (site.url("/c/"), "nav", True)]
    assert (site.url("/regi/"), "footer", True) in nav

    noindex, = con.execute("SELECT noindex FROM pages WHERE url = ?", [site.url("/e/")]).fetchone()
    assert noindex is True
    graph = [json.loads(j) for (j,) in con.execute(
        "SELECT s.json FROM schema_blocks s JOIN pages p USING (page_id) WHERE p.url = ? "
        "ORDER BY s.ordinal", [site.url("/e/")]).fetchall()]
    assert graph == [
        {"@context": "https://schema.org", "@type": "WebPage", "name": "E"},
        {"@context": "https://schema.org", "@type": "Organization", "name": "Mini"},
    ]
    headings = con.execute(
        "SELECT h.level, h.text FROM headings h JOIN pages p USING (page_id) WHERE p.url = ? "
        "ORDER BY h.ordinal", [site.url("/e/")]).fetchall()
    assert headings == [(1, "E"), (2, "Alcím")]

    site_row = con.execute(
        "SELECT robots_status, robots_txt, trailing_slash, page_count, crawled_at IS NOT NULL "
        "FROM site").fetchone()
    assert site_row[0] == 200 and "Disallow: /tiltott/" in site_row[1]
    assert site_row[2:] == (True, 12, True)

    run_row = con.execute(
        "SELECT finished_at IS NOT NULL, pages_done, pages_failed, pages_skipped, pages_per_sec, "
        "bytes_stored FROM crawl_runs").fetchall()
    assert len(run_row) == 1
    finished, done, failed, skipped, rate, stored = run_row[0]
    assert (finished, done, failed, skipped) == (True, 9, 3, 0)
    assert rate > 0 and stored > 0
    assert (summary.pages_done, summary.pages_failed, summary.bytes_stored) == (9, 3, stored)


async def test_redirect_target_is_one_page(site, tools):
    con = connect(":memory:")
    await run(con, site, tools)
    (count,) = con.execute("SELECT count(*) FROM pages WHERE url = ?", [site.url("/b/")]).fetchone()
    assert count == 1
    assert queue(con)[site.url("/b/")] == "done"
    links_from_redirect = con.execute(
        "SELECT count(*) FROM links l JOIN pages p ON p.page_id = l.from_page_id WHERE p.url = ?",
        [site.url("/regi/")]).fetchone()
    assert links_from_redirect == (0,)


async def test_page_transaction_is_all_or_nothing_and_resume_completes(site, tools, monkeypatch):
    con = connect(":memory:")
    original = crawl_module._Run._write_children
    target = site.url("/e/")

    def failing(self, page_id, parsed):
        original(self, page_id, parsed)
        (url,) = self.con.execute("SELECT url FROM pages WHERE page_id = ?", [page_id]).fetchone()
        if url == target:
            raise RuntimeError("írás közbeni hiba")

    monkeypatch.setattr(crawl_module._Run, "_write_children", failing)
    with pytest.raises(RuntimeError, match="írás közbeni hiba"):
        await run(con, site, tools, concurrency=1)
    assert target not in pages(con)
    assert queue(con)[target] == "queued"
    (orphans,) = con.execute(
        "SELECT count(*) FROM headings WHERE page_id NOT IN (SELECT page_id FROM pages)").fetchone()
    assert orphans == 0
    unfinished = con.execute("SELECT finished_at, notes FROM crawl_runs").fetchall()
    assert unfinished[0][0] is None and unfinished[0][1].endswith("(megszakítva)")

    monkeypatch.setattr(crawl_module._Run, "_write_children", original)
    await run(con, site, tools, resume=True)
    assert set(pages(con)) == {site.url(path) for path in EXPECTED_PAGES}
    assert set(queue(con).values()) == {"done", "failed"}


async def test_resume_after_interrupt_with_db_reopen(site, tools, tmp_path):
    path = tmp_path / "mini.duckdb"
    con = connect(path)
    renderer, client = tools
    written: list[str] = []
    task = None

    def stop_after_three(url, status, error):
        written.append(url)
        if len(written) == 3:
            task.cancel()

    task = asyncio.ensure_future(crawl(
        con, site.url("/"), CrawlOptions(concurrency=1), client=client, renderer=renderer,
        progress=stop_after_three))
    with pytest.raises(asyncio.CancelledError):
        await task
    before = pages(con)
    assert len(before) == 3
    assert "queued" in queue(con).values()
    con.close()

    con = connect(path)
    summary = await run(con, site, tools, resume=True)
    after = pages(con)
    assert set(after) == {site.url(p) for p in EXPECTED_PAGES}
    assert {url: after[url][3] for url in before} == {url: row[3] for url, row in before.items()}
    assert summary.pages_done + summary.pages_failed == len(after) - len(before)
    runs = con.execute("SELECT finished_at IS NULL, notes FROM crawl_runs ORDER BY run_id").fetchall()
    assert runs[0] == (True, runs[0][1]) and runs[0][1].endswith("(megszakítva)")
    assert runs[1][0] is False and runs[1][1].startswith("resume")
    con.close()


async def test_resume_needs_a_crawl(site, tools):
    con = connect(":memory:")
    with pytest.raises(ValueError, match="site tábla üres"):
        await run(con, site, tools, resume=True)
    assert con.execute("SELECT count(*) FROM crawl_runs").fetchone() == (0,)


# ---------------------------------------------------------------------------
# hash-alapú skip
# ---------------------------------------------------------------------------


async def test_hash_skip_leaves_unchanged_rows(site, tools):
    con = connect(":memory:")
    await run(con, site, tools)
    first = pages(con)
    site.pages["/b/"] = (200, {}, html("<main><h1>B</h1><p>megváltozott</p></main>"))
    con.execute("UPDATE pages SET fetched_at = fetched_at - ? WHERE url = ?",
                [timedelta(days=8), site.url("/f/")])

    summary = await run(con, site, tools)
    second = pages(con)
    assert summary.pages_skipped == 2
    for path in ("/a/", "/e/"):
        assert second[site.url(path)] == first[site.url(path)]
    for path in ("/b/", "/f/", "/"):
        assert second[site.url(path)][5] == 2
        assert second[site.url(path)][3] == first[site.url(path)][3]
    assert set(second) == set(first)
    assert set(queue(con).values()) == {"done", "failed"}
    b_headings = con.execute(
        "SELECT h.text FROM headings h JOIN pages p USING (page_id) WHERE p.url = ?",
        [site.url("/b/")]).fetchall()
    assert b_headings == [("B",)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_crawl_status_export(site, tmp_path, monkeypatch):
    monkeypatch.setattr(connect_module, "DATA_DIR", tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["crawl", site.url("/"), "--concurrency", "2", "--quiet"])
    assert result.exit_code == 0, result.output
    assert "kész: 9 oldal rendben, 3 hibás" in result.output
    assert (tmp_path / "127.0.0.1.duckdb").exists()

    status = runner.invoke(app, ["status", site.url("/")])
    assert status.exit_code == 0, status.output
    assert "127.0.0.1: 12 oldal" in status.output
    assert "0 várakozik, 11 kész, 1 hibás" in status.output

    out = tmp_path / "pages.csv"
    exported = runner.invoke(app, ["export", "127.0.0.1", "--table", "pages", "--csv", "--out", str(out)])
    assert exported.exit_code == 0, exported.output
    header, *rows = out.read_text(encoding="utf-8").splitlines()
    assert "rendered_html" not in header.split(",") and "final_url" in header.split(",")
    assert len(rows) == 12

    unknown = runner.invoke(app, ["export", "127.0.0.1", "--table", "pages; DROP TABLE pages"])
    assert unknown.exit_code == 1

    resumed = runner.invoke(app, ["crawl", site.url("/"), "--resume", "--quiet"])
    assert resumed.exit_code == 1
    assert "nincs várakozó" in resumed.output


# ---------------------------------------------------------------------------
# rögzített valódi site: Materia Trattoria (felvétel: pytest -m live -k record_materia)
# ---------------------------------------------------------------------------

MATERIA = "https://materia-tm.com/"


def crawl_snapshot(con):
    """A crawl sorrendfüggetlen eredménye, JSON-ba menthető formában."""
    return {
        "pages": [list(row) for row in con.execute(
            "SELECT url, status, error, final_url, main_content_method FROM pages ORDER BY url"
        ).fetchall()],
        "links": [list(row) for row in con.execute(
            "SELECT p.url, l.to_url, l.position, l.ordinal FROM links l "
            "JOIN pages p ON p.page_id = l.from_page_id ORDER BY p.url, l.ordinal").fetchall()],
        "queue": dict(con.execute(
            "SELECT status, count(*) FROM crawl_queue GROUP BY status").fetchall()),
        "site": list(con.execute(
            "SELECT trailing_slash, https_redirect, robots_status, page_count FROM site").fetchone()),
    }


@pytest.mark.live
async def test_live_record_materia_crawl():
    from tests.recorded import Recording

    recording = Recording("materia-crawl")
    recording.clear()
    async with Renderer(concurrency=3, upstream=recording.record) as renderer, httpx.AsyncClient(
        transport=recording.recording_transport(), timeout=20.0,
        headers={"User-Agent": renderer.user_agent},
    ) as client:
        con = connect(":memory:")
        summary = await crawl(con, MATERIA, CrawlOptions(concurrency=3), client=client,
                              renderer=renderer)
    snapshot = crawl_snapshot(con)
    recording.save(**snapshot)
    print(f"\nmateria felvétel: {summary} válasz={len(recording.responses)} "
          f"oldal={len(snapshot['pages'])} link={len(snapshot['links'])}")
    assert summary.pages_done >= 5
    assert snapshot["queue"].get("queued", 0) == 0


async def test_replay_materia_crawl():
    from tests.recorded import Recording

    recording = Recording("materia-crawl")
    if not recording.exists:
        pytest.skip("nincs felvétel: pytest -m live -k record_materia")
    async with Renderer(concurrency=3, upstream=recording.replay) as renderer, httpx.AsyncClient(
        transport=recording.replay_transport(), timeout=20.0,
    ) as client:
        con = connect(":memory:")
        await crawl(con, MATERIA, CrawlOptions(concurrency=3), client=client, renderer=renderer)
    assert crawl_snapshot(con) == json.loads(json.dumps(recording.measured))
