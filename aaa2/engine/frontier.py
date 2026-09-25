"""Frontier: a site felmérése a crawl előtt, és a crawl_queue.

Sorrend: seed → sitemap-URL-ek → a talált belső linkek BFS-ben. A sor a DuckDB-ben él, ezért
a crawl megszakítható és `--resume`-mal folytatható. A kiadás sorrendje mélység, prioritás
(seed < sitemap < nav < body < aside < footer), végül URL. A mélység ütemezési szint: a seed 0,
a sitemap-URL-ek és a seed linkjei 1, a többi a felfedező oldal mélysége + 1.

A site-döntések a crawl elején rögzülnek, és a crawl alatt nem változnak:

- `https_redirect`: a seed http-változata 301, 302, 307 vagy 308 átirányítással https-re visz;
  a kapott státusz a `site.https_redirect_status`-ba kerül;
- `trailing_slash`: a seed első 50 különböző belső linkjének domináns formája; ha nincs
  domináns forma, a sitemap összes belső URL-jének formája dönt; ha az sem, None.

A sorba csak belső, normalizált URL kerül, amit az include/exclude regex átenged (az exclude
nyer) és a seed hostjának robots.txt-je nem tilt. A seed ezek alól kivétel.
"""
from __future__ import annotations

import html
import re
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlsplit, urlunsplit

import duckdb
import httpx

from aaa2.engine.normalize import (
    UrlPolicy,
    decide_trailing_slash,
    is_internal,
    normalize,
    normalize_path_encoding,
    registrable_domain,
)

PRIORITY = {"seed": 0, "sitemap": 10, "nav": 20, "body": 30, "aside": 35, "footer": 40}
MAX_PAGES = 5000
MAX_SITEMAP_FILES = 100
MAX_SITEMAP_URLS = 50_000
MAX_SITEMAP_BYTES = 50 * 1024 * 1024

_HTTPS_REDIRECTS = frozenset({301, 302, 307, 308})
_LOC = re.compile(
    r"<loc(?:\s[^>]*)?>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.IGNORECASE | re.DOTALL
)
_SITEMAP_INDEX = re.compile(r"<sitemapindex[\s>]", re.IGNORECASE)


@dataclass(frozen=True)
class Robots:
    """A robots.txt `*` user-agent csoportja és a Sitemap-sorai, RFC 9309 szerinti illesztéssel."""

    rules: tuple[tuple[re.Pattern[str], int, bool], ...] = ()
    sitemaps: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> Robots:
        """Az összes `User-agent: *` csoport szabálya együtt; a többi csoport nem számít."""
        rules: list[tuple[re.Pattern[str], int, bool]] = []
        sitemaps: list[str] = []
        agents: list[str] = []
        in_rules = False
        for raw in text.splitlines():
            key, sep, value = raw.split("#", 1)[0].partition(":")
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == "sitemap":
                if value:
                    sitemaps.append(value)
            elif key == "user-agent":
                if in_rules:
                    agents, in_rules = [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if "*" in agents and value:
                    pattern, length = _robots_pattern(value)
                    rules.append((pattern, length, key == "allow"))
        return cls(tuple(rules), tuple(sitemaps))

    def allowed(self, url: str) -> bool:
        """A leghosszabb illeszkedő szabály dönt, egyenlő hossznál az Allow; szabály nélkül szabad."""
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best_length, best_allow = -1, True
        for pattern, length, allow in self.rules:
            if pattern.match(target) and (length > best_length or (length == best_length and allow)):
                best_length, best_allow = length, allow
        return best_allow


@dataclass(frozen=True)
class Discovery:
    """A crawl előtti hálózati felmérés: átirányítás, robots.txt, sitemap-URL-ek."""

    https_redirect: bool = False
    https_redirect_status: int | None = None
    robots: Robots | None = None
    sitemap_urls: tuple[str, ...] = ()
    robots_status: int | None = None
    robots_txt: str | None = None


@dataclass(frozen=True)
class QueueItem:
    url: str
    depth: int
    priority: int


async def discover(
    client: httpx.AsyncClient,
    seed_url: str,
    *,
    sitemap: str | None = None,
    respect_robots: bool = True,
    max_urls: int = MAX_SITEMAP_URLS,
) -> Discovery:
    """https-próba, robots.txt, sitemap. A sitemap a `sitemap` URL-ből, különben a robots.txt
    Sitemap-soraiból, különben a `/sitemap.xml`-ből jön. A robots.txt Sitemap-sorait
    `respect_robots=False` mellett is felhasználja."""
    https_redirect, https_redirect_status = await probe_https_redirect(client, seed_url)
    robots_status, robots_txt = await fetch_robots_file(client, seed_url)
    robots = Robots.parse(robots_txt) if robots_txt is not None else None
    if sitemap:
        roots = [sitemap]
    elif robots is not None and robots.sitemaps:
        roots = list(robots.sitemaps)
    else:
        roots = [urljoin(seed_url, "/sitemap.xml")]
    urls = await read_sitemaps(client, roots, max_urls=max_urls)
    return Discovery(
        https_redirect=https_redirect,
        https_redirect_status=https_redirect_status,
        robots=robots if respect_robots else None,
        sitemap_urls=tuple(urls),
        robots_status=robots_status,
        robots_txt=robots_txt,
    )


async def probe_https_redirect(
    client: httpx.AsyncClient, seed_url: str
) -> tuple[bool, int | None]:
    """(átirányít-e https-re, a kapott státusz) a seed http-változatára. Átirányítás: 301, 302,
    307 vagy 308, a cél https ugyanazon a registrable domainen. Hálózati hibánál a státusz None."""
    parts = urlsplit(seed_url.strip())
    host = parts.hostname
    if not host:
        return False, None
    probe = urlunsplit(("http", host, parts.path or "/", parts.query, ""))
    try:
        response = await client.get(probe, follow_redirects=False)
    except httpx.HTTPError:
        return False, None
    status = response.status_code
    if status not in _HTTPS_REDIRECTS:
        return False, status
    target = urlsplit(urljoin(probe, response.headers.get("location", "")))
    redirect = (
        target.scheme == "https"
        and target.hostname is not None
        and registrable_domain(target.hostname) == registrable_domain(host)
    )
    return redirect, status


async def fetch_robots(client: httpx.AsyncClient, seed_url: str) -> Robots | None:
    """A seed hostjának robots.txt-je; None, ha nem 200 (4xx, 5xx, hálózati hiba: nincs tiltás)."""
    _, text = await fetch_robots_file(client, seed_url)
    return Robots.parse(text) if text is not None else None


async def fetch_robots_file(
    client: httpx.AsyncClient, seed_url: str
) -> tuple[int | None, str | None]:
    """(státusz, szöveg) a seed hostjának robots.txt-jére, átirányítást követve. A szöveg csak
    200-nál van meg; a státusz None, ha a kérés hálózati hibával elbukott."""
    try:
        response = await client.get(urljoin(seed_url, "/robots.txt"), follow_redirects=True)
    except httpx.HTTPError:
        return None, None
    if response.status_code != 200:
        return response.status_code, None
    return 200, response.content.decode("utf-8", "replace")


async def read_sitemaps(
    client: httpx.AsyncClient,
    roots: Sequence[str],
    *,
    max_urls: int = MAX_SITEMAP_URLS,
    max_files: int = MAX_SITEMAP_FILES,
) -> list[str]:
    """Az oldal-URL-ek a sitemapokból, sitemap indexet kibontva, gzipet kicsomagolva, ismétlés nélkül."""
    pending = list(roots)
    seen_files: set[str] = set()
    urls: list[str] = []
    seen_urls: set[str] = set()
    while pending and len(seen_files) < max_files and len(urls) < max_urls:
        sitemap_url = pending.pop(0)
        if sitemap_url in seen_files:
            continue
        seen_files.add(sitemap_url)
        body = await _fetch(client, sitemap_url)
        if body is None:
            continue
        text = body.decode("utf-8", "replace")
        locs = [html.unescape(loc) for loc in _LOC.findall(text)]
        if _SITEMAP_INDEX.search(text):
            pending.extend(urljoin(sitemap_url, loc) for loc in locs)
            continue
        for loc in locs:
            if len(urls) >= max_urls:
                break
            if loc and loc not in seen_urls:
                seen_urls.add(loc)
                urls.append(loc)
    return urls


class Frontier:
    """A crawl_queue kezelője. Tranzakciót csak a `start` nyit; a többi hívás a hívóéban fut."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        policy: UrlPolicy,
        *,
        robots: Robots | None = None,
        max_pages: int = MAX_PAGES,
        include: str | None = None,
        exclude: str | None = None,
    ) -> None:
        self.con = con
        self.policy = policy
        self.robots = robots
        self.max_pages = max_pages
        self._include = re.compile(include) if include else None
        self._exclude = re.compile(exclude) if exclude else None
        self._known = {url for (url,) in con.execute("SELECT url FROM crawl_queue").fetchall()}
        self._leased: set[str] = set()

    @classmethod
    def start(
        cls,
        con: duckdb.DuckDBPyConnection,
        seed_url: str,
        discovery: Discovery,
        seed_links: Iterable[tuple[str, str]] = (),
        *,
        max_pages: int = MAX_PAGES,
        include: str | None = None,
        exclude: str | None = None,
    ) -> Frontier:
        """Új crawl. A sor kiürül, a site-döntések a `site` táblába kerülnek, a sorba a seed,
        a sitemap-URL-ek és a seed linkjei. `seed_links`: (abszolút URL, pozíció) a seed
        renderelt oldaláról, DOM-sorrendben."""
        links = list(seed_links)
        policy = UrlPolicy.from_seed(seed_url, https_redirect=discovery.https_redirect)
        policy = replace(
            policy,
            trailing_slash=_decide_trailing_slash(
                policy, [url for url, _ in links], discovery.sitemap_urls
            ),
        )
        seed = normalize(seed_url, policy)
        if seed is None:
            raise ValueError(f"nem normalizálható seed URL: {seed_url!r}")

        con.begin()
        try:
            con.execute("DELETE FROM crawl_queue")
            con.execute(
                "INSERT INTO site (domain, seed_url, trailing_slash, https_redirect, "
                "https_redirect_status, robots_status, robots_txt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (domain) DO UPDATE SET "
                "seed_url = excluded.seed_url, trailing_slash = excluded.trailing_slash, "
                "https_redirect = excluded.https_redirect, "
                "https_redirect_status = excluded.https_redirect_status, "
                "robots_status = excluded.robots_status, robots_txt = excluded.robots_txt",
                [
                    policy.domain, seed, policy.trailing_slash, policy.https_redirect,
                    discovery.https_redirect_status, discovery.robots_status,
                    discovery.robots_txt,
                ],
            )
            frontier = cls(
                con, policy, robots=discovery.robots, max_pages=max_pages,
                include=include, exclude=exclude,
            )
            frontier._insert(seed, depth=0, priority=PRIORITY["seed"], discovered_from=None)
            for url in discovery.sitemap_urls:
                frontier.add(url, depth=1, priority=PRIORITY["sitemap"])
            frontier.add_links(QueueItem(seed, 0, PRIORITY["seed"]), links)
            con.commit()
        except BaseException:
            con.rollback()
            raise
        return frontier

    @classmethod
    def resume(
        cls,
        con: duckdb.DuckDBPyConnection,
        *,
        robots: Robots | None = None,
        max_pages: int = MAX_PAGES,
        include: str | None = None,
        exclude: str | None = None,
    ) -> Frontier:
        """Folytatás a DB-ben lévő sorból, a `site` táblában rögzített döntésekkel."""
        row = con.execute("SELECT seed_url, https_redirect, trailing_slash FROM site").fetchone()
        if row is None:
            raise ValueError("nincs folytatható crawl: a site tábla üres")
        (queued,) = con.execute(
            "SELECT count(*) FROM crawl_queue WHERE status = 'queued'"
        ).fetchone()
        if not queued:
            raise ValueError("nincs folytatható crawl: nincs várakozó URL a sorban")
        seed_url, https_redirect, trailing_slash = row
        policy = UrlPolicy.from_seed(
            seed_url, https_redirect=bool(https_redirect), trailing_slash=trailing_slash
        )
        return cls(
            con, policy, robots=robots, max_pages=max_pages, include=include, exclude=exclude
        )

    @property
    def seed_url(self) -> str:
        (url,) = self.con.execute("SELECT seed_url FROM site").fetchone()
        return url

    def admit(self, url: str) -> str | None:
        """A sorba kerülő alak; None, ha külső, a szűrő kizárja, vagy a robots.txt tiltja."""
        if not is_internal(url, self.policy):
            return None
        target = normalize(url, self.policy)
        if target is None:
            return None
        if self._exclude and self._exclude.search(target):
            return None
        if self._include and not self._include.search(target):
            return None
        if (
            self.robots is not None
            and urlsplit(target).hostname == self.policy.seed_host
            and not self.robots.allowed(target)
        ):
            return None
        return target

    def add(
        self, url: str, *, depth: int, priority: int, discovered_from: str | None = None
    ) -> bool:
        """Új URL a sorba, ha átjut a szűrőkön és van még hely a `max_pages` alatt. Ismert,
        még várakozó URL-nél a kisebb mélység és prioritás marad. True, ha új sor keletkezett."""
        target = self.admit(url)
        if target is None:
            return False
        if target in self._known:
            self.con.execute(
                "UPDATE crawl_queue SET depth = least(depth, ?), priority = least(priority, ?), "
                "updated_at = current_timestamp "
                "WHERE url = ? AND status = 'queued' AND (depth > ? OR priority > ?)",
                [depth, priority, target, depth, priority],
            )
            return False
        if len(self._known) >= self.max_pages:
            return False
        self._insert(target, depth=depth, priority=priority, discovered_from=discovered_from)
        return True

    def add_links(self, source: QueueItem, links: Iterable[tuple[str, str]]) -> int:
        """Egy oldal linkjei (abszolút URL, pozíció) a sorba; a pozíció a prioritást adja."""
        return sum(
            self.add(
                url,
                depth=source.depth + 1,
                priority=PRIORITY.get(position, PRIORITY["body"]),
                discovered_from=source.url,
            )
            for url, position in links
        )

    def next_batch(self, n: int) -> list[QueueItem]:
        """Legfeljebb n várakozó URL, amit még nem adott ki. Egy kiadott URL a
        `mark_done` / `mark_failed` hívásig foglalt; új Frontier-példány újra kiadja."""
        rows = self.con.execute(
            "SELECT url, depth, priority FROM crawl_queue WHERE status = 'queued' "
            "ORDER BY depth, priority, url LIMIT ?",
            [n + len(self._leased)],
        ).fetchall()
        batch = [QueueItem(*row) for row in rows if row[0] not in self._leased][:n]
        self._leased.update(item.url for item in batch)
        return batch

    def mark_done(self, url: str) -> None:
        self._finish(url, "done", None)

    def claim(self, url: str, *, depth: int, discovered_from: str | None = None) -> str | None:
        """Egy már renderelt URL (egy átirányítás célja) sorát a hívó veszi át és `done`-ra
        állítja; ha nincs a sorban, a `max_pages` alól kivételként kerül be. Visszaadja a sor
        URL-jét, ha a hívónak kell megírnia az oldalt; None, ha a szűrők nem engedik, a sor már
        kész vagy hibás, vagy épp egy másik worker rendereli."""
        target = self.admit(url)
        if target is None or target in self._leased:
            return None
        if target in self._known:
            (status,) = self.con.execute(
                "SELECT status FROM crawl_queue WHERE url = ?", [target]
            ).fetchone()
            if status != "queued":
                return None
        else:
            self._insert(target, depth=depth, priority=PRIORITY["body"],
                         discovered_from=discovered_from)
        self.mark_done(target)
        return target

    def mark_failed(self, url: str, error: str) -> None:
        self._finish(url, "failed", error)

    def _finish(self, url: str, status: str, error: str | None) -> None:
        self.con.execute(
            "UPDATE crawl_queue SET status = ?, error = ?, updated_at = current_timestamp "
            "WHERE url = ?",
            [status, error, url],
        )
        self._leased.discard(url)

    def _insert(
        self, url: str, *, depth: int, priority: int, discovered_from: str | None
    ) -> None:
        self.con.execute(
            "INSERT INTO crawl_queue (url, depth, priority, status, discovered_from, updated_at) "
            "VALUES (?, ?, ?, 'queued', ?, current_timestamp)",
            [url, depth, priority, discovered_from],
        )
        self._known.add(url)


def _decide_trailing_slash(
    policy: UrlPolicy, link_urls: Sequence[str], sitemap_urls: Sequence[str]
) -> bool | None:
    found = list(dict.fromkeys(
        target
        for url in link_urls
        if is_internal(url, policy) and (target := normalize(url, policy)) is not None
    ))
    decision = decide_trailing_slash(found)
    if decision is None:
        internal = [url for url in sitemap_urls if is_internal(url, policy)]
        decision = decide_trailing_slash(internal, sample=len(internal))
    return decision


def _robots_pattern(value: str) -> tuple[re.Pattern[str], int]:
    path, sep, query = value.partition("?")
    value = normalize_path_encoding(path) + sep + query
    anchored = value.endswith("$")
    body = value[:-1] if anchored else value
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(regex + (r"\Z" if anchored else "")), len(value)


async def _fetch(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        response = await client.get(url, follow_redirects=True)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    body = response.content
    if body[:2] == b"\x1f\x8b":
        try:
            body = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, MAX_SITEMAP_BYTES)
        except zlib.error:
            return None
    return body
