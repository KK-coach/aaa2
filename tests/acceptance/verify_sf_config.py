"""Egy SF-konfiguráció ellenőrzése helyi próba-site-on, az `aaa crawl` viselkedéséhez mérve.

    python -m tests.acceptance.verify_sf_config tests/acceptance/aaa2-acceptance.seospiderconfig
    python -m tests.acceptance.verify_sf_config tests/acceptance/aaa2-acceptance-ngx.seospiderconfig --ngx

A próba-site minden kérést naplóz (út, User-Agent). Az SF kétszer crawlol: a gyökérből és a
`/a/` mappából. Pontok:

- a UA egyezik az aaa UA-jával (Playwright Chromium);
- JS-render: a JS-sel beszúrt link célja (`/js-only/`) crawlolva;
- robots.txt tisztelve: a tiltott `/private/x` nincs lekérve;
- nofollow belső link követve: `/nf/` lekérve;
- sitemap a robots.txt-ből: a csak sitemapben szereplő `/orphan/` crawlolva;
- hreflang- és canonical-cél crawlolva: `/de/`, `/canon/`;
- a kezdő mappán kívülre is megy: a `/a/`-ból indítva a `/b/` crawlolva.

`--ngx`: az include-os konfiguráció ellenőrzése; a próba-site egyik URL-je sem illeszkedik a
`/ngx-bootstrap/` include-ra, így a seeden kívül semmit nem szabad crawlolnia.
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
    "/a/": ('<link rel="canonical" href="/canon/">', '<a href="/">home</a> <a href="/b/">B</a>'),
    "/b/": ("", '<a href="/">home</a>'),
    "/nf/": ("", "nofollow cél"),
    "/private/x": ("", "tiltott"),
    "/js-only/": ("", "js"),
    "/orphan/": ("", "csak sitemap"),
    "/de/": ("", "hreflang-cél"),
    "/canon/": ("", "canonical-cél"),
}


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("config", type=Path)
    parser.add_argument("--ngx", action="store_true", help="az include-os konfiguráció")
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
        paths_root = {path for path, _ in requests}
        requests_root = len(requests)
        folder_exit = crawl(sf_cli, args.config, f"{base}/a/", Path(tmp) / "folder")
        paths_folder = {path for path, _ in requests[requests_root:]}
    server.shutdown()
    agents = sorted({agent for path, agent in requests if path != "/robots.txt"})
    pages = {path for path in paths_root if path in PAGES}
    if args.ngx:
        checks = [("a seeden kívül semmit nem crawlol (include)", pages <= {"/"})]
    else:
        checks = [
            ("JS-render (/js-only/)", "/js-only/" in paths_root),
            ("robots.txt tisztelve (/private/x nincs lekérve)", "/private/x" not in paths_root),
            ("nofollow belső link követve (/nf/)", "/nf/" in paths_root),
            ("sitemap a robots.txt-ből (/orphan/)", "/orphan/" in paths_root),
            ("hreflang-cél crawlolva (/de/)", "/de/" in paths_root),
            ("canonical-cél crawlolva (/canon/)", "/canon/" in paths_root),
            ("a kezdő mappán kívülre is megy (/a/-ból a /b/)", "/b/" in paths_folder),
        ]
    checks.insert(0, (f"UA = aaa UA ({expected_ua})", agents == [expected_ua]))
    print(f"SF kilépési kódok: {root_exit}, {folder_exit}; kapott UA: {agents}")
    for label, ok in checks:
        print(f"  {'rendben' if ok else 'ELTÉR '}  {label}")
    return 0 if root_exit == 0 and folder_exit == 0 and all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
