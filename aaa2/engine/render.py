"""Render: Playwright Chromium, egy perzisztens böngésző, `concurrency` darab context.

Egy oldal renderelése, egy `render_timeout` kereten belül:

1. navigáció `domcontentloaded`-ig; a fő válasz body-ja a nyers HTML, renderelés előtt;
2. `networkidle` (500 ms hálózati csend), legfeljebb a keret maradékáig;
3. consent: a `config/consent_texts.txt` feliratai, pontos egyezés kis-nagybetű nélkül, három
   körben (button, link, bármely elem szövege); az első látható egyezés kattint; hiba nem
   szakítja meg a renderelést, a kattintás okozta navigáció visszalép;
4. aktiválás: valódi egérmozgás és görgő, hogy a késleltetett scriptek (WP Rocket) is
   elinduljanak, utána egy görgetési kör a lazy betöltőknek; ha az oldalon WP Rocket
   késleltetett script van, a befejező eseményére is vár (legfeljebb 2 s);
5. DOM-stabilitás: minták 500 ms-onként; kész, ha két egymást követő mintában a
   `body.innerHTML` hossza 1%-nál kevesebbet változott, vagy elfogyott a keret;
6. wall: ha a látható szöveg rövid (≤ 50 szó) és a `config/wall_phrases.txt` egy
   kifejezését tartalmazza, a render érvénytelen, `error = 'wall'`.

Route-abort: image, media és font erőforrás, valamint a `config/route_abort_domains.txt`
hostjai (host-végződés, opcionális path-előtaggal). CSS és JS átmegy. A service workerek
tiltva vannak, hogy minden kérés a route-on menjen át.

Retry: navigációs timeout 1×, 5xx 2× exponenciális várakozással, 403 1× másik
user-agenttel. A `render()` sosem dob kivételt: minden hívás egy `RenderResult`.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Frame,
    Locator,
    Page,
    Playwright,
    Request,
    Response,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

CONFIG_DIR = Path(__file__).parent / "config"
CONCURRENCY = 6
RENDER_TIMEOUT = 15.0
BACKOFF_SECONDS = 1.0
TIMEOUT_RETRIES = 1
SERVER_ERROR_RETRIES = 2
ABORTED_RESOURCE_TYPES = frozenset({"image", "media", "font"})
STABILITY_INTERVAL_MS = 500
STABILITY_THRESHOLD = 0.01
WALL_MAX_WORDS = 50
ROCKET_EVENT_WAIT_MS = 2000
CONSENT_CLICK_TIMEOUT_MS = 1500
CONSENT_ROUNDS = ("button", "link", "text")
SCROLL_MAX_STEPS = 30
VIEWPORT = {"width": 1920, "height": 1080}

_WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)
_MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)

_ROCKET_LISTEN_JS = """() => {
  window.__aaaRocketDone = false;
  const done = () => { window.__aaaRocketDone = true; };
  document.addEventListener('rocket-allScriptsLoaded', done, {once: true});
  window.addEventListener('rocket-allScriptsLoaded', done, {once: true});
  return !!document.querySelector('script[type="rocketlazyloadscript"]');
}"""
_SCROLL_JS = """async (maxSteps) => {
  const height = () => Math.max(
    document.documentElement ? document.documentElement.scrollHeight : 0,
    document.body ? document.body.scrollHeight : 0);
  for (let i = 0; i < maxSteps && window.scrollY + window.innerHeight < height(); i++) {
    window.scrollBy(0, 900);
    await new Promise((r) => setTimeout(r, 60));
  }
  window.scrollTo(0, 0);
}"""
_BODY_LENGTH_JS = "() => document.body ? document.body.innerHTML.length : 0"
_BODY_TEXT_JS = "() => document.body ? document.body.innerText : ''"
_NET_ERROR = re.compile(r"net::(ERR_[A-Z_]+)")

Upstream = Callable[[Route, Request], Awaitable[None]]


@dataclass(frozen=True)
class RenderResult:
    """Egy URL renderelésének eredménye. `status` a fő navigáció HTTP-státusza az
    átirányítások után; `redirects` a végső válasz előtti HTTP-átirányítási lépések
    (státusz, URL) sorrendben; `headers` a végső válasz fejlécei kisbetűs kulcsokkal;
    `final_url` az oldal URL-je a render végén; `raw_html` a fő válasz body-ja renderelés
    előtt; `render_ms` az utolsó kísérlet ideje."""

    url: str
    status: int | None = None
    final_url: str | None = None
    raw_html: bytes | None = None
    rendered_html: str | None = None
    error: str | None = None
    render_ms: int = 0
    attempts: int = 1
    redirects: tuple[tuple[int, str], ...] = ()
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def raw_html_hash(self) -> str | None:
        """sha256 a nyers válaszon; ez a skip-alap újrafutásnál."""
        return hashlib.sha256(self.raw_html).hexdigest() if self.raw_html is not None else None


def load_config(name: str) -> tuple[str, ...]:
    """Egy config-fájl sorai; a '#'-os és az üres sorok kimaradnak."""
    lines = (CONFIG_DIR / name).read_text(encoding="utf-8").splitlines()
    return tuple(s for line in lines if (s := line.strip()) and not s.startswith("#"))


def parse_abort_rules(lines: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """(host-végződés, path-előtag) párok; `facebook.com/tr` → ('facebook.com', '/tr')."""
    rules = []
    for line in lines:
        host, _, path = line.strip().lower().partition("/")
        rules.append((host, f"/{path.rstrip('/')}" if path else ""))
    return tuple(rules)


def should_abort(url: str, resource_type: str, rules: Iterable[tuple[str, str]]) -> bool:
    """Kép, média, font, vagy egy listázott host (és path-előtag) kérése."""
    if resource_type in ABORTED_RESOURCE_TYPES:
        return True
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return False
    for suffix, prefix in rules:
        if host != suffix and not host.endswith(f".{suffix}"):
            continue
        if not prefix or parts.path == prefix or parts.path.startswith(f"{prefix}/"):
            return True
    return False


def is_wall(text: str, phrases: Iterable[str]) -> bool:
    """A látható szöveg rövid, és egy wall-kifejezés adja."""
    words = text.lower().split()
    if len(words) > WALL_MAX_WORDS:
        return False
    flat = " ".join(words)
    return any(phrase.lower() in flat for phrase in phrases)


class Renderer:
    """Perzisztens Chromium `concurrency` darab contexttel. Async context managerként
    használandó; a `render()` egyszerre legfeljebb `concurrency` oldalt renderel.

    `upstream`: ha meg van adva, az abort-szűrőn átjutó kéréseket ez szolgálja ki a hálózat
    helyett (rögzített fixture-ök, tesztek)."""

    def __init__(
        self,
        *,
        concurrency: int = CONCURRENCY,
        render_timeout: float = RENDER_TIMEOUT,
        backoff: float = BACKOFF_SECONDS,
        upstream: Upstream | None = None,
        abort_rules: Iterable[tuple[str, str]] | None = None,
        consent_texts: Iterable[str] | None = None,
        wall_phrases: Iterable[str] | None = None,
    ) -> None:
        self.concurrency = concurrency
        self.render_timeout = render_timeout
        self.backoff = backoff
        self.upstream = upstream
        self.abort_rules = tuple(
            abort_rules if abort_rules is not None
            else parse_abort_rules(load_config("route_abort_domains.txt"))
        )
        self.consent_texts = tuple(
            consent_texts if consent_texts is not None else load_config("consent_texts.txt")
        )
        self.wall_phrases = tuple(
            wall_phrases if wall_phrases is not None else load_config("wall_phrases.txt")
        )
        self.user_agent = ""
        self.fallback_user_agent = ""
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._pool: asyncio.Queue[BrowserContext] = asyncio.Queue()
        self._generation = 0
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        self._playwright = await async_playwright().start()
        await self._launch()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except PlaywrightError:
                pass
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def render(self, url: str) -> RenderResult:
        """Egy URL renderelése retry-jal. Sosem dob kivételt."""
        started = time.perf_counter()
        try:
            return await self._render_with_retries(url)
        except Exception as exc:  # noqa: BLE001 — a hívó mindig eredményt kap
            return RenderResult(url, error=_describe(exc), render_ms=_elapsed_ms(started))

    async def _render_with_retries(self, url: str) -> RenderResult:
        attempts = timeouts = server_errors = 0
        fallback = False
        while True:
            attempts += 1
            retryable = partial(_retryable, server_errors=server_errors, fallback=fallback)
            result = await self._attempt(url, fallback=fallback, retryable=retryable)
            if result.error == "timeout" and timeouts < TIMEOUT_RETRIES:
                timeouts += 1
            elif retryable(result.status) and result.status == 403:
                fallback = True
            elif retryable(result.status):
                await asyncio.sleep(self.backoff * 2**server_errors)
                server_errors += 1
            else:
                return replace(result, attempts=attempts)

    async def _attempt(
        self, url: str, *, fallback: bool, retryable: Callable[[int | None], bool]
    ) -> RenderResult:
        await self._ensure_browser()
        if fallback:
            context = await self._new_context(self.fallback_user_agent)
            try:
                return await self._render_page(context, url, retryable)
            finally:
                await _close_quietly(context)
        generation = self._generation
        context = await self._pool.get()
        try:
            return await self._render_page(context, url, retryable)
        finally:
            if generation == self._generation:
                self._pool.put_nowait(context)
            else:
                await _close_quietly(context)

    async def _render_page(
        self, context: BrowserContext, url: str, retryable: Callable[[int | None], bool]
    ) -> RenderResult:
        """Egy kísérlet. Ha a státusz újrapróbálható, a render kimarad: úgyis jön új kísérlet."""
        started = time.perf_counter()
        deadline = time.monotonic() + self.render_timeout
        page = await context.new_page()
        navigations: list[Response] = []

        def on_response(response: Response) -> None:
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                navigations.append(response)

        page.on("response", on_response)
        page.on("popup", _close_quietly)
        try:
            try:
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.render_timeout * 1000
                )
            except PlaywrightTimeoutError:
                return RenderResult(url, error="timeout", render_ms=_elapsed_ms(started))
            except PlaywrightError as exc:
                last = navigations[-1] if navigations else None
                if last is not None and _non_html(last):
                    return RenderResult(
                        url, final_url=last.url, error=_non_html(last),
                        render_ms=_elapsed_ms(started), **await _response_fields(last),
                    )
                return RenderResult(url, error=_describe(exc), render_ms=_elapsed_ms(started))

            fields = await _response_fields(response)
            raw_html = await _body(response)
            if retryable(fields.get("status")):
                return RenderResult(
                    url, final_url=page.url, raw_html=raw_html,
                    render_ms=_elapsed_ms(started), **fields,
                )
            if response is not None and (non_html := _non_html(response)):
                return RenderResult(
                    url, final_url=page.url, raw_html=raw_html, error=non_html,
                    render_ms=_elapsed_ms(started), **fields,
                )

            await _wait_network_idle(page, deadline)
            await _accept_consent(page, self.consent_texts)
            await _activate(page, deadline)
            await _wait_dom_stable(page, deadline)
            rendered_html = await _content(page)
            text = await _evaluate(page, _BODY_TEXT_JS, default="")
            return RenderResult(
                url,
                final_url=page.url,
                raw_html=raw_html,
                rendered_html=rendered_html,
                error="wall" if is_wall(text, self.wall_phrases) else None,
                render_ms=_elapsed_ms(started),
                **fields,
            )
        finally:
            await _close_quietly(page)

    async def _ensure_browser(self) -> None:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self._launch()

    async def _launch(self) -> None:
        if self._playwright is None:
            raise RuntimeError("a Renderer-t async with blokkban kell használni")
        self._browser = await self._playwright.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        major = self._browser.version.split(".", 1)[0]
        self.user_agent = _WINDOWS_UA.format(major=major)
        self.fallback_user_agent = _MAC_UA.format(major=major)
        self._generation += 1
        self._pool = asyncio.Queue()
        for _ in range(self.concurrency):
            self._pool.put_nowait(await self._new_context(self.user_agent))

    async def _new_context(self, user_agent: str) -> BrowserContext:
        assert self._browser is not None
        context = await self._browser.new_context(
            viewport=VIEWPORT, user_agent=user_agent, service_workers="block"
        )
        await context.route("**/*", self._route)
        return context

    async def _route(self, route: Route, request: Request) -> None:
        try:
            if should_abort(request.url, request.resource_type, self.abort_rules):
                await route.abort()
            elif self.upstream is not None:
                await self.upstream(route, request)
            else:
                await route.continue_()
        except PlaywrightError:
            pass


def _retryable(status: int | None, *, server_errors: int, fallback: bool) -> bool:
    """Jön-e még kísérlet erre a státuszra: 5xx a keret alatt, vagy 403 a másik UA előtt."""
    if status is None:
        return False
    if 500 <= status < 600:
        return server_errors < SERVER_ERROR_RETRIES
    return status == 403 and not fallback


async def _wait_network_idle(page: Page, deadline: float) -> None:
    remaining = _remaining_ms(deadline)
    if remaining <= 0:
        return
    try:
        await page.wait_for_load_state("networkidle", timeout=remaining)
    except PlaywrightError:
        pass


async def _accept_consent(page: Page, texts: Iterable[str]) -> tuple[str, str] | None:
    """Consent-kattintás három körben, mindegyikben a teljes feliratlistával, a sorrendjében:
    `button` szerep, `link` szerep, végül bármely elem pontos szövege. Az első látható
    egyezés kattint, a kattintási hiba a következő jelöltre visz. Ha a kattintás másik
    oldalra navigált, visszalép. Visszaadja a (kör, felirat) párt, vagy None-t."""
    patterns = [(text, re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE)) for text in texts]
    for kind in CONSENT_ROUNDS:
        for text, name in patterns:
            for frame in page.frames:
                before = _without_fragment(page.url)
                if await _click_first_visible(_consent_candidates(frame, kind, name)):
                    if _without_fragment(page.url) != before:
                        await _go_back(page)
                    return kind, text
    return None


def _consent_candidates(frame: Frame, kind: str, name: re.Pattern[str]) -> Locator:
    if kind == "text":
        return frame.get_by_text(name)
    return frame.get_by_role(kind, name=name)


async def _click_first_visible(candidates: Locator) -> bool:
    try:
        for index in range(min(await candidates.count(), 5)):
            candidate = candidates.nth(index)
            if await candidate.is_visible():
                await candidate.click(timeout=CONSENT_CLICK_TIMEOUT_MS)
                return True
    except PlaywrightError:
        pass
    return False


async def _go_back(page: Page) -> None:
    try:
        await page.go_back(wait_until="domcontentloaded", timeout=CONSENT_CLICK_TIMEOUT_MS * 2)
    except PlaywrightError:
        pass


async def _activate(page: Page, deadline: float) -> None:
    has_rocket = await _evaluate(page, _ROCKET_LISTEN_JS, default=False)
    try:
        await page.mouse.move(160, 200)
        await page.mouse.move(420, 320)
        await page.mouse.wheel(0, 700)
    except PlaywrightError:
        pass
    if _remaining_ms(deadline) > 0:
        await _evaluate(page, _SCROLL_JS, SCROLL_MAX_STEPS)
    remaining = min(_remaining_ms(deadline), ROCKET_EVENT_WAIT_MS)
    if has_rocket and remaining > 0:
        try:
            await page.wait_for_function("() => window.__aaaRocketDone === true", timeout=remaining)
        except PlaywrightError:
            pass


async def _wait_dom_stable(page: Page, deadline: float) -> None:
    previous = await _evaluate(page, _BODY_LENGTH_JS, default=0)
    while (remaining := _remaining_ms(deadline)) > 0:
        try:
            await page.wait_for_timeout(min(STABILITY_INTERVAL_MS, remaining))
        except PlaywrightError:
            return
        current = await _evaluate(page, _BODY_LENGTH_JS, default=0)
        if abs(current - previous) < STABILITY_THRESHOLD * max(previous, 1):
            return
        previous = current


async def _content(page: Page) -> str | None:
    """A renderelt DOM; ha közben JS-navigáció fut, megvárja és újrapróbálja egyszer."""
    for _ in range(2):
        try:
            return await page.content()
        except PlaywrightError:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=2000)
            except PlaywrightError:
                pass
    return None


async def _response_fields(response: Response | None) -> dict:
    """A végső válasz státusza, az előtte lévő átirányítási lépések és a fejlécek."""
    if response is None:
        return {}
    return {
        "status": response.status,
        "redirects": await _redirect_chain(response),
        "headers": await _headers(response),
    }


async def _redirect_chain(response: Response) -> tuple[tuple[int, str], ...]:
    steps = []
    request = response.request.redirected_from
    while request is not None:
        try:
            previous = await request.response()
        except PlaywrightError:
            previous = None
        if previous is not None:
            steps.append((previous.status, request.url))
        request = request.redirected_from
    return tuple(reversed(steps))


async def _headers(response: Response) -> dict[str, str]:
    try:
        return {key.lower(): value for key, value in (await response.all_headers()).items()}
    except PlaywrightError:
        return {}


def _without_fragment(url: str) -> str:
    return url.split("#", 1)[0]


def _non_html(response: Response) -> str | None:
    """'non_html: <típus>', ha a válasz Content-Type-ja megvan és nem HTML; különben None."""
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type and "html" not in content_type:
        return f"non_html: {content_type}"
    return None


async def _body(response: Response | None) -> bytes | None:
    if response is None:
        return None
    try:
        return await response.body()
    except PlaywrightError:
        return None


async def _evaluate(page: Page, script: str, arg: object = None, *, default: object = None):
    try:
        return await page.evaluate(script, arg)
    except PlaywrightError:
        return default


async def _close_quietly(target: Page | BrowserContext) -> None:
    try:
        await target.close()
    except PlaywrightError:
        pass


def _describe(exc: BaseException) -> str:
    message = str(exc)
    if isinstance(exc, PlaywrightTimeoutError):
        return "timeout"
    if match := _NET_ERROR.search(message):
        return f"network_error: {match.group(1)}"
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    return f"render_error: {type(exc).__name__}: {first_line}"[:300]


def _remaining_ms(deadline: float) -> int:
    return int((deadline - time.monotonic()) * 1000)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
