"""Crawl: a frontier, a render és a parse összefűzése egy site DuckDB-jébe.

Új crawl:

1. felmérés (`discover`): https-próba, robots.txt, sitemap;
2. a seed renderelése; a linkjeiből dől el a trailing slash (`Frontier.start`);
3. `concurrency` worker dolgozza fel a sort: hash-próba, render, parse, írás;
4. a végén a linkek `to_page_id`-je, a site-profil (`site_profile.py`: célország, piaci
   hatókör, nyelvek, `page_count`, nyers tech-jelek) és a `crawl_runs` sor lezárása.

`--resume`: a `site` táblában rögzített szabályokkal a várakozó sorokból folytat.

Oldalanként egy tranzakció: a `pages` sor, a `links`, `headings`, `schema_blocks` sorai és a
sor állapota (`crawl_queue`, a talált linkek felvétele) együtt, vagy sehogy. A tranzakción
belül nincs `await`, így egy megszakítás nem hagy félkész oldalt.

Hash-alapú skip: ha az URL-nek van `pages` sora 7 napnál frissebb `fetched_at`-tel, és a nyers
GET válaszának stabil hash-e (`stable_hash`: sha256 a kérésenként változó tokenek nélkül)
egyezik a tárolt `raw_html_hash`-sel, a render kimarad és a sor érintetlen; a tárolt linkjei
kerülnek a sorba. A seedre is: ha változatlan, a trailing-slash döntés a tárolt DOM linkjeiből
jön, render nélkül.

Átirányítás: a render követi. Ha a `final_url` normalizált alakja egy másik belső URL, a kért
URL sora átirányítás-sor (az első lépés státusza, `final_url`, tartalom nélkül), a tartalom a
cél sorába kerül, és a cél sora `done`. Ha a cél nem belső vagy a szűrők nem engedik, csak az
átirányítás-sor marad.

Normalizálás okozta 404: ha az URL 404-et ad, de a trailing slash másik alakja 2xx, az
`error` "normalized form 404, original served".

Tartalom (parse, linkek, headingek, JSON-LD) csak hibátlan, 400 alatti válaszból kerül tárolásra;
a hibás oldal sora státusszal és `error`-ral áll. Hibás (`failed`) a sorban az URL, amelyre nem
jött HTTP-válasz; a `crawl_runs.pages_failed` minden hibás vagy 400 feletti oldalt számol.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import duckdb
import httpx
import zstandard

from aaa2.db.connect import connect, db_path
from aaa2.engine.frontier import (
    MAX_PAGES,
    PRIORITY,
    Frontier,
    QueueItem,
    discover,
    fetch_robots,
)
from aaa2.engine.normalize import UrlPolicy, is_internal, normalize, slash_alternate
from aaa2.engine.parse import ParsedPage, parse_page
from aaa2.engine.render import (
    CONCURRENCY,
    NAVIGATION_HEADERS,
    RENDER_TIMEOUT,
    Renderer,
    RenderResult,
)
from aaa2.engine.site_profile import update_site_profile
from aaa2.engine.stable_hash import decode_raw, stable_hash

SKIP_MAX_AGE = timedelta(days=7)
NORMALIZED_404 = "normalized form 404, original served"
HTTP_TIMEOUT = 20.0

_PAGE_COLUMNS = (
    "url", "status", "error", "canonical", "noindex", "title", "meta_description", "h1", "lang",
    "hreflang", "word_count", "main_content", "main_content_method", "external_link_count",
    "raw_html_hash", "rendered_html", "render_ms", "fetched_at", "run_id", "final_url",
)
_UPSERT_PAGE = (
    f"INSERT INTO pages ({', '.join(_PAGE_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _PAGE_COLUMNS)}) ON CONFLICT (url) DO UPDATE SET "
    + ", ".join(f"{column} = excluded.{column}" for column in _PAGE_COLUMNS[1:])
    + " RETURNING page_id"
)


@dataclass(frozen=True)
class CrawlOptions:
    sitemap: str | None = None
    max_pages: int = MAX_PAGES
    concurrency: int = CONCURRENCY
    render_timeout: float = RENDER_TIMEOUT
    respect_robots: bool = True
    resume: bool = False
    include: str | None = None
    exclude: str | None = None


@dataclass(frozen=True)
class CrawlSummary:
    run_id: int
    pages_done: int
    pages_failed: int
    pages_skipped: int
    pages_per_sec: float
    bytes_stored: int
    seconds: float


Progress = Callable[[str, int | None, str | None], None]


async def run_crawl(
    seed_url: str, options: CrawlOptions, *, progress: Progress | None = None
) -> tuple[CrawlSummary, str]:
    """A CLI belépési pontja: `data/<domain>.duckdb`, saját böngésző és HTTP-kliens."""
    path = db_path(UrlPolicy.from_seed(seed_url).domain)
    con = connect(path)
    try:
        async with Renderer(
            concurrency=options.concurrency, render_timeout=options.render_timeout
        ) as renderer, httpx.AsyncClient(
            headers={"User-Agent": renderer.user_agent, **NAVIGATION_HEADERS}, timeout=HTTP_TIMEOUT
        ) as client:
            summary = await crawl(
                con, seed_url, options, client=client, renderer=renderer, progress=progress
            )
    finally:
        con.close()
    return summary, str(path)


async def crawl(
    con: duckdb.DuckDBPyConnection,
    seed_url: str,
    options: CrawlOptions,
    *,
    client: httpx.AsyncClient,
    renderer: Renderer,
    progress: Progress | None = None,
) -> CrawlSummary:
    """Egy site crawlja a `con` adatbázisba. A `progress` minden megírt oldal után hívódik
    (URL, státusz, hiba)."""
    run = _Run(con, client, renderer, options, progress)
    return await run.execute(seed_url)


class _Run:
    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        client: httpx.AsyncClient,
        renderer: Renderer,
        options: CrawlOptions,
        progress: Progress | None,
    ) -> None:
        self.con = con
        self.client = client
        self.renderer = renderer
        self.options = options
        self.progress = progress
        self.frontier: Frontier | None = None
        self.run_id = 0
        self.done = self.failed = self.skipped = self.bytes_stored = 0
        self._compressor = zstandard.ZstdCompressor()

    async def execute(self, seed_url: str) -> CrawlSummary:
        started = _now()
        clock = asyncio.get_running_loop().time()
        mode = "resume" if self.options.resume else "új"
        if self.options.resume:
            await self._resume()
        (self.run_id,) = self.con.execute(
            "INSERT INTO crawl_runs (started_at, max_pages, concurrency, notes) "
            "VALUES (?, ?, ?, ?) RETURNING run_id",
            [started, self.options.max_pages, self.options.concurrency, f"{mode}: {seed_url}"],
        ).fetchone()
        finished = False
        try:
            if not self.options.resume:
                await self._start(seed_url, started)
            await self._drain()
            self._finish()
            finished = True
        finally:
            seconds = asyncio.get_running_loop().time() - clock
            summary = self._close_run(seconds, finished)
        return summary

    async def _start(self, seed_url: str, started: datetime) -> None:
        options = self.options
        discovery = await discover(
            self.client, seed_url, sitemap=options.sitemap, respect_robots=options.respect_robots
        )
        provisional = UrlPolicy.from_seed(seed_url, https_redirect=discovery.https_redirect)
        seed = normalize(seed_url, provisional)
        if seed is None:
            raise ValueError(f"nem normalizálható seed URL: {seed_url!r}")
        candidates = [url for url in (seed, slash_alternate(seed)) if url]
        stored = self._fresh_row(candidates)
        if stored is not None and stored[3] is not None and await self._unchanged(seed, stored[1]):
            await self._start_from_stored(seed_url, discovery, provisional, stored, started)
            return
        result = await self.renderer.render(seed)
        seed_links: list[tuple[str, str]] = []
        if result.rendered_html and result.error is None:
            parsed = parse_page(
                result.rendered_html, result.final_url or seed, provisional, result.headers
            )
            seed_links = [(link.to_url, link.position) for link in parsed.links]
        self.frontier = Frontier.start(
            self.con, seed_url, discovery, seed_links, max_pages=options.max_pages,
            include=options.include, exclude=options.exclude,
        )
        self.con.execute("UPDATE site SET crawled_at = ?", [started])
        (item,) = self.frontier.next_batch(1)
        error = await self._normalization_404(item.url, result)
        self._store(item, result, error)

    async def _start_from_stored(
        self, seed_url: str, discovery, provisional: UrlPolicy, stored: tuple, started: datetime
    ) -> None:
        """A seed változatlan: a trailing-slash döntés a tárolt DOM linkjeiből jön, render nélkül,
        a sor a tárolt linkekkel indul."""
        page_id, _, url, compressed, final_url = stored
        html = zstandard.ZstdDecompressor().decompress(compressed).decode("utf-8")
        parsed = parse_page(html, final_url or url, provisional)
        options = self.options
        self.frontier = Frontier.start(
            self.con, seed_url, discovery, [(link.to_url, link.position) for link in parsed.links],
            max_pages=options.max_pages, include=options.include, exclude=options.exclude,
        )
        self.con.execute("UPDATE site SET crawled_at = ?", [started])
        (item,) = self.frontier.next_batch(1)
        self._skip(item, page_id)

    async def _resume(self) -> None:
        row = self.con.execute("SELECT seed_url FROM site").fetchone()
        if row is None:
            raise ValueError("nincs folytatható crawl: a site tábla üres")
        robots = await fetch_robots(self.client, row[0]) if self.options.respect_robots else None
        self.frontier = Frontier.resume(
            self.con, robots=robots, max_pages=self.options.max_pages,
            include=self.options.include, exclude=self.options.exclude,
        )

    async def _drain(self) -> None:
        condition = asyncio.Condition()
        in_flight = 0

        async def worker() -> None:
            nonlocal in_flight
            while True:
                async with condition:
                    while not (batch := self.frontier.next_batch(1)):
                        if in_flight == 0:
                            condition.notify_all()
                            return
                        await condition.wait()
                    in_flight += 1
                try:
                    await self._process(batch[0])
                finally:
                    async with condition:
                        in_flight -= 1
                        condition.notify_all()

        try:
            async with asyncio.TaskGroup() as group:
                for _ in range(max(1, self.options.concurrency)):
                    group.create_task(worker())
        except ExceptionGroup as errors:
            raise errors.exceptions[0] from errors

    async def _process(self, item: QueueItem) -> None:
        if await self._try_skip(item):
            return
        result = await self.renderer.render(item.url)
        error = await self._normalization_404(item.url, result)
        self._store(item, result, error)

    async def _try_skip(self, item: QueueItem) -> bool:
        stored = self._fresh_row([item.url])
        if stored is None or not await self._unchanged(item.url, stored[1]):
            return False
        self._skip(item, stored[0])
        return True

    def _fresh_row(self, urls: list[str]) -> tuple | None:
        """(page_id, raw_html_hash, url, rendered_html, final_url) az első URL-hez, amelynek
        van 7 napnál frissebb, hash-sel bíró sora; különben None."""
        now = _now()
        for url in urls:
            row = self.con.execute(
                "SELECT page_id, raw_html_hash, url, rendered_html, final_url, fetched_at "
                "FROM pages WHERE url = ?", [url],
            ).fetchone()
            if row and row[1] is not None and row[5] is not None and now - row[5] <= SKIP_MAX_AGE:
                return row[:5]
        return None

    async def _unchanged(self, url: str, stored_hash: str) -> bool:
        """A nyers GET válasza 400 alatti, és a stabil hash-e egyezik a tárolttal. A kliensnek a
        böngésző navigációs fejléceit kell küldenie (`NAVIGATION_HEADERS`)."""
        try:
            response = await self.client.get(url, follow_redirects=True)
        except httpx.HTTPError:
            return False
        return (
            response.status_code < 400
            and stable_hash(decode_raw(response.content)) == stored_hash
        )

    def _skip(self, item: QueueItem, page_id: int) -> None:
        """A sor érintetlen; a tárolt linkjei kerülnek a sorba."""
        links = self.con.execute(
            "SELECT to_url, position FROM links WHERE from_page_id = ? ORDER BY ordinal",
            [page_id],
        ).fetchall()
        with _transaction(self.con):
            self.frontier.add_links(item, links)
            self.frontier.mark_done(item.url)
        self.skipped += 1
        self._report(item.url, None, "skipped")

    async def _normalization_404(self, url: str, result: RenderResult) -> str | None:
        if result.status != 404 or result.redirects or self.frontier_policy.trailing_slash is None:
            return None
        alternate = slash_alternate(url)
        if alternate is None:
            return None
        try:
            response = await self.client.get(alternate, follow_redirects=True)
        except httpx.HTTPError:
            return None
        return NORMALIZED_404 if 200 <= response.status_code < 300 else None

    @property
    def frontier_policy(self) -> UrlPolicy:
        return self.frontier.policy

    def _store(self, item: QueueItem, result: RenderResult, error: str | None) -> None:
        policy = self.frontier_policy
        target = None
        if result.redirects and result.final_url and is_internal(result.final_url, policy):
            target = normalize(result.final_url, policy)
        with _transaction(self.con):
            if result.redirects and target != item.url:
                self._write_redirect(item.url, result)
                claimed = self.frontier.claim(
                    result.final_url, depth=item.depth, discovered_from=item.url
                ) if target else None
                if claimed:
                    self._write_page(QueueItem(claimed, item.depth, PRIORITY["body"]), result, None)
            else:
                self._write_page(item, result, error)
            if result.status is None:
                self.frontier.mark_failed(item.url, result.error or "nincs válasz")
            else:
                self.frontier.mark_done(item.url)

    def _write_redirect(self, url: str, result: RenderResult) -> None:
        status = result.redirects[0][0]
        values = dict.fromkeys(_PAGE_COLUMNS)
        values.update(
            url=url, status=status, noindex=False, final_url=result.final_url,
            render_ms=result.render_ms, fetched_at=_now(), run_id=self.run_id,
        )
        page_id = self._upsert(values)
        self._clear_children(page_id)
        self._count(url, status, None)

    def _write_page(self, item: QueueItem, result: RenderResult, error: str | None) -> None:
        error = error or result.error
        values = dict.fromkeys(_PAGE_COLUMNS)
        values.update(
            url=item.url, status=result.status, error=error, noindex=False,
            raw_html_hash=result.raw_html_hash, render_ms=result.render_ms, fetched_at=_now(),
            run_id=self.run_id, final_url=result.final_url,
        )
        parsed = None
        if error is None and _usable(result):
            try:
                parsed = parse_page(
                    result.rendered_html, result.final_url or result.url, self.frontier_policy,
                    result.headers,
                )
            except Exception as exc:  # noqa: BLE001 — egy hibás DOM ne állítsa meg a crawlt
                error = values["error"] = f"parse_error: {type(exc).__name__}: {exc}"[:300]
        if parsed is not None:
            compressed = self._compressor.compress(result.rendered_html.encode("utf-8"))
            self.bytes_stored += len(compressed)
            values.update(
                canonical=parsed.canonical, noindex=parsed.noindex, title=parsed.title,
                meta_description=parsed.meta_description, h1=parsed.h1, lang=parsed.lang,
                hreflang=list(parsed.hreflang), word_count=parsed.word_count,
                main_content=parsed.main_content, main_content_method=parsed.main_content_method,
                external_link_count=parsed.external_link_count, rendered_html=compressed,
            )
        page_id = self._upsert(values)
        self._clear_children(page_id)
        if parsed is not None:
            self._write_children(page_id, parsed)
            self.frontier.add_links(item, [(link.to_url, link.position) for link in parsed.links])
        self._count(item.url, result.status, error)

    def _upsert(self, values: dict) -> int:
        (page_id,) = self.con.execute(
            _UPSERT_PAGE, [values[column] for column in _PAGE_COLUMNS]
        ).fetchone()
        return page_id

    def _clear_children(self, page_id: int) -> None:
        for table in ("links", "headings", "schema_blocks"):
            column = "from_page_id" if table == "links" else "page_id"
            self.con.execute(f"DELETE FROM {table} WHERE {column} = ?", [page_id])

    def _write_children(self, page_id: int, parsed: ParsedPage) -> None:
        if parsed.links:
            self.con.executemany(
                "INSERT INTO links (from_page_id, to_url, anchor, position, nofollow, ordinal) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(page_id, link.to_url, link.anchor, link.position, link.nofollow, link.ordinal)
                 for link in parsed.links],
            )
        if parsed.headings:
            self.con.executemany(
                "INSERT INTO headings (page_id, level, text, ordinal) VALUES (?, ?, ?, ?)",
                [(page_id, h.level, h.text, h.ordinal) for h in parsed.headings],
            )
        if parsed.schema_blocks:
            self.con.executemany(
                "INSERT INTO schema_blocks (page_id, type, json, ordinal) VALUES (?, ?, ?, ?)",
                [(page_id, b.type, b.json, b.ordinal) for b in parsed.schema_blocks],
            )

    def _count(self, url: str, status: int | None, error: str | None) -> None:
        if status is None or status >= 400 or error is not None:
            self.failed += 1
        else:
            self.done += 1
        self._report(url, status, error)

    def _report(self, url: str, status: int | None, error: str | None) -> None:
        if self.progress is not None:
            self.progress(url, status, error)

    def _finish(self) -> None:
        """A linkek `to_page_id`-je és a site-profil, egy tranzakcióban."""
        with _transaction(self.con):
            self.con.execute(
                "UPDATE links SET to_page_id = pages.page_id FROM pages "
                "WHERE links.to_url = pages.url "
                "AND links.to_page_id IS DISTINCT FROM pages.page_id"
            )
            update_site_profile(self.con)

    def _close_run(self, seconds: float, finished: bool) -> CrawlSummary:
        handled = self.done + self.failed
        rate = handled / seconds if seconds > 0 else 0.0
        self.con.execute(
            "UPDATE crawl_runs SET finished_at = ?, pages_done = ?, pages_failed = ?, "
            "pages_skipped = ?, pages_per_sec = ?, bytes_stored = ?, "
            "notes = notes || ? WHERE run_id = ?",
            [_now() if finished else None, self.done, self.failed, self.skipped, rate,
             self.bytes_stored, "" if finished else " (megszakítva)", self.run_id],
        )
        return CrawlSummary(
            run_id=self.run_id, pages_done=self.done, pages_failed=self.failed,
            pages_skipped=self.skipped, pages_per_sec=rate, bytes_stored=self.bytes_stored,
            seconds=seconds,
        )


def _usable(result: RenderResult) -> bool:
    return bool(result.rendered_html) and result.status is not None and result.status < 400


@contextmanager
def _transaction(con: duckdb.DuckDBPyConnection) -> Iterator[None]:
    con.begin()
    try:
        yield
    except BaseException:
        con.rollback()
        raise
    con.commit()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
