"""Parse: táblás esetek beágyazott HTML-lel, és a három referencia-site egy-egy oldala a
rögzített fixture-ből (`tests/fixtures/pages/`, felvétel: `pytest -m live -k record_parse`)."""
import json

import pytest
from selectolax.parser import HTMLParser

from aaa2.engine.language import detect_language
from aaa2.engine.normalize import UrlPolicy
from aaa2.engine.parse import (
    extract_main_content,
    is_noindex,
    link_position,
    parse_page,
    schema_blocks,
)
from aaa2.engine.render import Renderer
from tests.recorded import load_page, save_page

SEED = "https://kk.coach/"
POLICY = UrlPolicy.from_seed(SEED, https_redirect=True, trailing_slash=True)
WORDS = " ".join(["szó"] * 120)


def page(body, head="", url=SEED, headers=None, lang="hu"):
    html = f'<html lang="{lang}"><head>{head}</head><body>{body}</body></html>'
    return parse_page(html, url, POLICY, headers)


# ---------------------------------------------------------------------------
# link-pozíció
# ---------------------------------------------------------------------------

POSITIONS = [
    ("nav elem", "<nav><ul><li><a href='/x/'>x</a></li></ul></nav>", "nav"),
    ("site header", "<header><a href='/x/'>x</a></header>", "nav"),
    ("site footer", "<footer><p><a href='/x/'>x</a></p></footer>", "footer"),
    ("aside elem", "<aside><a href='/x/'>x</a></aside>", "aside"),
    ("main tartalom", "<main><p><a href='/x/'>x</a></p></main>", "body"),
    ("article header nem site header", "<article><header><a href='/x/'>x</a></header></article>",
     "body"),
    ("article footer nem site footer", "<article><footer><a href='/x/'>x</a></footer></article>",
     "body"),
    ("main-en belüli header", "<main><header><a href='/x/'>x</a></header></main>", "body"),
    ("role navigation", "<div role='navigation'><a href='/x/'>x</a></div>", "nav"),
    ("role banner", "<div role='banner'><a href='/x/'>x</a></div>", "nav"),
    ("role contentinfo", "<div role='contentinfo'><a href='/x/'>x</a></div>", "footer"),
    ("role complementary", "<div role='complementary'><a href='/x/'>x</a></div>", "aside"),
    ("legközelebbi landmark nyer", "<footer><nav><a href='/x/'>x</a></nav></footer>", "nav"),
    ("nav-on belüli footer nem landmark", "<nav><div><footer><a href='/x/'>x</a></footer></div></nav>",
     "nav"),
    ("landmark előbb, mint heurisztika", "<footer><div class='menu'><a href='/x/'>x</a></div></footer>",
     "footer"),
    ("heurisztika: footer osztály", "<div class='site-footer'><a href='/x/'>x</a></div>", "footer"),
    ("heurisztika: sidebar id", "<div id='sidebar'><a href='/x/'>x</a></div>", "aside"),
    ("heurisztika: sidebar custom elem", "<sidebar class='sidebar'><a href='/x/'>x</a></sidebar>",
     "aside"),
    ("heurisztika: menü", "<div class='main-menu'><a href='/x/'>x</a></div>", "nav"),
    ("heurisztika: breadcrumb", "<div class='breadcrumbs'><a href='/x/'>x</a></div>", "nav"),
    ("heurisztika: footer a menü előtt", "<div class='footer-menu'><a href='/x/'>x</a></div>",
     "footer"),
    ("heurisztika: sidebar a menü előtt", "<div class='sidebar-menu'><a href='/x/'>x</a></div>",
     "aside"),
    ("heurisztika: entry-header cikkben body",
     "<article><div class='entry-header'><a href='/x/'>x</a></div></article>", "body"),
    ("heurisztika: menü cikkben is nav", "<article><div class='toc-menu'><a href='/x/'>x</a></div></article>",
     "nav"),
    ("body osztálya nem számít", "<div><a href='/x/'>x</a></div>", "body"),
    ("semmi jel", "<div class='gb-element-1'><p><a href='/x/'>x</a></p></div>", "body"),
]


@pytest.mark.parametrize(("body", "expected"), [p[1:] for p in POSITIONS], ids=[p[0] for p in POSITIONS])
def test_link_position(body, expected):
    html = f"<html><body class='home no-sidebar right-sidebar has-menu'>{body}</body></html>"
    anchor = HTMLParser(html).css_first("a")
    assert link_position(anchor) == expected


# ---------------------------------------------------------------------------
# linkek
# ---------------------------------------------------------------------------


def test_links_internal_normalized_in_dom_order():
    parsed = page("""
      <a href="/b">B</a>
      <a href="http://www.kk.coach/a/?utm_source=x#top">A</a>
      <a href="https://kk.coach/b/">B újra</a>
      <a href="#szekcio">ugrás</a>
      <a href="">üres</a>
      <a href="https://en.kk.coach/">aldomain</a>
    """)
    assert [link.to_url for link in parsed.links] == [
        "https://kk.coach/b/", "https://kk.coach/a/", "https://kk.coach/b/",
        "https://kk.coach/", "https://kk.coach/", "https://en.kk.coach/",
    ]
    assert [link.ordinal for link in parsed.links] == [1, 2, 3, 4, 5, 6]


def test_external_links_only_counted():
    parsed = page("""
      <a href="https://example.com/">külső</a>
      <a href="//example.org/x">protokoll-relatív</a>
      <a href="https://cdn.kk.coach/f.pdf">kizárt aldomain</a>
      <a href="mailto:a@kk.coach">levél</a>
      <a href="tel:+3630">telefon</a>
      <a href="javascript:void(0)">js</a>
      <a href="ftp://kk.coach/x">ftp</a>
      <a href="/belso/">belső</a>
    """)
    assert parsed.external_link_count == 3
    assert [link.to_url for link in parsed.links] == ["https://kk.coach/belso/"]


def test_base_href_resolves_relative_links():
    parsed = page("<a href='cikk/'>c</a>", head="<base href='https://kk.coach/blog/'>",
                  url="https://kk.coach/hu/")
    assert parsed.links[0].to_url == "https://kk.coach/blog/cikk/"


def test_noscript_and_template_links_skipped():
    parsed = page("""
      <noscript><a href="/ns/">ns</a></noscript>
      <template><a href="/tpl/">tpl</a></template>
      <a href="/el/">él</a>
    """)
    assert [link.to_url for link in parsed.links] == ["https://kk.coach/el/"]


ANCHORS = [
    ("szöveg", "<a href='/x/'>  Rólam \n oldal </a>", "Rólam oldal"),
    ("beágyazott szöveg", "<a href='/x/'><span>Szolgál</span><b>tatások</b></a>", "Szolgál tatások"),
    ("kép alt", "<a href='/x/'><img src='a.png' alt=''><img src='b.png' alt=' Logó '></a>", "Logó"),
    ("aria-label", "<a href='/x/' aria-label='Kezdőlap'><svg></svg></a>", "Kezdőlap"),
    ("szöveg az alt előtt", "<a href='/x/'><img alt='kép'>Szöveg</a>", "Szöveg"),
    ("semmi", "<a href='/x/'><svg></svg></a>", None),
]


@pytest.mark.parametrize(("body", "expected"), [a[1:] for a in ANCHORS], ids=[a[0] for a in ANCHORS])
def test_anchor_text(body, expected):
    assert page(body).links[0].anchor == expected


def test_nofollow_from_rel():
    parsed = page("<a href='/a/' rel='NoFollow noopener'>a</a><a href='/b/' rel='ugc'>b</a>")
    assert [link.nofollow for link in parsed.links] == [True, False]


# ---------------------------------------------------------------------------
# headingek és meta
# ---------------------------------------------------------------------------


def test_headings_in_dom_order():
    parsed = page("""
      <h2>Kettő</h2><h1> Egy <br>első </h1><section><h3>Három</h3></section>
      <h1></h1><noscript><h2>rejtett</h2></noscript><h6>Hat</h6>
    """)
    assert [(h.level, h.text, h.ordinal) for h in parsed.headings] == [
        (2, "Kettő", 1), (1, "Egy első", 2), (3, "Három", 3), (1, "", 4), (6, "Hat", 5),
    ]
    assert parsed.h1 == "Egy első"


def test_h1_missing_is_none():
    assert page("<h2>csak kettes</h2>").h1 is None


def test_meta_fields():
    parsed = page(
        "<svg><title>ikon</title></svg>",
        head="""<title> Rólam  | KK </title>
        <meta NAME="Description" content=" Leírás. ">
        <link rel="canonical" href="/rolam/">
        <link rel="alternate" hreflang="hu" href="/hu/rolam/">
        <link rel="alternate" hreflang="x-default" href="https://kk.coach/rolam/">
        <link rel="alternate" type="application/rss+xml" href="/feed/">""",
        url="https://kk.coach/rolam/",
    )
    assert parsed.title == "Rólam | KK"
    assert parsed.meta_description == "Leírás."
    assert parsed.canonical == "https://kk.coach/rolam/"
    assert parsed.hreflang == ("hu|https://kk.coach/hu/rolam/", "x-default|https://kk.coach/rolam/")
    assert parsed.lang == "hu"


def test_title_only_from_head():
    assert page("<svg><title>ikon</title></svg>").title is None


def test_hreflang_needs_rel_alternate():
    head = "<link rel='preload' hreflang='de' href='/de/'><link rel='alternate' hreflang='hu' href='/hu/'>"
    assert page("", head=head).hreflang == ("hu|https://kk.coach/hu/",)


def test_missing_and_empty_meta():
    assert page("").meta_description is None
    assert page("", head="<meta name='description' content=''>").meta_description == ""
    assert page("").title is None
    assert page("").canonical is None


HU_TEXT = "Ez egy magyar szöveg, amely azt mutatja, hogy a weboldal nem angol. " * 5
EN_TEXT = "This is the page that shows which language the website uses for their content. " * 5


@pytest.mark.parametrize(("text", "expected"), [
    (HU_TEXT, "hu"), (EN_TEXT, "en"), ("12345 67890 !!!", None), ("", None),
    ("Budapest Csávoly Kecskemét " * 10, None),
])
def test_detect_language(text, expected):
    assert detect_language(text) == expected


def test_lang_falls_back_to_detection():
    html = f"<html><body><main><p>{HU_TEXT * 3}</p></main></body></html>"
    assert parse_page(html, SEED, POLICY).lang == "hu"


NOINDEX = [
    ("nincs jel", "", {}, False),
    ("meta noindex", "<meta name='robots' content='noindex, follow'>", {}, True),
    ("meta nagybetűvel", "<meta NAME='Robots' CONTENT='NoIndex'>", {}, True),
    ("meta none", "<meta name='robots' content='none'>", {}, True),
    ("googlebot meta", "<meta name='googlebot' content='noindex'>", {}, True),
    ("más bot meta", "<meta name='bingbot' content='noindex'>", {}, False),
    ("meta index", "<meta name='robots' content='index, follow, max-snippet:-1'>", {}, False),
    ("fejléc noindex", "", {"x-robots-tag": "noindex"}, True),
    ("fejléc googlebot", "", {"x-robots-tag": "googlebot: noindex, nofollow"}, True),
    ("fejléc más bot", "", {"x-robots-tag": "bingbot: noindex"}, False),
    ("fejléc értékes direktíva", "", {"x-robots-tag": "max-snippet: 50, unavailable_after: 2027"}, False),
    ("értékes direktíva után noindex", "", {"x-robots-tag": "max-snippet: 50, noindex"}, True),
    ("fejléc több sor", "", {"x-robots-tag": "otherbot: noindex\nnone"}, True),
]


@pytest.mark.parametrize(("head", "headers", "expected"), [n[1:] for n in NOINDEX], ids=[n[0] for n in NOINDEX])
def test_noindex(head, headers, expected):
    tree = HTMLParser(f"<html><head>{head}</head><body></body></html>")
    assert is_noindex(tree, headers) is expected
    assert page("", head=head, headers=headers).noindex is expected


# ---------------------------------------------------------------------------
# schema_blocks
# ---------------------------------------------------------------------------


def ld(data):
    text = data if isinstance(data, str) else json.dumps(data)
    return f'<script type="application/ld+json">{text}</script>'


def test_schema_blocks_split_and_typed():
    html = "".join([
        ld({"@context": "https://schema.org", "@type": "Organization", "name": "KK"}),
        ld({"@context": "https://schema.org", "@graph": [
            {"@type": "WebSite", "name": "KK"}, {"@type": ["Person", "Author"], "name": "K"},
        ]}),
        ld([{"@type": "BreadcrumbList"}, {"@graph": [{"@type": "FAQPage"}]}]),
        ld('{"@type": "Broken",}'),
        ld("   "),
        '<script type="application/ld+json; charset=utf-8">{"@type": "Event"}</script>',
        '<script type="application/json">{"@type": "NotLd"}</script>',
        '<script>var x = {"@type": "Js"};</script>',
    ])
    blocks = schema_blocks(HTMLParser(f"<html><head>{html}</head></html>"))
    assert [(b.type, b.ordinal) for b in blocks] == [
        ("Organization", 1), ("WebSite", 2), ("Person,Author", 3), ("BreadcrumbList", 4),
        ("FAQPage", 5), ("invalid", 6), ("Event", 7),
    ]
    assert json.loads(blocks[1].json) == {"@type": "WebSite", "name": "KK"}
    assert blocks[5].json == '{"@type": "Broken",}'


def test_schema_block_without_type():
    blocks = schema_blocks(HTMLParser(ld({"name": "típus nélkül"})))
    assert [(b.type, json.loads(b.json)) for b in blocks] == [(None, {"name": "típus nélkül"})]


# ---------------------------------------------------------------------------
# main content
# ---------------------------------------------------------------------------

LONG = " ".join(f"mondat{i}" for i in range(150))
SHORT = "csak néhány szó"

MAIN_CONTENT = [
    ("main", f"<nav>{LONG} menü</nav><main><p>{LONG}</p></main>", "semantic_main", "menü"),
    ("rövid main után article", f"<main>{SHORT}</main><article><p>{LONG}</p></article>",
     "semantic_article", SHORT),
    ("role=main", f"<div role='main'><p>{LONG}</p></div>", "role_main", None),
    ("content-szelektor", f"<div class='entry-content'><p>{LONG}</p></div>", "content_selector", None),
    ("readability: a hosszabb, de linksűrű blokk veszít",
     (f"<div class='x'>{''.join(f'<a href=/{i}>navlink{i} {LONG[:60]}</a>' for i in range(80))}</div>"
      f"<div class='y'><p>{LONG}</p><p>{LONG}</p></div>"), "readability", "navlink"),
    ("tartalék: body a keretek nélkül",
     f"<header>{WORDS} fejléc</header><p>{SHORT}</p><footer>{WORDS} lábléc</footer>",
     "fallback_body", "lábléc"),
]


@pytest.mark.parametrize(("body", "method", "absent"), [m[1:] for m in MAIN_CONTENT],
                         ids=[m[0] for m in MAIN_CONTENT])
def test_main_content_strategy(body, method, absent):
    text, used = extract_main_content(f"<html><body>{body}</body></html>")
    assert used == method
    if absent:
        assert absent not in text


def test_main_content_filters_noise():
    body = f"""<main><p>{LONG}</p>
      <script>var titok = 1;</script><style>.x{{}}</style>
      <div style="display: none">rejtett</div>
      <div class="cookie-notice">süti</div><div id="gdpr-consent">hozzájárulás</div>
    </main>"""
    text, method = extract_main_content(f"<html><body>{body}</body></html>")
    assert method == "semantic_main"
    for noise in ("titok", "rejtett", "süti", "hozzájárulás"):
        assert noise not in text


def test_word_count_from_main_content():
    parsed = page(f"<nav>{WORDS}</nav><main><p>{LONG}</p></main>")
    assert parsed.word_count == 150
    assert parsed.main_content_method == "semantic_main"


# ---------------------------------------------------------------------------
# referencia-oldalak a rögzített fixture-ből
# ---------------------------------------------------------------------------

REFERENCE_PAGES = {
    "kk-coach-home": "https://kk.coach/",
    "materia-home": "https://materia-tm.com/",
    "ngx-bootstrap-components": "https://valor-software.com/ngx-bootstrap/components",
}


def reference(name):
    snapshot = load_page(name)
    if snapshot is None:
        pytest.skip(f"nincs felvétel: pytest -m live -k record_parse ({name})")
    policy = UrlPolicy.from_seed(REFERENCE_PAGES[name])
    return parse_page(snapshot["rendered_html"], snapshot["final_url"], policy, snapshot["headers"])


def by_url(parsed):
    found = {}
    for link in parsed.links:
        found.setdefault(link.to_url, []).append(link)
    return found


def test_reference_kk_coach_home():
    parsed = reference("kk-coach-home")
    assert (parsed.canonical, parsed.noindex, parsed.lang) == ("https://kk.coach/", False, "en-US")
    assert set(parsed.hreflang) == {"en|https://kk.coach/", "hu|https://kk.coach/hu/"}
    assert parsed.h1 and sum(h.level == 1 for h in parsed.headings) == 1
    assert parsed.main_content_method == "semantic_main" and parsed.word_count > 100
    assert parsed.external_link_count == 0
    links = by_url(parsed)
    assert [(link.position, link.anchor) for link in links["https://kk.coach/about/"]] == [
        ("nav", "About")]
    assert [link.position for link in links["https://kk.coach/solutions/seo/"]] == ["nav", "body"]
    assert [(link.position, link.anchor) for link in links["https://kk.coach/hu/"]] == [
        ("nav", "Magyar")]
    assert {"footer", "aside"}.isdisjoint(link.position for link in parsed.links)
    types = [block.type for block in parsed.schema_blocks]
    assert {"Organization", "Person", "WebSite", "WebPage"} <= set(types)
    assert types.count("SiteNavigationElement") == 10


def test_reference_materia_home():
    parsed = reference("materia-home")
    assert parsed.h1 is None
    assert parsed.meta_description is None
    assert (parsed.canonical, parsed.lang) == ("https://materia-tm.com/", "en-US")
    assert {pair.split("|")[0] for pair in parsed.hreflang} == {"en", "hu", "it", "x-default"}
    assert parsed.schema_blocks == ()
    links = by_url(parsed)
    assert [link.position for link in links["https://materia-tm.com/menu/"]] == ["nav", "nav"]
    assert [(link.position, link.anchor) for link in links["https://materia-tm.com/privacy-policy/"]] \
        == [("footer", "GDPR")]
    assert {link.anchor for link in links["https://materia-tm.com/hu/"]} == {"Hungarian"}
    assert all("cdn-cgi" not in url for url in links)


def test_reference_ngx_bootstrap_components():
    parsed = reference("ngx-bootstrap-components")
    assert (parsed.h1, parsed.lang, parsed.canonical) == ("All Components", "en", None)
    assert len(parsed.links) == 53 and len(by_url(parsed)) == 53
    assert sum(link.position == "aside" for link in parsed.links) == 51
    assert "https://valor-software.com/ngx-bootstrap/%5B''%5D" in by_url(parsed)
    assert all("#" not in link.to_url for link in parsed.links)


@pytest.mark.live
async def test_live_record_parse_fixtures():
    async with Renderer(concurrency=3) as renderer:
        for name, url in REFERENCE_PAGES.items():
            result = await renderer.render(url)
            assert (result.status, result.error) == (200, None), name
            save_page(name, result)
