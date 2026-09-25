"""Render: helyi statikus HTML Playwright route-on át, hálózat nélkül.

Kivétel az átirányítás: a route-ból adott 3xx célját a Playwright már nem vezeti át a
route-on, ezért azt egy helyi HTTP-szerver szolgálja ki (127.0.0.1).
"""
import asyncio
import hashlib
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

import pytest
from selectolax.parser import HTMLParser

import aaa2.engine.render as render_module
from aaa2.engine.normalize import UrlPolicy, is_internal, normalize
from aaa2.engine.render import (
    Renderer,
    is_wall,
    load_config,
    parse_abort_rules,
    should_abort,
)

HTML = "text/html; charset=utf-8"


class FakeSite:
    """URL → válasz a Renderer upstream-jén. Érték: str (200 HTML), int (státusz),
    (státusz, body), (státusz, fejlécek, body), "abort", vagy async függvény a kérésre."""

    def __init__(self, pages):
        self.pages = dict(pages)
        self.requests = []

    async def __call__(self, route, request):
        self.requests.append(
            (request.url, request.resource_type, request.headers.get("user-agent", ""))
        )
        spec = self.pages.get(request.url, 404)
        if callable(spec):
            spec = await spec(request)
        if spec == "abort":
            await route.abort("connectionrefused")
            return
        if isinstance(spec, int):
            status, headers, body = spec, {}, f"<html><body><h1>{spec}</h1></body></html>"
        elif isinstance(spec, str):
            status, headers, body = 200, {}, spec
        elif len(spec) == 2:
            (status, body), headers = spec, {}
        else:
            status, headers, body = spec
        await route.fulfill(status=status, headers={"content-type": HTML, **headers}, body=body)

    def hits(self, url, resource_type=None):
        return sum(
            1 for u, t, _ in self.requests if u == url and resource_type in (None, t)
        )


@pytest.fixture
async def make_renderer():
    renderers = []

    async def factory(pages=(), **options):
        site = FakeSite(dict(pages))
        options.setdefault("concurrency", 1)
        options.setdefault("render_timeout", 5.0)
        options.setdefault("backoff", 0)
        renderer = await Renderer(upstream=site, **options).__aenter__()
        renderers.append(renderer)
        return renderer, site

    yield factory
    for renderer in renderers:
        await renderer.close()


def links(html):
    """A DOM `<a href>` célpontjai; a scriptek szövegében álló `<a` nem számít."""
    if not html:
        return []
    return [a.attributes.get("href") for a in HTMLParser(html).css("a[href]")]


# ---------------------------------------------------------------------------
# tiszta függvények
# ---------------------------------------------------------------------------

RULES = parse_abort_rules(["google-analytics.com", "googletagmanager.com", "facebook.com/tr"])

ABORTS = [
    ("listázott host aldomainje", "https://www.google-analytics.com/g/collect", "xhr", True),
    ("listázott host maga", "https://google-analytics.com/x", "script", True),
    ("csak végződés-egyezés", "https://notgoogle-analytics.com/x", "script", False),
    ("path-előtag pontosan", "https://www.facebook.com/tr", "script", True),
    ("path-előtag query-vel", "https://www.facebook.com/tr?id=1", "script", True),
    ("path-előtag alatt", "https://www.facebook.com/tr/x", "script", True),
    ("path-előtag csak szegmenshatáron", "https://www.facebook.com/trending", "script", False),
    ("host, de más path", "https://www.facebook.com/", "document", False),
    ("kép", "https://kk.coach/a.png", "image", True),
    ("média", "https://kk.coach/v.mp4", "media", True),
    ("font", "https://kk.coach/f.woff2", "font", True),
    ("saját JS átmegy", "https://kk.coach/app.js", "script", False),
    ("saját CSS átmegy", "https://kk.coach/s.css", "stylesheet", False),
    ("data: kép", "data:image/png;base64,AAAA", "image", True),
    ("data: CSS", "data:text/css,p{}", "stylesheet", False),
]


@pytest.mark.parametrize(
    ("url", "resource_type", "expected"), [a[1:] for a in ABORTS], ids=[a[0] for a in ABORTS]
)
def test_should_abort(url, resource_type, expected):
    assert should_abort(url, resource_type, RULES) is expected


def test_parse_abort_rules():
    assert parse_abort_rules(["hotjar.com", "facebook.com/tr", " LinkedIn.com/px/ "]) == (
        ("hotjar.com", ""), ("facebook.com", "/tr"), ("linkedin.com", "/px"),
    )


def test_config_files_load_without_comments():
    abort = load_config("route_abort_domains.txt")
    assert "google-analytics.com" in abort and not any(line.startswith("#") for line in abort)
    assert load_config("consent_texts.txt")[0] == "Accept all"
    assert "enable javascript" in load_config("wall_phrases.txt")


WALLS = [
    ("csak a wall-szöveg", "Please enable JavaScript to continue.", True),
    ("magyar wall", "Böngésződ nem támogatott. Frissíts!", True),
    ("sortörés a kifejezésben", "Please enable\n  JavaScript", True),
    ("hosszú oldal a kifejezéssel", "enable javascript " + "szó " * 60, False),
    ("rendes rövid oldal", "Minden rendben, itt a tartalom.", False),
    ("üres", "", False),
    ("Cloudflare várakozás", "Just a moment... Checking your browser before accessing kk.coach.", True),
    ("Cloudflare Turnstile", "kk.coach Verify you are human by completing the action below.", True),
    ("Cloudflare tiltás", "Attention Required! | Cloudflare Sorry, you have been blocked", True),
    ("cookie-fal", "Please enable cookies. Error 1020", True),
]


@pytest.mark.parametrize(("text", "expected"), [w[1:] for w in WALLS], ids=[w[0] for w in WALLS])
def test_is_wall(text, expected):
    assert is_wall(text, load_config("wall_phrases.txt")) is expected


# ---------------------------------------------------------------------------
# JS-render
# ---------------------------------------------------------------------------

CSR_PAGE = """<!doctype html><html><head><title>CSR</title></head>
<body><div id="app"></div>
<script>
setTimeout(() => {
  const app = document.getElementById('app');
  for (const [href, text] of [['/termekek/', 'Termékek'], ['/kosar/', 'Kosár'], ['/termek/1/', 'Egy']]) {
    const a = document.createElement('a'); a.href = href; a.textContent = text; app.appendChild(a);
  }
}, 300);
</script></body></html>"""


async def test_csr_links_only_in_rendered_dom(make_renderer):
    renderer, _ = await make_renderer({"https://bolt.test/": CSR_PAGE})
    result = await renderer.render("https://bolt.test/")
    assert (result.status, result.error, result.attempts) == (200, None, 1)
    assert result.final_url == "https://bolt.test/"
    assert links(result.raw_html.decode()) == []
    assert links(result.rendered_html) == ["/termekek/", "/kosar/", "/termek/1/"]
    assert result.raw_html == CSR_PAGE.encode()
    assert result.raw_html_hash == hashlib.sha256(CSR_PAGE.encode()).hexdigest()
    assert result.render_ms > 0


PROGRESSIVE_PAGE = """<html><body><h1>Lista</h1><script>
let n = 0;
const t = setInterval(() => {
  const p = document.createElement('p'); p.id = 'chunk' + n; p.textContent = 'x'.repeat(2000);
  document.body.appendChild(p);
  if (++n === 6) clearInterval(t);
}, 300);
</script></body></html>"""


XHR_PAGE = """<html><body><h1>Bolt</h1><div id="menu"></div><script>
fetch('/api/menu').then((r) => r.json()).then((items) => {
  for (const href of items) {
    const a = document.createElement('a'); a.href = href; a.textContent = href;
    document.getElementById('menu').appendChild(a);
  }
});
</script></body></html>"""


async def test_waits_for_network_idle(make_renderer):
    async def slow_api(request):
        await asyncio.sleep(1.2)
        return 200, {"content-type": "application/json"}, '["/kategoria/", "/akcio/"]'

    renderer, _ = await make_renderer({
        "https://bolt.test/": XHR_PAGE, "https://bolt.test/api/menu": slow_api,
    })
    result = await renderer.render("https://bolt.test/")
    assert links(result.rendered_html) == ["/kategoria/", "/akcio/"]


async def test_waits_until_dom_is_stable(make_renderer):
    renderer, _ = await make_renderer({"https://bolt.test/lista": PROGRESSIVE_PAGE})
    result = await renderer.render("https://bolt.test/lista")
    assert all(f'id="chunk{i}"' in result.rendered_html for i in range(6))


ROCKET_PAGE = """<html><body><h1>WP</h1>
<script type="rocketlazyloadscript">/* késleltetett script */</script>
<script>
const go = () => {
  window.removeEventListener('mousemove', go);
  setTimeout(() => {
    const a = document.createElement('a'); a.href = '/kesleltetett/'; a.textContent = 'K';
    document.body.appendChild(a);
    setTimeout(() => document.dispatchEvent(new Event('rocket-allScriptsLoaded')), 50);
  }, 900);
};
window.addEventListener('mousemove', go);
</script></body></html>"""


async def test_waits_for_wp_rocket_delayed_scripts(make_renderer):
    renderer, _ = await make_renderer({"https://wp.test/": ROCKET_PAGE})
    result = await renderer.render("https://wp.test/")
    assert "/kesleltetett/" in links(result.rendered_html)


WHEEL_PAGE = """<html><body><h1>Görgő</h1><script>
window.addEventListener('wheel', () => {
  const a = document.createElement('a'); a.href = '/gorgo/'; a.textContent = 'G';
  document.body.appendChild(a);
}, {once: true});
</script></body></html>"""


async def test_real_input_activates_delayed_content(make_renderer):
    renderer, _ = await make_renderer({"https://wp.test/gorgo": WHEEL_PAGE})
    result = await renderer.render("https://wp.test/gorgo")
    assert links(result.rendered_html) == ["/gorgo/"]


LAZY_PAGE = """<html><body><div style="height:6000px">fent</div>
<div id="lazy" style="height:20px"></div>
<script>
new IntersectionObserver((entries, observer) => {
  if (!entries.some((e) => e.isIntersecting)) return;
  const a = document.createElement('a'); a.href = '/lent/'; a.textContent = 'L';
  document.getElementById('lazy').appendChild(a);
  observer.disconnect();
}).observe(document.getElementById('lazy'));
</script></body></html>"""


async def test_scroll_pass_triggers_lazy_loaders(make_renderer):
    renderer, _ = await make_renderer({"https://wp.test/hosszu": LAZY_PAGE})
    result = await renderer.render("https://wp.test/hosszu")
    assert links(result.rendered_html) == ["/lent/"]


# ---------------------------------------------------------------------------
# route-abort
# ---------------------------------------------------------------------------

ABORT_PAGE = """<html><head>
<link rel="stylesheet" href="/s.css">
<script src="/app.js"></script>
<script src="https://www.googletagmanager.com/gtm.js?id=GTM-X"></script>
<script src="https://www.facebook.com/tr?id=1"></script>
<script src="https://www.facebook.com/trending.js"></script>
</head><body>
<img src="/kep.png" alt="kép"><video src="/v.mp4" autoplay muted></video>
<p style="font-family: F">szöveg</p>
</body></html>"""


async def test_route_abort_blocks_listed_hosts_and_resource_types(make_renderer):
    renderer, site = await make_renderer({
        "https://kk.test/": ABORT_PAGE,
        "https://kk.test/s.css": (200, {"content-type": "text/css"},
                                  "@font-face{font-family:F;src:url(/f.woff2)}"),
        "https://kk.test/app.js": (200, {"content-type": "text/javascript"}, "window.ok=1"),
        "https://www.facebook.com/trending.js": (200, {"content-type": "text/javascript"}, ""),
    })
    result = await renderer.render("https://kk.test/")
    assert result.status == 200
    reached = {url for url, _, _ in site.requests}
    assert {"https://kk.test/", "https://kk.test/s.css", "https://kk.test/app.js",
            "https://www.facebook.com/trending.js"} <= reached
    assert not reached & {
        "https://www.googletagmanager.com/gtm.js?id=GTM-X",
        "https://www.facebook.com/tr?id=1",
        "https://kk.test/kep.png",
        "https://kk.test/v.mp4",
        "https://kk.test/f.woff2",
    }


# ---------------------------------------------------------------------------
# consent
# ---------------------------------------------------------------------------

CONSENT_SCRIPT = """<script>
function mark(v) { document.body.setAttribute('data-consent', v); }
</script>"""

CONSENT_PAGE = f"""<html><body>{CONSENT_SCRIPT}
<div id="banner">
  <button style="display:none" onclick="mark('rejtett')">Accept all</button>
  <button onclick="mark('cookies')">Accept all cookies</button>
  <button onclick="mark('elfogadom')">ELFOGADOM</button>
</div></body></html>"""

# A 0×0-s gomb benne van az akadálymentesítési fában (a get_by_role megtalálja), de nem
# látható; a display:none gombot a get_by_role eleve kihagyná.
CONSENT_SECOND_VISIBLE_PAGE = f"""<html><body>{CONSENT_SCRIPT}
<button style="width:0;height:0;padding:0;border:0;overflow:hidden"
        onclick="mark('rejtett')">Accept all</button>
<button onclick="mark('lathato')">Accept all</button>
<button onclick="mark('elfogadom')">Elfogadom</button>
</body></html>"""

CONSENT_BLOCKED_PAGE = f"""<html><body>{CONSENT_SCRIPT}
<button style="pointer-events:none" onclick="mark('blokkolt')">Accept all</button>
<button onclick="mark('elfogadom')">Elfogadom</button>
</body></html>"""

CONSENT_IFRAME_PAGE = """<html><body><h1>Oldal</h1>
<iframe src="https://kk.test/cmp.html"></iframe></body></html>"""
CONSENT_IFRAME = """<html><body>
<button onclick="parent.document.body.setAttribute('data-consent', 'iframe')">Allow all</button>
</body></html>"""


async def test_consent_clicks_first_visible_exact_match(make_renderer):
    renderer, _ = await make_renderer({"https://kk.test/": CONSENT_PAGE})
    result = await renderer.render("https://kk.test/")
    assert 'data-consent="elfogadom"' in result.rendered_html


async def test_consent_skips_hidden_duplicate(make_renderer):
    renderer, _ = await make_renderer({"https://kk.test/": CONSENT_SECOND_VISIBLE_PAGE})
    result = await renderer.render("https://kk.test/")
    assert 'data-consent="lathato"' in result.rendered_html


# Ikonos link: az akadálymentes neve "Elfogadom", a látható szövege nem, így csak a
# link-kör találja meg, a szöveg-kör nem.
CONSENT_LINK_PAGE = f"""<html><body>{CONSENT_SCRIPT}
<a href="#" aria-label="Elfogadom" onclick="mark('link'); return false;">&#10003;</a>
</body></html>"""

CONSENT_TEXT_PAGE = f"""<html><body>{CONSENT_SCRIPT}
<div class="cc-banner"><div class="cc-btn" onclick="mark('div')">Összes elfogadása</div></div>
</body></html>"""

# Az "Accept all" a lista elején áll, de csak div; az "Elfogadom" később jön, de gomb.
# A button-kör a teljes listán végigmegy, mielőtt a szöveg-kör indulna.
CONSENT_ROUND_ORDER_PAGE = f"""<html><body>{CONSENT_SCRIPT}
<div onclick="mark('div')">Accept all</div>
<button onclick="mark('gomb')">Elfogadom</button>
</body></html>"""

CONSENT_NAVIGATING_LINK_PAGE = """<html><body><h1 id="eredeti">Eredeti oldal</h1>
<a href="/cookie-szabalyzat">Accept all</a>
</body></html>"""

CONSENT_POPUP_PAGE = f"""<html><body>{CONSENT_SCRIPT}
<a href="/uj-ablak" target="_blank" onclick="mark('popup')">Elfogadom</a>
</body></html>"""


async def test_consent_link_round(make_renderer):
    renderer, _ = await make_renderer({"https://kk.test/": CONSENT_LINK_PAGE})
    result = await renderer.render("https://kk.test/")
    assert 'data-consent="link"' in result.rendered_html


async def test_consent_text_round_any_visible_element(make_renderer):
    renderer, _ = await make_renderer({"https://kk.test/": CONSENT_TEXT_PAGE})
    result = await renderer.render("https://kk.test/")
    assert 'data-consent="div"' in result.rendered_html


async def test_consent_rounds_run_over_whole_list(make_renderer):
    renderer, _ = await make_renderer({"https://kk.test/": CONSENT_ROUND_ORDER_PAGE})
    result = await renderer.render("https://kk.test/")
    assert 'data-consent="gomb"' in result.rendered_html


async def test_consent_navigation_is_undone(make_renderer):
    renderer, site = await make_renderer({
        "https://kk.test/": CONSENT_NAVIGATING_LINK_PAGE,
        "https://kk.test/cookie-szabalyzat": "<html><body><h1>Szabályzat</h1></body></html>",
    })
    result = await renderer.render("https://kk.test/")
    assert site.hits("https://kk.test/cookie-szabalyzat", "document") == 1
    assert result.final_url == "https://kk.test/"
    assert 'id="eredeti"' in result.rendered_html


async def test_consent_popup_is_closed(make_renderer):
    renderer, site = await make_renderer({
        "https://kk.test/": CONSENT_POPUP_PAGE,
        "https://kk.test/uj-ablak": "<html><body>felugró</body></html>",
    })
    result = await renderer.render("https://kk.test/")
    await asyncio.sleep(0.3)
    assert result.final_url == "https://kk.test/"
    assert 'data-consent="popup"' in result.rendered_html
    assert site.hits("https://kk.test/uj-ablak", "document") == 1
    assert [len(context.pages) for context in renderer._browser.contexts] == [0]


async def test_consent_click_error_does_not_stop_render(make_renderer):
    renderer, _ = await make_renderer({"https://kk.test/": CONSENT_BLOCKED_PAGE})
    result = await renderer.render("https://kk.test/")
    assert result.error is None
    assert 'data-consent="elfogadom"' in result.rendered_html


async def test_consent_inside_iframe(make_renderer):
    renderer, _ = await make_renderer({
        "https://kk.test/": CONSENT_IFRAME_PAGE,
        "https://kk.test/cmp.html": CONSENT_IFRAME,
    })
    result = await renderer.render("https://kk.test/")
    assert 'data-consent="iframe"' in result.rendered_html


# ---------------------------------------------------------------------------
# wall, státuszok, nem-HTML, hibák
# ---------------------------------------------------------------------------


async def test_wall_page_is_invalid(make_renderer):
    renderer, _ = await make_renderer({
        "https://bolt.test/": "<html><body><p>Please enable JavaScript to continue.</p></body></html>",
    })
    result = await renderer.render("https://bolt.test/")
    assert (result.status, result.error) == (200, "wall")


CHALLENGE_PAGE = """<html><head><title>Just a moment...</title></head><body>
<h1>Just a moment...</h1><p>Checking your browser before accessing bolt.test.</p>
</body></html>"""


async def test_bot_challenge_page_is_wall(make_renderer):
    renderer, _ = await make_renderer({"https://bolt.test/": (403, {}, CHALLENGE_PAGE)})
    result = await renderer.render("https://bolt.test/")
    assert (result.status, result.error, result.attempts) == (403, "wall", 2)


async def test_404_is_rendered_not_retried(make_renderer):
    renderer, site = await make_renderer()
    result = await renderer.render("https://kk.test/nincs/")
    assert (result.status, result.error, result.attempts) == (404, None, 1)
    assert "<h1>404</h1>" in result.rendered_html
    assert site.hits("https://kk.test/nincs/", "document") == 1


async def test_non_html_response(make_renderer):
    renderer, _ = await make_renderer({
        "https://kk.test/adat.json": (200, {"content-type": "application/json"}, '{"a": 1}'),
        "https://kk.test/doc.pdf": (200, {"content-type": "application/pdf"}, "%PDF-1.4"),
    })
    json_result = await renderer.render("https://kk.test/adat.json")
    assert (json_result.status, json_result.error) == (200, "non_html: application/json")
    assert json_result.raw_html == b'{"a": 1}' and json_result.rendered_html is None
    assert json_result.headers["content-type"] == "application/json"
    pdf_result = await renderer.render("https://kk.test/doc.pdf")
    assert (pdf_result.status, pdf_result.error) == (200, "non_html: application/pdf")


async def test_headers_of_final_response(make_renderer):
    renderer, _ = await make_renderer({
        "https://kk.test/": (200, {
            "X-Robots-Tag": "noindex",
            "Last-Modified": "Wed, 23 Sep 2026 10:00:00 GMT",
            "ETag": '"abc"',
            "Cache-Control": "no-cache",
            "Content-Language": "hu-HU",
        }, "<html><body>ok</body></html>"),
    })
    result = await renderer.render("https://kk.test/")
    assert result.headers["x-robots-tag"] == "noindex"
    assert result.headers["last-modified"] == "Wed, 23 Sep 2026 10:00:00 GMT"
    assert result.headers["etag"] == '"abc"'
    assert result.headers["cache-control"] == "no-cache"
    assert result.headers["content-language"] == "hu-HU"
    assert result.headers["content-type"] == HTML
    assert result.redirects == ()


async def test_error_status_keeps_headers(make_renderer, sleeps):
    renderer, _ = await make_renderer({"https://kk.test/": (503, {"Retry-After": "120"}, "le")})
    result = await renderer.render("https://kk.test/")
    assert (result.status, result.headers["retry-after"]) == (503, "120")


async def test_network_error_and_invalid_url_never_raise(make_renderer):
    renderer, _ = await make_renderer({"https://kk.test/le": "abort"})
    refused = await renderer.render("https://kk.test/le")
    assert (refused.status, refused.error) == (None, "network_error: ERR_CONNECTION_REFUSED")
    invalid = await renderer.render("nem-url")
    assert invalid.status is None and invalid.error.startswith("render_error:")


# ---------------------------------------------------------------------------
# retry
# ---------------------------------------------------------------------------


def sequence(*responses):
    remaining = list(responses)

    async def handler(request):
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return handler


@pytest.fixture
def sleeps(monkeypatch):
    """A render modul várakozásai: rögzítve, valódi várakozás nélkül."""
    recorded = []

    async def fake_sleep(delay):
        recorded.append(delay)

    fake = types.SimpleNamespace(**{**vars(asyncio), "sleep": fake_sleep})
    monkeypatch.setattr(render_module, "asyncio", fake)
    return recorded


async def test_5xx_retried_twice_with_exponential_backoff(make_renderer, sleeps):
    renderer, site = await make_renderer(
        {"https://kk.test/": sequence(503, 502, "<html><body>ok</body></html>")}, backoff=0.5
    )
    result = await renderer.render("https://kk.test/")
    assert (result.status, result.error, result.attempts) == (200, None, 3)
    assert sleeps == [0.5, 1.0]
    assert site.hits("https://kk.test/", "document") == 3


async def test_5xx_gives_up_after_two_retries(make_renderer, sleeps):
    renderer, site = await make_renderer({"https://kk.test/": 503})
    result = await renderer.render("https://kk.test/")
    assert (result.status, result.attempts) == (503, 3)
    assert "<h1>503</h1>" in result.rendered_html
    assert site.hits("https://kk.test/", "document") == 3


async def test_403_retried_once_with_other_user_agent(make_renderer):
    renderer, site = await make_renderer()

    async def only_fallback_ua(request):
        if request.headers.get("user-agent") == renderer.fallback_user_agent:
            return "<html><body>ok</body></html>"
        return 403

    site.pages["https://kk.test/"] = only_fallback_ua
    result = await renderer.render("https://kk.test/")
    assert (result.status, result.error, result.attempts) == (200, None, 2)
    agents = [ua for url, kind, ua in site.requests if kind == "document"]
    assert agents == [renderer.user_agent, renderer.fallback_user_agent]
    assert renderer.user_agent != renderer.fallback_user_agent


async def test_403_stays_403_after_one_retry(make_renderer):
    renderer, site = await make_renderer({"https://kk.test/": 403})
    result = await renderer.render("https://kk.test/")
    assert (result.status, result.attempts) == (403, 2)
    assert site.hits("https://kk.test/", "document") == 2


async def test_timeout_retried_once(make_renderer):
    release = asyncio.Event()

    async def hang(request):
        await release.wait()
        return "<html><body>késő</body></html>"

    renderer, site = await make_renderer({"https://kk.test/lassu": hang}, render_timeout=1.0)
    try:
        result = await renderer.render("https://kk.test/lassu")
    finally:
        release.set()
    assert (result.status, result.error, result.attempts) == (None, "timeout", 2)
    assert site.hits("https://kk.test/lassu", "document") == 2
    await asyncio.sleep(0.1)


async def test_timeout_then_success(make_renderer):
    release = asyncio.Event()
    calls = []

    async def slow_once(request):
        calls.append(1)
        if len(calls) == 1:
            await release.wait()
        return "<html><body>ok</body></html>"

    renderer, _ = await make_renderer({"https://kk.test/": slow_once}, render_timeout=1.0)
    try:
        result = await renderer.render("https://kk.test/")
    finally:
        release.set()
    assert (result.status, result.error, result.attempts) == (200, None, 2)
    await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# böngésző és contextek
# ---------------------------------------------------------------------------


async def test_concurrency_limits_parallel_pages(make_renderer):
    state = {"now": 0, "max": 0}

    async def page(request):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.3)
        state["now"] -= 1
        return "<html><body>ok</body></html>"

    urls = [f"https://kk.test/{i}" for i in range(6)]
    renderer, _ = await make_renderer({url: page for url in urls}, concurrency=2)
    results = await asyncio.gather(*(renderer.render(url) for url in urls))
    assert [r.status for r in results] == [200] * 6
    assert state["max"] == 2


async def test_browser_is_relaunched_after_crash(make_renderer):
    renderer, _ = await make_renderer({"https://kk.test/": "<html><body>ok</body></html>"})
    assert (await renderer.render("https://kk.test/")).status == 200
    await renderer._browser.close()
    result = await renderer.render("https://kk.test/")
    assert (result.status, result.error) == (200, None)


# ---------------------------------------------------------------------------
# átirányítás helyi HTTP-szerverrel
# ---------------------------------------------------------------------------


UJ_HEADERS = {
    "Content-Type": HTML,
    "X-Robots-Tag": "noindex, nofollow",
    "Last-Modified": "Wed, 23 Sep 2026 10:00:00 GMT",
    "ETag": '"uj-1"',
    "Cache-Control": "max-age=600",
    "Content-Language": "hu",
}


@pytest.fixture
def local_site():
    pages = {
        "/regi": (301, {"Location": "/uj/"}, b""),
        "/lanc": (302, {"Location": "/koztes"}, b""),
        "/koztes": (301, {"Location": "/uj/"}, b""),
        "/uj/": (200, UJ_HEADERS, b"<html><body><a href='/x/'>x</a></body></html>"),
        "/torott": (301, {"Location": "/sehol/"}, b""),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = pages.get(self.path, (404, {"Content-Type": HTML}, b"nincs"))
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


async def test_redirect_is_followed_final_url_and_raw_from_target(local_site):
    async with Renderer(concurrency=1, render_timeout=5.0) as renderer:
        moved = await renderer.render(f"{local_site}/regi")
        broken = await renderer.render(f"{local_site}/torott")
    assert (moved.status, moved.error) == (200, None)
    assert moved.final_url == f"{local_site}/uj/"
    assert moved.raw_html == b"<html><body><a href='/x/'>x</a></body></html>"
    assert moved.redirects == ((301, f"{local_site}/regi"),)
    assert (broken.status, broken.final_url) == (404, f"{local_site}/sehol/")
    assert broken.redirects == ((301, f"{local_site}/torott"),)


async def test_redirect_chain_in_order_and_final_headers(local_site):
    async with Renderer(concurrency=1, render_timeout=5.0) as renderer:
        chained = await renderer.render(f"{local_site}/lanc")
        direct = await renderer.render(f"{local_site}/uj/")
    assert (chained.status, chained.final_url) == (200, f"{local_site}/uj/")
    assert chained.redirects == ((302, f"{local_site}/lanc"), (301, f"{local_site}/koztes"))
    assert direct.redirects == ()
    expected = {key.lower(): value for key, value in UJ_HEADERS.items()}
    assert expected.items() <= chained.headers.items()
    assert all(key == key.lower() for key in chained.headers)


# ---------------------------------------------------------------------------
# élő füstpróba (csak `pytest -m live`)
# ---------------------------------------------------------------------------


def internal_links(html, base, policy):
    found = set()
    for href in links(html):
        url = urljoin(base, href)
        if is_internal(url, policy) and (target := normalize(url, policy)):
            found.add(target)
    return found


@pytest.mark.live
async def test_live_ngx_bootstrap_links_appear_after_render():
    seed = "https://valor-software.com/ngx-bootstrap/components"
    async with Renderer(concurrency=1) as renderer:
        result = await renderer.render(seed)
    assert result.raw_html is not None, result.error
    policy = UrlPolicy.from_seed(seed)
    raw = internal_links(result.raw_html.decode("utf-8", "replace"), result.final_url, policy)
    rendered = internal_links(result.rendered_html, result.final_url, policy)
    hash_links = [href for href in links(result.rendered_html) if "#/" in href]
    print(
        f"\nngx-bootstrap: status={result.status} error={result.error} "
        f"final_url={result.final_url} render_ms={result.render_ms} raw={len(raw)} "
        f"rendered={len(rendered)} belső link, hash-link={len(hash_links)}"
    )
    assert (result.status, result.error) == (200, None)
    assert len(raw) * 5 <= len(rendered)
    assert len(rendered) >= 30
    assert hash_links == []


@pytest.mark.live
async def test_live_kk_coach_server_rendered_links_survive_render():
    seed = "https://kk.coach/"
    async with Renderer(concurrency=1) as renderer:
        result = await renderer.render(seed)
    policy = UrlPolicy.from_seed(seed)
    raw = internal_links(result.raw_html.decode("utf-8", "replace"), result.final_url, policy)
    rendered = internal_links(result.rendered_html, result.final_url, policy)
    print(
        f"\nkk.coach: status={result.status} error={result.error} final_url={result.final_url} "
        f"render_ms={result.render_ms} raw={len(raw)} rendered={len(rendered)} belső link"
    )
    assert (result.status, result.error) == (200, None)
    assert len(raw) > 0
    assert raw <= rendered


# ---------------------------------------------------------------------------
# beragadó frame és biztonsági háló
# ---------------------------------------------------------------------------

STUCK_FRAME_PAGE = """<html><body><h1>Keret</h1>
<iframe src="https://kk.test/soha"></iframe>
<p>tartalom</p></body></html>"""

# A látómezőn kívüli lusta iframe-nek nincs dokumentuma, amíg nem görgetnek oda: a hálózat
# csendes (networkidle teljesül), de a frame-en a locator-hívás határidő nélkül várna.
LAZY_FRAME_PAGE = """<html><body><h1>Térkép lent</h1>
<div style="height:6000px">hosszú</div>
<iframe loading="lazy" src="https://kk.test/terkep"></iframe></body></html>"""


async def test_lazy_frame_without_document_does_not_hang_consent(make_renderer):
    renderer, _ = await make_renderer({
        "https://kk.test/": LAZY_FRAME_PAGE,
        "https://kk.test/terkep": "<html><body>térkép</body></html>",
    }, render_timeout=5.0)
    result = await asyncio.wait_for(renderer.render("https://kk.test/"), timeout=60)
    assert (result.status, result.error) == (200, None)
    assert result.render_ms < 12_000


async def test_pending_frame_does_not_hang_render(make_renderer):
    release = asyncio.Event()

    async def never(request):
        await release.wait()
        return "<html><body>késő</body></html>"

    renderer, _ = await make_renderer(
        {"https://kk.test/": STUCK_FRAME_PAGE, "https://kk.test/soha": never}, render_timeout=5.0)
    try:
        result = await asyncio.wait_for(renderer.render("https://kk.test/"), timeout=30)
    finally:
        release.set()
    assert (result.status, result.error) == (200, None)
    assert result.render_ms < 15_000
    await asyncio.sleep(0.1)


async def test_hard_timeout_returns_and_replaces_context(make_renderer, monkeypatch):
    renderer, _ = await make_renderer(
        {"https://kk.test/": "<html><body>ok</body></html>"}, render_timeout=1.0)
    original = render_module._accept_consent

    async def stuck(page, texts, deadline):
        await asyncio.Event().wait()

    monkeypatch.setattr(render_module, "_accept_consent", stuck)
    before = list(renderer._pool._queue)
    result = await asyncio.wait_for(renderer.render("https://kk.test/"), timeout=30)
    assert (result.status, result.error, result.attempts) == (None, "hard_timeout", 1)
    after = list(renderer._pool._queue)
    assert len(after) == 1 and after[0] is not before[0]
    monkeypatch.setattr(render_module, "_accept_consent", original)
    healthy = await asyncio.wait_for(renderer.render("https://kk.test/"), timeout=30)
    assert (healthy.status, healthy.error) == (200, None)
