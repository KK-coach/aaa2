"""Egy SF-konfiguráció ellenőrzése helyi próba-site-on, az `aaa crawl` viselkedéséhez mérve.

    python -m tests.acceptance.verify_sf_config tests/acceptance/aaa2-acceptance.seospiderconfig
    python -m tests.acceptance.verify_sf_config tests/acceptance/aaa2-acceptance-ngx.seospiderconfig --ngx

A próba-site minden kérést naplóz (út, User-Agent), a napló a kimenetre kerül. Az SF kétszer
crawlol: a gyökérből és a `/a/` mappából. Pontok:

- a UA bájtra egyezik az aaa UA-jával (Playwright Chromium, `render.py`);
- JS-render: a JS-sel beszúrt link célja (`/js-only/`) crawlolva;
- robots.txt tisztelve: a tiltott `/private/x` nincs lekérve;
- nofollow belső link követve: `/nf/` lekérve;
- sitemap a robots.txt-ből: a `sitemap.xml` lekérve, a csak benne szereplő `/orphan/` crawlolva;
- hreflang- és canonical-cél crawlolva: `/de/`, `/canon/`;
- a kezdő mappán kívülre is megy: a `/a/`-ból indítva a `/b/` crawlolva.

`--ngx`: a kezdő mappára szűkített konfiguráció. A `/a/`-ból indítva a mappán belüli `/a/sub/`
crawlolva, a mappán kívüli oldal (`/`, `/b/`, …) nincs lekérve; a robots.txt és a sitemap igen.
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.acceptance.run_acceptance import aaa_user_agent, find_sf_cli

PAGES = {
    "/": ('<link rel="alternate" hreflang="de" href="/de/"><link rel="canonical" href="/">',
          ('<a href="/a/">A</a> <a href="/b/">B</a> <a href="/private/x">P</a> '
           '<a href="/nf/" rel="nofollow">NF</a> <div id="js"></div>'
           '<script>document.getElementById("js").innerHTML=\'<a href="/js-only/">JS</a>\''
           '</script>')),
    "/a/": ('<link rel="canonical" href="/canon/">',
            '<a href="/">home</a> <a href="/b/">B</a> <a href="/a/sub/">sub</a>'),
    "/a/sub/": ("", '<a href="/a/">A</a>'),
    "/b/": ("", '<a href="/">home</a>'),
    "/nf/": ("", "nofollow cél"),
    "/private/x": ("", "tiltott"),
    "/js-only/": ("", "js"),
    "/orphan/": ("", "csak sitemap"),
    "/de/": ("", "hreflang-cél"),
    "/canon/": ("", "canonical-cél"),
}
NOT_PAGES = ("/robots.txt", "/sitemap.xml")


def serve(requests: list[tuple[str, str]]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append((self.path, self.headers.get("User-Agent") or ""))
            base = f"http://127.0.0.1:{self.server.server_port}"
            if self.path == "/robots.txt":
                self._send(200, f"User-agent: *\nDisallow: /private/\nSitemap: {base}/sitemap.xml\n",
                           "text/plain")
            elif self.path == "/sitemap.xml":
                self._send(200, '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/'
                                f'schemas/sitemap/0.9"><url><loc>{base}/orphan/</loc></url></urlset>',
                           "application/xml")
            elif self.path in PAGES:
                head, body = PAGES[self.path]
                self._send(200, f"<html><head><title>{self.path}</title>{head}</head>"
                                f"<body>{body}</body></html>", "text/html; charset=utf-8")
            else:
                self._send(404, "<html><body>nincs</body></html>", "text/html")

        def _send(self, status: int, text: str, content_type: str) -> None:
            data = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def crawl(sf_cli: Path, config: Path, url: str, out: Path) -> int:
    """Egy headless SF-crawl; hibánál a napló vége a kimenetre."""
    out.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [str(sf_cli), "--crawl", url, "--headless", "--config", str(config.resolve()),
         "--output-folder", str(out), "--overwrite"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode:
        print("\n".join((completed.stdout + completed.stderr).splitlines()[-15:]))
    return completed.returncode


def print_log(title: str, requests: list[tuple[str, str]]) -> None:
    print(f"kérésnapló, {title}:")
    seen: list[tuple[str, str]] = []
    for request in requests:
        if request not in seen:
            seen.append(request)
    for path, agent in seen:
        print(f"  {path}  |  {agent}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--ngx", action="store_true", help="a kezdő mappára szűkített konfiguráció")
    args = parser.parse_args(argv)
    sf_cli = find_sf_cli()
    if sf_cli is None or not args.config.exists():
        print(f"Nincs SF CLI vagy konfiguráció: {sf_cli}, {args.config}")
        return 2
    expected_ua = asyncio.run(aaa_user_agent())
    requests: list[tuple[str, str]] = []
    server = serve(requests)
    base = f"http://127.0.0.1:{server.server_port}"
    with tempfile.TemporaryDirectory() as tmp:
        root_exit = crawl(sf_cli, args.config, f"{base}/", Path(tmp) / "root")
        root = list(requests)
        folder_exit = crawl(sf_cli, args.config, f"{base}/a/", Path(tmp) / "folder")
        folder = requests[len(root):]
    server.shutdown()
    print_log(f"crawl a gyökérből ({base}/)", root)
    print_log(f"crawl a /a/ mappából ({base}/a/)", folder)
    root_paths, folder_paths = {p for p, _ in root}, {p for p, _ in folder}
    agents = {agent for _, agent in requests}
    ua_ok = agents == {expected_ua}
    checks = [(f"UA bájtra = aaa UA ({expected_ua!r})", ua_ok)]
    if args.ngx:
        outside = sorted(p for p in folder_paths
                         if not p.startswith("/a/") and p not in NOT_PAGES)
        checks += [
            ("mappán belüli oldal crawlolva (/a/sub/)", "/a/sub/" in folder_paths),
            (f"mappán kívüli oldal nincs lekérve (kívül: {outside})", not outside),
        ]
    else:
        checks += [
            ("JS-render (/js-only/)", "/js-only/" in root_paths),
            ("robots.txt tisztelve (/private/x nincs lekérve)", "/private/x" not in root_paths),
            ("nofollow belső link követve (/nf/)", "/nf/" in root_paths),
            ("sitemap: /sitemap.xml lekérve, /orphan/ crawlolva",
             "/sitemap.xml" in root_paths and "/orphan/" in root_paths),
            ("hreflang-cél crawlolva (/de/)", "/de/" in root_paths),
            ("canonical-cél crawlolva (/canon/)", "/canon/" in root_paths),
            ("a kezdő mappán kívülre is megy (/a/-ból a /b/)", "/b/" in folder_paths),
        ]
    if not ua_ok:
        checks.append((f"kapott UA: {sorted(agents)!r}", False))
    print(f"SF kilépési kódok: {root_exit}, {folder_exit}")
    for label, ok in checks:
        print(f"  {'rendben' if ok else 'ELTÉR '}  {label}")
    return 0 if root_exit == 0 and folder_exit == 0 and all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
