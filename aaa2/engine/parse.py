"""Parse: renderelt DOM → a `pages`, `links`, `headings` és `schema_blocks` sorainak tartalma.

Tiszta függvény, adatbázist nem ír; az oldalankénti tranzakció a crawl dolga.

- `pages`: title (`head > title`), meta description, canonical (abszolút URL, nem
  normalizálva), noindex (meta robots / googlebot és az X-Robots-Tag fejléc), első H1,
  `html[lang]` (ha nincs, nyelvdetekció a main contentből), hreflang `"lang|url"` párokként,
  main content és szószám, külső linkek száma.
- `headings`: h1–h6 DOM-sorrendben, `ordinal` 1-től.
- `links`: minden `<a href>` belső célra, normalizálva, DOM-sorrendben. Anchor: a szöveg,
  ha üres, az első nem üres `img[alt]`, ha az is, az `aria-label`. Pozíció: a legközelebbi
  landmark-ős (`nav`, `aside`, `role`, valamint `header` / `footer`, ha nem sectioning
  elemen belül áll: article, aside, main, nav, section); landmark nélkül az ősök class- és
  id-tokenjei (a body és a html kivételével); különben body. `nofollow` a `rel`-ből. A `<noscript>` és `<template>`
  tartalma kimarad. A külső http(s) link csak számolva van.
- `schema_blocks`: minden `application/ld+json`; tömb és `@graph` elemenként, `@type`-pal
  (több típus vesszővel); a `@graph` eleme megkapja a szülő `@context`-jét, ha nincs sajátja;
  hibás JSON `type = 'invalid'`, nyers szöveggel.
- main content: öt stratégia sorban, az első, ami 100 szó fölött ad: `main`, `article`,
  `[role=main]`, ismert tartalom-szelektorok, readability-pontszám (szöveg / link arány a
  bekezdéssűrűséggel súlyozva) a `div` és `section` elemeken; végül a body a
  header / nav / footer / aside nélkül. Előtte kimarad a script, style, noscript, iframe,
  template, az inline rejtett elem és a cookie / consent banner.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

from aaa2.engine.language import detect_language
from aaa2.engine.normalize import UrlPolicy, is_internal, normalize

MIN_MAIN_CONTENT_WORDS = 100
HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

_SECTIONING = frozenset({"article", "aside", "main", "nav", "section"})
_ROLE_POSITIONS = {
    "navigation": "nav", "banner": "nav", "contentinfo": "footer", "complementary": "aside",
}
_TOKEN_POSITIONS = (
    ("footer", frozenset({"footer", "colophon"})),
    ("aside", frozenset({"sidebar", "aside"})),
    ("nav", frozenset({
        "nav", "navbar", "navigation", "menu", "header", "masthead", "topbar",
        "breadcrumb", "breadcrumbs",
    })),
)
_SCOPED_TOKENS = frozenset({"header", "masthead", "footer", "colophon"})
# A body és a html osztályai az oldal elrendezését írják le (pl. "no-sidebar"), nem a linkét.
_LAYOUT_ROOTS = frozenset({"body", "html"})
_SKIPPED_ANCESTORS = frozenset({"noscript", "template"})
_NON_PAGE_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "sms:", "fax:")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")

_NOISE_SELECTOR = "script, style, noscript, iframe, template"
_COOKIE_SELECTORS = (
    "#cookie-banner", ".cookie-notice", '[class*="cookie"]', '[id*="cookie"]',
    '[class*="consent"]', '[id*="consent"]', '[class*="gdpr"]',
)
_CONTENT_SELECTORS = (
    "#content", "#main", "#main-content", "#article", ".content", ".main-content",
    ".post-content", ".entry-content", ".article-body",
)
_ROBOTS_META_NAMES = frozenset({"robots", "googlebot"})
_NOINDEX_DIRECTIVES = frozenset({"noindex", "none"})
_DIRECTIVES_WITH_VALUE = frozenset({
    "max-snippet", "max-image-preview", "max-video-preview", "unavailable_after",
})


@dataclass(frozen=True)
class Link:
    to_url: str
    anchor: str | None
    position: str
    nofollow: bool
    ordinal: int


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    ordinal: int


@dataclass(frozen=True)
class SchemaBlock:
    type: str | None
    json: str
    ordinal: int


@dataclass(frozen=True)
class ParsedPage:
    title: str | None
    meta_description: str | None
    canonical: str | None
    noindex: bool
    h1: str | None
    lang: str | None
    hreflang: tuple[str, ...]
    main_content: str
    main_content_method: str
    word_count: int
    external_link_count: int
    links: tuple[Link, ...]
    headings: tuple[Heading, ...]
    schema_blocks: tuple[SchemaBlock, ...]


def parse_page(
    html: str, url: str, policy: UrlPolicy, headers: Mapping[str, str] | None = None
) -> ParsedPage:
    """Egy renderelt oldal. `url` a ténylegesen kiszolgált URL (a relatív linkek alapja,
    ha nincs `<base href>`); `headers` a végső válasz fejlécei kisbetűs kulcsokkal."""
    tree = HTMLParser(html or "")
    base = _base_url(tree, url)
    headings = _headings(tree)
    links, external = _links(tree, base, policy)
    main_content, method = extract_main_content(html or "")
    return ParsedPage(
        title=_text(tree.css_first("head > title")) or None,
        meta_description=_meta_content(tree, "description"),
        canonical=_link_href(tree, "canonical", base),
        noindex=is_noindex(tree, headers or {}),
        h1=next((h.text for h in headings if h.level == 1), None),
        lang=_html_lang(tree) or detect_language(main_content),
        hreflang=_hreflang(tree, base),
        main_content=main_content,
        main_content_method=method,
        word_count=len(main_content.split()),
        external_link_count=external,
        links=links,
        headings=headings,
        schema_blocks=schema_blocks(tree),
    )


def link_position(node: Node) -> str:
    """nav | footer | aside | body a link ősei alapján."""
    ancestors = list(_ancestors(node))
    for index, element in enumerate(ancestors):
        role = (element.attributes.get("role") or "").strip().lower()
        if role in _ROLE_POSITIONS:
            return _ROLE_POSITIONS[role]
        if element.tag in ("nav", "aside"):
            return element.tag
        if element.tag in ("header", "footer") and not _in_sectioning(ancestors[index + 1:]):
            return "nav" if element.tag == "header" else "footer"
    for index, element in enumerate(ancestors):
        if element.tag in _LAYOUT_ROOTS:
            continue
        tokens = _class_id_tokens(element)
        for position, markers in _TOKEN_POSITIONS:
            hits = tokens & markers
            if not hits:
                continue
            if hits <= _SCOPED_TOKENS and _in_sectioning(ancestors[index + 1:]):
                continue
            return position
    return "body"


def is_noindex(tree: HTMLParser, headers: Mapping[str, str]) -> bool:
    """noindex vagy none a robots / googlebot meta tagben vagy az X-Robots-Tag fejlécben.
    A más user-agentnek címzett X-Robots-Tag direktíva nem számít."""
    for meta in tree.css("meta[name]"):
        name = (meta.attributes.get("name") or "").strip().lower()
        directives = _directives(meta.attributes.get("content") or "")
        if name in _ROBOTS_META_NAMES and directives & _NOINDEX_DIRECTIVES:
            return True
    for value in (headers.get("x-robots-tag") or "").splitlines():
        scope, directives = _split_robots_scope(value)
        if scope in (None, "googlebot") and _directives(directives) & _NOINDEX_DIRECTIVES:
            return True
    return False


def schema_blocks(tree: HTMLParser) -> tuple[SchemaBlock, ...]:
    blocks: list[SchemaBlock] = []
    for script in tree.css("script[type]"):
        kind = (script.attributes.get("type") or "").split(";")[0].strip().lower()
        if kind != "application/ld+json":
            continue
        raw = (script.text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            blocks.append(SchemaBlock("invalid", raw, len(blocks) + 1))
            continue
        for item in _schema_items(data):
            blocks.append(SchemaBlock(
                _schema_type(item), json.dumps(item, ensure_ascii=False), len(blocks) + 1
            ))
    return tuple(blocks)


def extract_main_content(html: str) -> tuple[str, str]:
    """(szöveg, stratégia): semantic_main | semantic_article | role_main | content_selector |
    readability | fallback_body."""
    tree = _content_tree(html)
    for method, selector in (("semantic_main", "main"), ("semantic_article", "article")):
        candidates = tree.css(selector)
        if candidates:
            text = max((_text(node) for node in candidates), key=len)
            if len(text.split()) > MIN_MAIN_CONTENT_WORDS:
                return text, method
    for node in tree.css('[role="main"]'):
        text = _text(node)
        if len(text.split()) > MIN_MAIN_CONTENT_WORDS:
            return text, "role_main"
    for selector in _CONTENT_SELECTORS:
        text = _text(tree.css_first(selector))
        if len(text.split()) > MIN_MAIN_CONTENT_WORDS:
            return text, "content_selector"
    best_text, best_score = "", -1.0
    for node in tree.css("div, section"):
        text = _text(node)
        if len(text.split()) <= MIN_MAIN_CONTENT_WORDS:
            continue
        score = _readability_score(node, text)
        if score > best_score:
            best_text, best_score = text, score
    if best_text:
        return best_text, "readability"
    fallback = _content_tree(html)
    for node in fallback.css("header, nav, footer, aside"):
        node.decompose()
    return _text(fallback.css_first("body")), "fallback_body"


def _links(tree: HTMLParser, base: str, policy: UrlPolicy) -> tuple[tuple[Link, ...], int]:
    links: list[Link] = []
    external = 0
    for anchor in tree.css("a[href]"):
        if any(a.tag in _SKIPPED_ANCESTORS for a in _ancestors(anchor)):
            continue
        href = (anchor.attributes.get("href") or "").strip()
        if href.lower().startswith(_NON_PAGE_SCHEMES):
            continue
        target = urljoin(base, href)
        if urlsplit(target).scheme.lower() not in ("http", "https"):
            continue
        if not is_internal(target, policy):
            external += 1
            continue
        normalized = normalize(target, policy)
        if normalized is None:
            continue
        rel = set((anchor.attributes.get("rel") or "").lower().split())
        links.append(Link(
            to_url=normalized,
            anchor=_anchor_text(anchor),
            position=link_position(anchor),
            nofollow="nofollow" in rel,
            ordinal=len(links) + 1,
        ))
    return tuple(links), external


def _headings(tree: HTMLParser) -> tuple[Heading, ...]:
    root = tree.root
    if root is None:
        return ()
    headings: list[Heading] = []
    for node in root.traverse():
        if node.tag in HEADING_TAGS and not any(
            a.tag in _SKIPPED_ANCESTORS for a in _ancestors(node)
        ):
            headings.append(Heading(int(node.tag[1]), _text(node), len(headings) + 1))
    return tuple(headings)


def _anchor_text(anchor: Node) -> str | None:
    text = _text(anchor)
    if text:
        return text
    for image in anchor.css("img[alt]"):
        alt = _WHITESPACE.sub(" ", image.attributes.get("alt") or "").strip()
        if alt:
            return alt
    label = _WHITESPACE.sub(" ", anchor.attributes.get("aria-label") or "").strip()
    return label or None


def _base_url(tree: HTMLParser, url: str) -> str:
    base = tree.css_first("base[href]")
    href = (base.attributes.get("href") or "").strip() if base is not None else ""
    return urljoin(url, href) if href else url


def _meta_content(tree: HTMLParser, name: str) -> str | None:
    for meta in tree.css("meta[name]"):
        if (meta.attributes.get("name") or "").strip().lower() == name:
            return (meta.attributes.get("content") or "").strip()
    return None


def _link_href(tree: HTMLParser, rel: str, base: str) -> str | None:
    for link in tree.css("link[rel][href]"):
        if rel in (link.attributes.get("rel") or "").lower().split():
            return urljoin(base, (link.attributes.get("href") or "").strip())
    return None


def _hreflang(tree: HTMLParser, base: str) -> tuple[str, ...]:
    pairs = []
    for link in tree.css("link[hreflang][href]"):
        if "alternate" not in (link.attributes.get("rel") or "").lower().split():
            continue
        language = (link.attributes.get("hreflang") or "").strip()
        href = urljoin(base, (link.attributes.get("href") or "").strip())
        pairs.append(f"{language}|{href}")
    return tuple(pairs)


def _html_lang(tree: HTMLParser) -> str | None:
    html = tree.css_first("html")
    lang = (html.attributes.get("lang") or "").strip() if html is not None else ""
    return lang or None


def _directives(content: str) -> set[str]:
    return {d.strip().lower() for d in content.split(",") if d.strip()}


def _split_robots_scope(value: str) -> tuple[str | None, str]:
    """('googlebot', 'noindex') a 'googlebot: noindex'-ből; (None, érték), ha nincs címzett."""
    head, sep, rest = value.partition(":")
    name = head.strip().lower()
    if sep and name and " " not in name and "," not in name and name not in _DIRECTIVES_WITH_VALUE:
        return name, rest
    return None, value


def _schema_items(data: object, context: object = None) -> Iterator[object]:
    if isinstance(data, list):
        for item in data:
            yield from _schema_items(item, context)
    elif isinstance(data, dict) and isinstance(data.get("@graph"), list):
        for item in data["@graph"]:
            yield from _schema_items(item, data.get("@context", context))
    elif context is not None and isinstance(data, dict) and "@context" not in data:
        yield {"@context": context, **data}
    else:
        yield data


def _schema_type(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("@type")
    if isinstance(kind, list):
        return ",".join(str(k) for k in kind) or None
    return str(kind) if kind is not None else None


def _content_tree(html: str) -> HTMLParser:
    tree = HTMLParser(html)
    for node in tree.css(_NOISE_SELECTOR):
        node.decompose()
    for node in tree.css("[style]"):
        style = (node.attributes.get("style") or "").lower().replace(" ", "")
        if "display:none" in style or "visibility:hidden" in style:
            node.decompose()
    for selector in _COOKIE_SELECTORS:
        for node in tree.css(selector):
            node.decompose()
    return tree


def _readability_score(node: Node, text: str) -> float:
    links = len(node.css("a"))
    paragraphs = len(node.css("p"))
    descendants = len(node.css("*")) or 1
    return len(text) / (links + 1) * (1.0 + paragraphs / descendants)


def _ancestors(node: Node) -> Iterator[Node]:
    parent = node.parent
    while parent is not None and parent.tag not in (None, "-undef"):
        yield parent
        parent = parent.parent


def _in_sectioning(ancestors: list[Node]) -> bool:
    return any(a.tag in _SECTIONING for a in ancestors)


def _class_id_tokens(node: Node) -> set[str]:
    raw = f"{node.attributes.get('class') or ''} {node.attributes.get('id') or ''}".lower()
    return {token for token in _TOKEN_SPLIT.split(raw) if token}


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return _WHITESPACE.sub(" ", node.text(separator=" ", strip=True) or "").strip()
