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
from datetime import UTC, datetime
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
_URL_BLOCK = re.compile(r"<url(?:\s[^>]*)?>(.*?)</url>", re.IGNORECASE | re.DOTALL)
_LASTMOD = re.compile(r"<lastmod(?:\s[^>]*)?>\s*(.*?)\s*</lastmod>", re.IGNORECASE | re.DOTALL)
# alapútvonalak, ha a robots.txt nem hivatkoz sitemapet; az első, amelyik sitemapet ad
DEFAULT_SITEMAPS = ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")
SITEMAP_SOURCES = ("given", "robots", "default")


@dataclass(frozen=True)
class Robots:
    """A robots.txt `*` user-agent csoportja és a Sitemap-sorai, RFC 9309 szerinti illesztéssel."""

    rules: tuple[tuple[re.Pattern[str], int, bool], ...] = ()
    sitemaps: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()       # a szabályok szövege (`Disallow: /x`), a `rules` sorrendjében

    @classmethod
    def parse(cls, text: str) -> Robots:
        """Az összes `User-agent: *` csoport szabálya együtt; a többi csoport nem számít."""
        rules: list[tuple[re.Pattern[str], int, bool]] = []
        sources: list[str] = []
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
                    sources.append(f"{key.capitalize()}: {value}")
        return cls(tuple(rules), tuple(sitemaps), tuple(sources))

    def _deciding(self, url: str) -> tuple[int | None, bool]:
        """(a döntő szabály sorszáma vagy None, szabad-e): a leghosszabb illeszkedő szabály
        dönt, egyenlő hossznál az Allow; szabály nélkül szabad."""
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best, best_length, best_allow = None, -1, True
        for index, (pattern, length, allow) in enumerate(self.rules):
            if pattern.match(target) and (length > best_length or (length == best_length and allow)):
                best, best_length, best_allow = index, length, allow
        return best, best_allow

    def allowed(self, url: str) -> bool:
        """A leghosszabb illeszkedő szabály dönt, egyenlő hossznál az Allow; szabály nélkül szabad."""
        return self._deciding(url)[1]

    def blocking_rule(self, url: str) -> str | None:
        """A címet tiltó szabály szövege (`Disallow: /x`); None, ha a cím szabad, vagy a
        szabály szövege nem ismert."""
        index, allow = self._deciding(url)
        if allow or index is None or index >= len(self.sources):
            return None
        return self.sources[index]


@dataclass(frozen=True)
class SitemapUrl:
    """Egy cím a sitemapből: a nyers cím, a `lastmod` (ahogy a fájlban áll) és a fájl."""

    raw_url: str
    lastmod: str | None = None
    sitemap_file: str | None = None


@dataclass(frozen=True)
class SitemapFile:
    """Egy lekért sitemap-fájl: megvan-e (200-as válasz címmel vagy sitemap indexszel), index-e,
    és hány címet adott (az ismétlődőkkel együtt)."""

    url: str
    found: bool
    is_index: bool = False
    urls: int = 0


@dataclass(frozen=True)
class Discovery:
    """A crawl előtti hálózati felmérés: átirányítás, robots.txt, sitemap-URL-ek. A sitemap
    részletei: honnan került elő (`sitemap_source`: given, robots, default), a címek a
    `lastmod`-dal és a fájljukkal (`sitemap_entries`), a lekért fájlok (`sitemap_files`) és a
    lekérés ideje."""

    https_redirect: bool = False
    https_redirect_status: int | None = None
    robots: Robots | None = None
    sitemap_urls: tuple[str, ...] = ()
    robots_status: int | None = None
    robots_txt: str | None = None
    sitemap_source: str | None = None
    sitemap_entries: tuple[SitemapUrl, ...] = ()
    sitemap_files: tuple[SitemapFile, ...] = ()
    sitemap_fetched_at: datetime | None = None


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
    Sitemap-soraiból, különben az alapútvonalakról jön (`DEFAULT_SITEMAPS`: az első, amelyik
    sitemapet ad). A robots.txt Sitemap-sorait `respect_robots=False` mellett is felhasználja."""
    https_redirect, https_redirect_status = await probe_https_redirect(client, seed_url)
    robots_status, robots_txt = await fetch_robots_file(client, seed_url)
    robots = Robots.parse(robots_txt) if robots_txt is not None else None
    source, entries, files = await find_sitemap(
        client, seed_url, sitemap=sitemap, robots_sitemaps=robots.sitemaps if robots else (),
        max_urls=max_urls)
    return Discovery(
        https_redirect=https_redirect,
        https_redirect_status=https_redirect_status,
        robots=robots if respect_robots else None,
        sitemap_urls=tuple(dict.fromkeys(entry.raw_url for entry in entries)),
        robots_status=robots_status,
        robots_txt=robots_txt,
        sitemap_source=source,
        sitemap_entries=tuple(entries),
        sitemap_files=tuple(files),
        sitemap_fetched_at=_now(),
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def find_sitemap(
    client: httpx.AsyncClient,
    seed_url: str,
    *,
    sitemap: str | None = None,
    robots_sitemaps: Sequence[str] = (),
    max_urls: int = MAX_SITEMAP_URLS,
) -> tuple[str, list[SitemapUrl], list[SitemapFile]]:
    """(forrás, címek, lekért fájlok). Forrás: `given` (megadott cím), `robots` (a robots.txt
    Sitemap-sorai), `default` (alapútvonal: sorban próbálva, az első, amelyik sitemapet ad)."""
    if sitemap:
        source, attempts = "given", [[sitemap]]
    elif robots_sitemaps:
        source, attempts = "robots", [list(robots_sitemaps)]
    else:
        source, attempts = "default", [[urljoin(seed_url, path)] for path in DEFAULT_SITEMAPS]
    entries: list[SitemapUrl] = []
    files: list[SitemapFile] = []
    for roots in attempts:
        entries, found = await read_sitemap_files(client, roots, max_urls=max_urls)
        files += found
        if any(item.found for item in found):
            break
    return source, entries, files


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
    entries, _ = await read_sitemap_files(client, roots, max_urls=max_urls, max_files=max_files)
    return list(dict.fromkeys(entry.raw_url for entry in entries))


async def read_sitemap_files(
    client: httpx.AsyncClient,
    roots: Sequence[str],
    *,
    max_urls: int = MAX_SITEMAP_URLS,
    max_files: int = MAX_SITEMAP_FILES,
) -> tuple[list[SitemapUrl], list[SitemapFile]]:
    """A sitemapok címei (a `lastmod`-dal és a fájljukkal; az ismétlődő cím minden előfordulása
    külön tétel, a `max_urls` a különböző címekre vonatkozik) és a lekért fájlok, sitemap indexet
    kibontva, gzipet kicsomagolva."""
    pending = list(roots)
    seen_files: set[str] = set()
    entries: list[SitemapUrl] = []
    files: list[SitemapFile] = []
    seen_urls: set[str] = set()
    while pending and len(seen_files) < max_files and len(seen_urls) < max_urls:
        sitemap_url = pending.pop(0)
        if sitemap_url in seen_files:
            continue
        seen_files.add(sitemap_url)
        body = await _fetch(client, sitemap_url)
        if body is None:
            files.append(SitemapFile(sitemap_url, False))
            continue
        text = body.decode("utf-8", "replace")
        locs = [html.unescape(loc) for loc in _LOC.findall(text)]
        if _SITEMAP_INDEX.search(text):
            pending.extend(urljoin(sitemap_url, loc) for loc in locs)
            files.append(SitemapFile(sitemap_url, True, True))
            continue
        lastmods: dict[str, str] = {}
        for block in _URL_BLOCK.findall(text):
            loc, lastmod = _LOC.search(block), _LASTMOD.search(block)
            if loc and lastmod and lastmod.group(1):
                lastmods.setdefault(html.unescape(loc.group(1)), lastmod.group(1))
        added = 0
        for loc in locs:
            if not loc:
                continue
            if loc not in seen_urls:
                if len(seen_urls) >= max_urls:
                    break
                seen_urls.add(loc)
            entries.append(SitemapUrl(loc, lastmods.get(loc), sitemap_url))
            added += 1
        files.append(SitemapFile(sitemap_url, bool(locs), False, added))
    return entries, files


def store_sitemap(
    con: duckdb.DuckDBPyConnection,
    policy: UrlPolicy,
    source: str,
    entries: Sequence[SitemapUrl],
    files: Sequence[SitemapFile],
    *,
    snapshot: str,
    fetched_at: datetime | None,
) -> None:
    """A sitemap tárolása (`sitemap_files`, `sitemap_urls`): a `snapshot` korábbi sorai helyére.
    A címek a crawl címeivel azonos normalizálással kerülnek a `url` oszlopba."""
    con.execute("DELETE FROM sitemap_files WHERE snapshot = ?", [snapshot])
    con.execute("DELETE FROM sitemap_urls WHERE snapshot = ?", [snapshot])
    for ordinal, item in enumerate(files):
        con.execute(
            "INSERT INTO sitemap_files (snapshot, ordinal, url, source, found, is_index, urls, "
            "fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [snapshot, ordinal, item.url, source, item.found, item.is_index, item.urls,
             fetched_at])
    for ordinal, entry in enumerate(entries):
        con.execute(
            "INSERT INTO sitemap_urls (snapshot, ordinal, raw_url, url, internal, lastmod, "
            "sitemap_file, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [snapshot, ordinal, entry.raw_url, normalize(entry.raw_url, policy),
             is_internal(entry.raw_url, policy), entry.lastmod, entry.sitemap_file, source,
             fetched_at])


def stored_policy(con: duckdb.DuckDBPyConnection) -> UrlPolicy | None:
    """A tárolt crawl URL-szabályai a `site` táblából (seed, https-átirányítás, záró perjel)."""
    row = con.execute("SELECT seed_url, https_redirect, trailing_slash FROM site").fetchone()
    if row is None:
        return None
    seed_url, https_redirect, trailing_slash = row
    policy = UrlPolicy.from_seed(seed_url, https_redirect=bool(https_redirect))
    return replace(policy, trailing_slash=trailing_slash)


def restore_sitemap_from_queue(con: duckdb.DuckDBPyConnection) -> int:
    """A korábbi crawl sitemap-címei a crawl-sorból (`crawl_queue.priority` = sitemap), ha a
    crawl idejéről nincs tárolt sitemap: a `crawl` pillanatkép `queue` forrással, nyers cím,
    fájl és lastmod nélkül. Visszaad: hány cím került be (0, ha már volt tárolt sitemap)."""
    (stored,) = con.execute(
        "SELECT (SELECT count(*) FROM sitemap_urls WHERE snapshot = 'crawl') + (SELECT count(*) "
        "FROM sitemap_files WHERE snapshot = 'crawl')").fetchone()
    if stored:
        return 0
    started = con.execute("SELECT min(started_at) FROM crawl_runs").fetchone()[0]
    urls = [url for (url,) in con.execute(
        "SELECT url FROM crawl_queue WHERE priority = ? ORDER BY url",
        [PRIORITY["sitemap"]]).fetchall()]
    for ordinal, url in enumerate(urls):
        con.execute(
            "INSERT INTO sitemap_urls (snapshot, ordinal, raw_url, url, internal, lastmod, "
            "sitemap_file, source, fetched_at) VALUES ('crawl', ?, NULL, ?, true, NULL, NULL, "
            "'queue', ?)", [ordinal, url, started])
    return len(urls)


def record_missing_mode(con: duckdb.DuckDBPyConnection, sitemap_only: bool,
                        include: str | None = None, exclude: str | None = None) -> int:
    """A mód és a hatókör pótlása azokon a futásokon, amelyek még nem rögzítették
    (`crawl_runs.mode`, ill. `include` és `exclude` üres): `sitemap`, ha a crawl sitemap-módban
    futott, különben `links`; a hatókör a megadott minták ('' ha nincs). Visszaad: hány futáson
    került be a mód."""
    mode = "sitemap" if sitemap_only else "links"
    con.execute("UPDATE crawl_runs SET include = ?, exclude = ? WHERE include IS NULL AND "
                "exclude IS NULL", [include or "", exclude or ""])
    return len(con.execute("UPDATE crawl_runs SET mode = ? WHERE mode IS NULL RETURNING run_id",
                           [mode]).fetchall())


async def refetch_sitemap(con: duckdb.DuckDBPyConnection, client: httpx.AsyncClient,
                          sitemap: str | None = None) -> tuple[str, int, int]:
    """A sitemap utólagos lekérése egy tárolt crawlhoz (`refetch` pillanatkép): csak a
    sitemap-fájlokat kéri le, oldalt nem. A forrás a tárolt robots.txt Sitemap-soraiból, különben
    az alapútvonalakról jön. Visszaad: (forrás, fájlok száma, címek száma)."""
    policy = stored_policy(con)
    if policy is None:
        raise ValueError("nincs tárolt crawl: a site tábla üres")
    seed_url, robots_txt = con.execute("SELECT seed_url, robots_txt FROM site").fetchone()
    robots = Robots.parse(robots_txt) if robots_txt is not None else None
    source, entries, files = await find_sitemap(
        client, seed_url, sitemap=sitemap, robots_sitemaps=robots.sitemaps if robots else ())
    store_sitemap(con, policy, source, entries, files, snapshot="refetch", fetched_at=_now())
    return source, len(files), len(entries)


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
        follow_links: bool = True,
    ) -> None:
        self.con = con
        self.policy = policy
        self.robots = robots
        self.max_pages = max_pages
        self.follow_links = follow_links
        self._include = re.compile(include) if include else None
        self._exclude = re.compile(exclude) if exclude else None
        self._known = {url for (url,) in con.execute("SELECT url FROM crawl_queue").fetchall()}
        self.limit_skipped: set[str] = set()   # a `max_pages` miatt a sorból kimaradt címek
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
        follow_links: bool = True,
    ) -> Frontier:
        """Új crawl. A sor kiürül, a site-döntések a `site` táblába kerülnek, a sorba a seed,
        a sitemap-URL-ek és a seed linkjei. `seed_links`: (abszolút URL, pozíció) a seed
        renderelt oldaláról, DOM-sorrendben. `follow_links=False` (sitemap-mód): a sorba csak a
        seed és a sitemap-URL-ek kerülnek, az oldalak linkjei nem."""
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
                include=include, exclude=exclude, follow_links=follow_links,
            )
            frontier._insert(seed, depth=0, priority=PRIORITY["seed"], discovered_from=None)
            con.execute("DELETE FROM sitemap_files")
            con.execute("DELETE FROM sitemap_urls")
            if discovery.sitemap_source is not None:
                store_sitemap(con, policy, discovery.sitemap_source, discovery.sitemap_entries,
                              discovery.sitemap_files, snapshot="crawl",
                              fetched_at=discovery.sitemap_fetched_at)
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
        follow_links: bool = True,
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
            con, policy, robots=robots, max_pages=max_pages, include=include, exclude=exclude,
            follow_links=follow_links,
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
            self.limit_skipped.add(target)
            return False
        self._insert(target, depth=depth, priority=priority, discovered_from=discovered_from)
        return True

    def add_links(self, source: QueueItem, links: Iterable[tuple[str, str]]) -> int:
        """Egy oldal linkjei (abszolút URL, pozíció) a sorba; a pozíció a prioritást adja.
        Sitemap-módban (`follow_links=False`) a linkek nem kerülnek a sorba."""
        if not self.follow_links:
            return 0
        return sum(
            self.add(
                url,
                depth=source.depth + 1,
                priority=PRIORITY.get(position, PRIORITY["body"]),
                discovered_from=source.url,
            )
            for url, position in links
        )

    def expected_pages(self, sitemap_urls: Iterable[str]) -> int | None:
        """A sitemap alapján várható oldalszám: a szűrőkön átjutó sitemap-URL-ek és a seed,
        ismétlés nélkül; None, ha a sitemap üres vagy egy URL-je sem jut át."""
        admitted = {target for url in sitemap_urls if (target := self.admit(url)) is not None}
        if not admitted:
            return None
        return len(admitted | {self.seed_url})

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
