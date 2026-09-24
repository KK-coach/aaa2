"""Async SEO/GEO HTML crawler.

Fetches a URL with httpx, parses it with selectolax, and returns a structured
dict of technical, content, schema, link, image, and JS-rendering signals.

Pure Python utility — safe to import from any ADK agent.
"""

from __future__ import annotations

import json
import logging
import re
import time
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)

# Chrome desktop UA so servers return the same markup a real browser would get.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT_SECONDS = 30.0
MAX_REDIRECTS = 5

# AAA-179: deterministic crawl Accept-Language from the audit's requested locale.
# Hard-set per audit; NEVER an ambient/environment default. Extensible.
ACCEPT_LANGUAGE_BY_LOCALE = {
    "hu": "hu-HU,hu;q=0.9",
    "en": "en-US,en;q=0.9",
    "de": "de-DE,de;q=0.9",
    "es": "es-ES,es;q=0.9",
}

# Module-global requested locale (mirrors reverse_engineering_agent.tools'
# _FORCED_CLIENT_AUDIT_ID pattern — SAFE under Cloud Run concurrency=1). Set once
# per audit by run_one(); read by crawl_html() / the Playwright escalation tool.
_REQUESTED_LOCALE: str | None = None


def _norm_locale(locale) -> str | None:
    s = (locale or "").strip().lower().replace("_", "-")
    return (s.split("-")[0] or None) if s else None


def set_requested_locale(locale: str | None) -> None:
    """Hard-set the audit's requested locale for the crawl fetch (AAA-179)."""
    global _REQUESTED_LOCALE
    _REQUESTED_LOCALE = _norm_locale(locale)


def get_requested_locale() -> str | None:
    return _REQUESTED_LOCALE


# AAA-282 S1-B — the separable locale contract for the audit. The audit's UI/report
# language, the crawl language, and HOW the crawl language was chosen are DISTINCT
# layers (browser/UI language ≠ crawl language ≠ detected page language ≠ report
# serving language). Set once per audit by run_one(); persisted on the audit doc by
# write_audit. Same module-global pattern as _REQUESTED_LOCALE (SAFE under Cloud Run
# concurrency=1). crawl_locale is None in "default" mode (neutral Accept-Language →
# the site serves its OWN default language; browser locale never silently leaks in).
_LOCALE_CONTEXT: dict = {}


def set_locale_context(ui_locale: str | None = None,
                       crawl_locale: str | None = None,
                       crawl_locale_mode: str | None = None) -> None:
    """Record the audit's resolved locale layers (AAA-282 S1-B). crawl_locale_mode:
    'default' (neutral Accept-Language, page default), 'explicit' (user chose a
    crawl language), or 'legacy_browser_locale' (old job: browser locale drove the
    crawl — preserved, never rewritten)."""
    global _LOCALE_CONTEXT
    _LOCALE_CONTEXT = {
        "ui_locale": _norm_locale(ui_locale),
        "crawl_locale": _norm_locale(crawl_locale),  # None in "default" mode
        "crawl_locale_mode": crawl_locale_mode or None,
    }


def get_locale_context() -> dict:
    return dict(_LOCALE_CONTEXT)


# AAA-255 — pre-flight crawl pass-through. The Gate-A reachability pre-flight in
# run_one fetches the client URL ONCE; it stashes the result here so the discovery
# phase's crawl_html(url) returns it instead of re-fetching (no double fetch). Same
# module-global / consume-once pattern as the locale + forced-audit-id seams (SAFE
# under Cloud Run concurrency=1). Keyed by exact URL; consumed once; a miss simply
# re-fetches (correctness-safe). Competitor crawls (other URLs) never match.
_PREFETCHED_CRAWL: dict = {}


def set_prefetched_crawl(url: str, result: dict) -> None:
    if url and isinstance(result, dict):
        _PREFETCHED_CRAWL[url] = result


def _consume_prefetched_crawl(url: str):
    return _PREFETCHED_CRAWL.pop(url, None)


def clear_prefetched_crawl() -> None:
    _PREFETCHED_CRAWL.clear()


def accept_language_for(locale: str | None) -> str | None:
    """BCP-47 Accept-Language header for a requested locale, or None if the
    locale is absent/unsupported (caller then OMITS the header — never injects
    en-US, so the site serves its own default)."""
    return ACCEPT_LANGUAGE_BY_LOCALE.get(_norm_locale(locale) or "")

_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


def _count_words(text: str) -> int:
    if not text:
        return 0
    return len(_WORD_RE.findall(text))


def _heading_text(node) -> str:
    """AAA-294 §12 — heading/inline text with spaces inserted at child-node, block and <br>
    boundaries. selectolax .text(strip=True) joins adjacent text nodes with NO separator
    ('London' + 'your team' -> 'Londonyour'); separator=' ' + whitespace-collapse fixes it
    while preserving punctuation and existing single spaces. Non-Latin/Hungarian safe."""
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.text(separator=" ", strip=True) or "").strip()


def _normalize_url(url: str) -> str:
    """Normalize for canonical self-reference comparison.

    Ignores scheme (http/https), a leading ``www.``, trailing slash, and case
    of host. Query string and fragment are dropped.
    """
    try:
        parsed = urlparse(url.strip())
    except (ValueError, AttributeError):
        return (url or "").strip().lower()

    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    if not path:
        path = "/"

    return f"{host}{path}"


def _extract_schema_types(node: object, acc: list[str]) -> None:
    """Recursively pull every ``@type`` value out of a parsed JSON-LD object.

    Handles single string types, arrays of types, nested objects, and
    ``@graph`` collections.
    """
    if isinstance(node, dict):
        type_val = node.get("@type")
        if isinstance(type_val, str):
            acc.append(type_val)
        elif isinstance(type_val, list):
            acc.extend(t for t in type_val if isinstance(t, str))
        for value in node.values():
            if isinstance(value, (dict, list)):
                _extract_schema_types(value, acc)
    elif isinstance(node, list):
        for item in node:
            _extract_schema_types(item, acc)


def _detect_spa_indicators(html: str, tree: HTMLParser) -> list[str]:
    indicators: list[str] = []

    # Next.js Pages Router emits __NEXT_DATA__ / #__next.
    if tree.css_first("#__next") or "__NEXT_DATA__" in html:
        indicators.append("Next.js")
    # Next.js App Router (RSC streaming) emits NONE of the above. Detect via
    # the streaming flush function, the route announcer, RSC payload markers,
    # or App-Router build chunk paths.
    app_router = (
        "self.__next_f" in html
        or tree.css_first("#__next-route-announcer__") is not None
        or tree.css_first("[data-precedence]") is not None
        or tree.css_first("[data-stack]") is not None
        or "_next/static/chunks/app/" in html
    )
    if app_router and "Next.js App Router" not in indicators:
        indicators.append("Next.js App Router")
    if tree.css_first("#__nuxt") or "__NUXT__" in html:
        indicators.append("Nuxt")

    root = tree.css_first("#root")
    if tree.css_first("[data-reactroot]") or root is not None:
        empty_root = root is not None and not (root.text(strip=True) or root.child)
        if "React" not in indicators:
            indicators.append("React (empty #root)" if empty_root else "React")

    app = tree.css_first("#app")
    if app is not None or tree.css_first("[data-server-rendered]"):
        if "Vue" not in indicators:
            indicators.append("Vue")
    # data-v-* scoped-style attributes are emitted by the Vue SFC compiler.
    if re.search(r"\sdata-v-[0-9a-f]{6,}", html) and "Vue" not in indicators:
        indicators.append("Vue")

    if tree.css_first("app-root") or re.search(r"\s_?ng[-\w]*=", html):
        if "Angular" not in indicators:
            indicators.append("Angular")

    return indicators


# Always removed before any main-content extraction strategy runs.
_NOISE_SELECTOR = "script, style, noscript, iframe, template"

# Cookie / consent banner selectors — stripped from the content tree and used
# to set boilerplate_detected.cookie_banner_detected.
_COOKIE_SELECTORS = (
    "#cookie-banner",
    ".cookie-notice",
    '[class*="cookie"]',
    '[id*="cookie"]',
    '[class*="consent"]',
    '[id*="consent"]',
    '[class*="gdpr"]',
)

# Strategy 3: common content containers, checked in this order.
_CONTENT_PATTERN_SELECTORS = (
    "#content",
    "#main",
    "#main-content",
    "#article",
    ".content",
    ".main-content",
    ".post-content",
    ".entry-content",
    ".article-body",
)

_MIN_WORDS = 100


def _normalize_ws(text: str) -> str:
    """Collapse all runs of whitespace to a single space."""
    return re.sub(r"\s+", " ", text or "").strip()


def _naive_words(text: str) -> int:
    """Word count = naive whitespace split (per spec for main_content)."""
    return len(text.split())


def _node_text(node) -> str:
    if node is None:
        return ""
    return _normalize_ws(node.text(separator=" ", strip=True) or "")


def _element_label(node) -> str | None:
    """Human-readable selector for the extracted element, e.g. ``div#content``."""
    if node is None:
        return None
    tag = node.tag or "?"
    node_id = (node.attributes.get("id") or "").strip()
    if node_id:
        return f"{tag}#{node_id}"
    cls = (node.attributes.get("class") or "").strip()
    if cls:
        return f"{tag}.{cls.split()[0]}"
    return tag


def _build_content_tree(raw_html: str) -> HTMLParser:
    """Fresh parse with universally-unwanted nodes removed.

    Strips scripts/styles/iframes, ``display:none`` elements, and cookie/consent
    banners. Structural elements (header/nav/footer/aside) are kept here — only
    the fallback strategy removes those.
    """
    tree = HTMLParser(raw_html)
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


def _mc_result(text: str, method: str, tag: str | None, confidence: float) -> dict:
    text = _normalize_ws(text)
    return {
        "extraction_method": method,
        "text": text,
        "chars": len(text),
        "words": _naive_words(text),
        "html_element_tag": tag,
        "confidence": confidence,
    }


def _readability_score(node) -> tuple[float, str]:
    """Heuristic score for a candidate block.

    Combines text-to-link density (high = content, low = nav) with paragraph
    density (proportion of descendants that are <p>).
    """
    text = _node_text(node)
    text_len = len(text)
    link_count = len(node.css("a"))
    p_count = len(node.css("p"))
    child_count = len(node.css("*")) or 1

    text_per_link = text_len / (link_count + 1)
    paragraph_density = p_count / child_count
    score = text_per_link * (1.0 + paragraph_density)
    return score, text


def _extract_main_content(raw_html: str) -> dict:
    """Run the 5 strategies in order; return the first that clears the bar."""
    tree = _build_content_tree(raw_html)

    # --- Strategy 1: semantic HTML5 (confidence 0.95) ---
    mains = tree.css("main")
    if mains:
        node = max(mains, key=lambda n: len(_node_text(n)))
        text = _node_text(node)
        if _naive_words(text) > _MIN_WORDS:
            return _mc_result(text, "semantic_html5", _element_label(node), 0.95)

    articles = tree.css("article")
    if articles:
        node = max(articles, key=lambda n: len(_node_text(n)))
        text = _node_text(node)
        if _naive_words(text) > _MIN_WORDS:
            return _mc_result(text, "semantic_html5", _element_label(node), 0.95)

    # --- Strategy 2: role-based (confidence 0.85) ---
    for node in tree.css('[role="main"]'):
        text = _node_text(node)
        if _naive_words(text) > _MIN_WORDS:
            return _mc_result(text, "semantic_html5", _element_label(node), 0.85)

    # --- Strategy 3: common id/class patterns (confidence 0.75) ---
    for selector in _CONTENT_PATTERN_SELECTORS:
        node = tree.css_first(selector)
        if node is None:
            continue
        text = _node_text(node)
        if _naive_words(text) > _MIN_WORDS:
            return _mc_result(text, "readability", _element_label(node), 0.75)

    # --- Strategy 4: readability-like heuristic (confidence 0.65) ---
    best_node = None
    best_text = ""
    best_score = -1.0
    for node in tree.css("div, section"):
        text = _node_text(node)
        if _naive_words(text) <= _MIN_WORDS:
            continue
        score, _ = _readability_score(node)
        if score > best_score:
            best_score, best_node, best_text = score, node, text
    if best_node is not None:
        return _mc_result(
            best_text, "largest_text_block", _element_label(best_node), 0.65
        )

    # --- Strategy 5: fallback — body minus structural boilerplate (0.30) ---
    fb_tree = _build_content_tree(raw_html)
    for node in fb_tree.css("header, nav, footer, aside"):
        node.decompose()
    body = fb_tree.css_first("body")
    return _mc_result(_node_text(body), "fallback_full_body", "body", 0.30)


def _detect_boilerplate(tree: HTMLParser) -> dict:
    has_cookie = any(
        tree.css_first(sel) is not None for sel in _COOKIE_SELECTORS
    )
    return {
        "header_present": tree.css_first("header") is not None,
        "nav_present": tree.css_first("nav") is not None,
        "footer_present": tree.css_first("footer") is not None,
        "sidebar_present": (
            tree.css_first("aside") is not None
            or tree.css_first('[role="complementary"]') is not None
        ),
        "cookie_banner_detected": has_cookie,
    }


# Googlebot indexes only the first 2 MiB of *uncompressed* HTML per page
# (Gary Illyes, developers.google.com/search/blog/2026/03/crawler-blog-post).
# gzip/Brotli does not help — the limit is on decoded bytes. External CSS/JS
# each get their own separate budget, but inline <script>/<style>/base64
# images count against this one.
_GOOGLEBOT_LIMIT_BYTES = 2_097_152  # 2 MiB, exact

# base64 payload inside an image data URI (the heavy part that bloats HTML).
_BASE64_IMG_RE = re.compile(
    r"data:image/[a-zA-Z0-9.+\-]+;base64,([A-Za-z0-9+/=]+)"
)


def _googlebot_index_limit(
    tree: HTMLParser,
    response: httpx.Response,
    raw_html: str,
    raw_html_bytes: int,
    full_visible_words: int,
) -> dict:
    limit = _GOOGLEBOT_LIMIT_BYTES

    # Inline assets that count against the budget (external files do not).
    inline_script_bytes = sum(
        len((s.text() or "").encode("utf-8"))
        for s in tree.css("script")
        if not s.attributes.get("src")
    )
    inline_style_bytes = sum(
        len((st.text() or "").encode("utf-8")) for st in tree.css("style")
    )
    inline_base64_image_bytes = sum(
        len(m.encode("utf-8")) for m in _BASE64_IMG_RE.findall(raw_html)
    )
    inline_contribution_bytes = (
        inline_script_bytes + inline_style_bytes + inline_base64_image_bytes
    )

    bytes_over_limit = max(0, raw_html_bytes - limit)
    exceeds_limit = raw_html_bytes > limit
    budget_used_percent = round(raw_html_bytes / limit * 100, 2)

    if exceeds_limit:
        risk_level = "critical"  # content past the cutoff is invisible to Google
    elif budget_used_percent >= 75:
        risk_level = "warning"  # approaching the cliff
    else:
        risk_level = "safe"

    # What Google never sees: parse only the first 2 MiB of decoded bytes and
    # diff it against the full document. Headings are prefix-stable, so any in
    # the full doc beyond the truncated count fall after the cutoff.
    cutoff_reached = exceeds_limit
    headings_after_cutoff: list[str] = []
    approx_words_after_cutoff = 0

    if cutoff_reached:
        encoding = response.encoding or "utf-8"
        truncated_html = response.content[:limit].decode(
            encoding, errors="ignore"
        )
        trunc_tree = HTMLParser(truncated_html)

        full_h = [
            n.text(strip=True)
            for n in tree.css("h1, h2, h3, h4, h5, h6")
            if n.text(strip=True)
        ]
        trunc_h = [
            n.text(strip=True)
            for n in trunc_tree.css("h1, h2, h3, h4, h5, h6")
            if n.text(strip=True)
        ]
        headings_after_cutoff = full_h[len(trunc_h):]

        for node in trunc_tree.css("script, style, noscript"):
            node.decompose()
        t_body = trunc_tree.css_first("body")
        trunc_text = (
            t_body.text(separator=" ", strip=True) if t_body else ""
        ) or ""
        trunc_words = _count_words(re.sub(r"\s+", " ", trunc_text))
        approx_words_after_cutoff = max(0, full_visible_words - trunc_words)

    return {
        "limit_bytes": limit,
        "raw_html_bytes": raw_html_bytes,
        "bytes_over_limit": bytes_over_limit,
        "budget_used_percent": budget_used_percent,
        "exceeds_limit": exceeds_limit,
        "risk_level": risk_level,
        "inline_script_bytes": inline_script_bytes,
        "inline_style_bytes": inline_style_bytes,
        "inline_base64_image_bytes": inline_base64_image_bytes,
        "inline_contribution_bytes": inline_contribution_bytes,
        "content_at_risk": {
            "cutoff_reached": cutoff_reached,
            "headings_after_cutoff": headings_after_cutoff,
            "approx_words_after_cutoff": approx_words_after_cutoff,
        },
    }


def _detect_i18n(tree: HTMLParser) -> dict:
    """Internationalisation signals consumed by the site_profile module."""
    html_node = tree.css_first("html")
    html_lang = (
        (html_node.attributes.get("lang") or "").strip() or None
        if html_node
        else None
    )

    cl_node = tree.css_first(
        'meta[http-equiv="content-language"], meta[name="content-language"]'
    )
    content_language = (
        (cl_node.attributes.get("content") or "").strip() or None
        if cl_node
        else None
    )

    og_node = tree.css_first('meta[property="og:site_name"]')
    og_site_name = (
        (og_node.attributes.get("content") or "").strip() or None
        if og_node
        else None
    )

    locale_node = tree.css_first('meta[property="og:locale"]')
    og_locale = (
        (locale_node.attributes.get("content") or "").strip() or None
        if locale_node
        else None
    )

    hreflang = []
    for link in tree.css('link[rel="alternate"][hreflang]'):
        hl = (link.attributes.get("hreflang") or "").strip()
        if hl:
            hreflang.append(
                {"hreflang": hl, "href": link.attributes.get("href") or ""}
            )

    return {
        "html_lang": html_lang,
        "content_language": content_language,
        "og_site_name": og_site_name,
        "og_locale": og_locale,
        "hreflang": hreflang,
    }


_BRAND_SCHEMA_TYPES = {"person", "organization", "localbusiness"}


def _walk_brand_entities(node, acc: list) -> None:
    """Collect Person/Organization/LocalBusiness names + identity fields
    from parsed JSON-LD (handles @graph, nested objects, arrays)."""
    if isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and x.lower() in _BRAND_SCHEMA_TYPES
               for x in types):
            name = node.get("name")
            if isinstance(name, str) and name.strip():
                def _flat(v):
                    if isinstance(v, str):
                        return v
                    if isinstance(v, dict):
                        return v.get("name") or v.get("@id") or ""
                    if isinstance(v, list):
                        return ", ".join(filter(None, (_flat(i) for i in v)))
                    return ""
                acc.append({
                    "type": next((x for x in types
                                  if isinstance(x, str)
                                  and x.lower() in _BRAND_SCHEMA_TYPES), "?"),
                    "name": name.strip(),
                    "url": node.get("url") if isinstance(
                        node.get("url"), str) else "",
                    "sameAs": _flat(node.get("sameAs")),
                    "founder": _flat(node.get("founder")),
                    "author": _flat(node.get("author")),
                })
        for v in node.values():
            _walk_brand_entities(v, acc)
    elif isinstance(node, list):
        for v in node:
            _walk_brand_entities(v, acc)


def _extract_brand_context(tree: HTMLParser, json_ld_blocks: list) -> dict:
    """Parallel brand/identity signal that main_content extraction discards.

    Additive: does NOT touch main_content. Surfaces JSON-LD identity
    entities, author/site meta tags, footer text and <address> so the
    site-profile / Discovery prompts have the real owner name instead of
    pattern-completing initials.
    """
    entities: list = []
    for block in (json_ld_blocks or []):
        _walk_brand_entities(block, entities)
    # de-dupe by (type, name)
    seen, deduped = set(), []
    for e in entities:
        key = (e["type"], e["name"].lower())
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    def _meta(*sel: str):
        for s in sel:
            n = tree.css_first(s)
            if n:
                v = (n.attributes.get("content") or "").strip()
                if v:
                    return v
        return None

    footer = tree.css_first("footer")
    footer_text = ""
    if footer:
        footer_text = re.sub(
            r"\s+", " ", footer.text(separator=" ", strip=True) or ""
        ).strip()[:500]

    addr = tree.css_first("address")
    address_text = ""
    if addr:
        address_text = re.sub(
            r"\s+", " ", addr.text(separator=" ", strip=True) or ""
        ).strip()[:300]

    return {
        "json_ld_entities": deduped[:10],
        "meta_author": _meta('meta[name="author"]', 'meta[property="og:author"]',
                             'meta[property="article:author"]'),
        "og_site_name": _meta('meta[property="og:site_name"]'),
        "og_title": _meta('meta[property="og:title"]'),
        "twitter_creator": _meta('meta[name="twitter:creator"]',
                                 'meta[property="twitter:creator"]'),
        "footer_text": footer_text,
        "address_text": address_text,
    }


def _build_report(url: str, response: httpx.Response, fetch_time_ms: int) -> dict:
    raw_html = response.text
    raw_html_bytes = len(response.content)
    tree = HTMLParser(raw_html)

    final_url = str(response.url)
    parsed_final = urlparse(final_url)
    base_host = (parsed_final.netloc or "").lower()

    # --- technical ---
    canonical_node = tree.css_first('link[rel="canonical"]')
    canonical_value = (
        canonical_node.attributes.get("href") if canonical_node else None
    )
    canonical_present = canonical_value is not None and canonical_value != ""
    self_referencing = bool(
        canonical_present
        and _normalize_url(canonical_value) == _normalize_url(final_url)
    )

    # Each r in response.history is a Response that was redirected FROM;
    # response.url is where we finally landed. httpx caps this at
    # MAX_REDIRECTS. No redirects -> just the originally requested URL.
    if response.history:
        redirect_chain = [str(r.url) for r in response.history]
        redirect_chain.append(final_url)
    else:
        redirect_chain = [url]

    # --- meta ---
    title_node = tree.css_first("title")
    title_text = title_node.text(strip=True) if title_node else ""

    desc_node = tree.css_first('meta[name="description"]')
    desc_text = ""
    if desc_node:
        desc_text = (desc_node.attributes.get("content") or "").strip()

    robots_node = tree.css_first('meta[name="robots"]')
    robots_val = robots_node.attributes.get("content") if robots_node else None

    # --- headings ---
    # AAA-294 §12 — selectolax .text(strip=True) concatenates adjacent child text nodes with
    # NO separator ("...London" + "your team" -> "Londonyour"). Use separator=" " + whitespace
    # collapse so spaces land at DOM / <br> / adjacent-node boundaries; punctuation preserved.
    headings: dict[str, list[str]] = {}
    for level in range(1, 7):
        tag = f"h{level}"
        headings[tag] = [
            _heading_text(n)
            for n in tree.css(tag)
            if _heading_text(n)
        ]

    # --- content (visible text) ---
    text_tree = HTMLParser(raw_html)
    for node in text_tree.css("script, style, noscript"):
        node.decompose()
    body = text_tree.css_first("body")
    visible_text = (body.text(separator=" ", strip=True) if body else "") or ""
    visible_text = re.sub(r"\s+", " ", visible_text).strip()

    # --- internationalisation signals (for site_profile) ---
    i18n = _detect_i18n(tree)
    # AAA-285 S1-A — HTTP Content-Language RESPONSE header (additive; distinct from
    # the <meta http-equiv> form already captured above). None when absent.
    i18n["content_language_header"] = (response.headers.get("content-language") or None)

    # --- main content (article body, boilerplate stripped) ---
    main_content = _extract_main_content(raw_html)
    boilerplate_detected = _detect_boilerplate(tree)

    # --- Googlebot 2 MiB indexing limit risk ---
    googlebot_index_limit = _googlebot_index_limit(
        tree, response, raw_html, raw_html_bytes, _count_words(visible_text)
    )

    # --- schema markup ---
    json_ld_blocks: list[dict] = []
    schema_types: list[str] = []
    for node in tree.css('script[type="application/ld+json"]'):
        block_text = node.text() or ""
        if not block_text.strip():
            continue
        try:
            parsed = json.loads(block_text)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    json_ld_blocks.append(item)
        elif isinstance(parsed, dict):
            json_ld_blocks.append(parsed)
        _extract_schema_types(parsed, schema_types)

    microdata_present = tree.css_first("[itemscope]") is not None
    for node in tree.css("[itemtype]"):
        itemtype = node.attributes.get("itemtype") or ""
        if itemtype:
            schema_types.append(itemtype.rstrip("/").rsplit("/", 1)[-1])

    # de-dupe preserving order
    seen: set[str] = set()
    schema_types_detected = [
        t for t in schema_types if not (t in seen or seen.add(t))
    ]

    # --- brand context (parallel identity signal; additive) ---
    brand_context = _extract_brand_context(tree, json_ld_blocks)

    # --- links ---
    internal_count = 0
    external_count = 0
    internal_anchors: list[dict] = []
    # AAA-294 HALT-7 — direct contact affordances (mailto:/tel:) are captured into a
    # DEDICATED canonical field BEFORE being excluded from navigation classification.
    # Always emitted as a list (an explicitly empty list means detection ran and found
    # nothing); matched case-insensitively; the ORIGINAL href is preserved unmodified;
    # contact links are never added to the internal/external navigation counts.
    contact_anchors: list[dict] = []
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        if href.lower().startswith(("mailto:", "tel:")):
            contact_anchors.append({"href": href, "text": a.text(strip=True)})
            continue
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        parsed_href = urlparse(href)
        is_external = bool(parsed_href.netloc) and (
            parsed_href.netloc.lower() != base_host
        )
        if is_external:
            external_count += 1
        else:
            internal_count += 1
            internal_anchors.append(
                {"href": href, "text": a.text(strip=True)}
            )

    # --- images ---
    imgs = tree.css("img")
    total_images = len(imgs)
    with_alt = sum(
        1
        for img in imgs
        if (img.attributes.get("alt") or "").strip() != ""
    )
    without_alt = total_images - with_alt
    alt_coverage = (
        round(with_alt / total_images * 100, 2) if total_images else 0.0
    )

    # --- javascript indicators ---
    scripts = tree.css("script")
    external_script_count = 0
    inline_script_bytes = 0
    total_script_bytes = 0
    for s in scripts:
        src = s.attributes.get("src")
        if src:
            external_script_count += 1
        else:
            body_bytes = len((s.text() or "").encode("utf-8"))
            inline_script_bytes += body_bytes
        total_script_bytes += len(s.html.encode("utf-8")) if s.html else 0

    script_to_html_ratio = (
        round(total_script_bytes / raw_html_bytes, 4)
        if raw_html_bytes
        else 0.0
    )

    return {
        # Per schema: final URL after redirects (not the requested input).
        "url": final_url,
        "status_code": response.status_code,
        "fetch_time_ms": fetch_time_ms,
        "technical": {
            "https": parsed_final.scheme == "https",
            "canonical": {
                "present": canonical_present,
                "value": canonical_value,
                "self_referencing": self_referencing,
            },
            "redirect_chain": redirect_chain,
        },
        "meta": {
            "title": {
                "text": title_text,
                "chars": len(title_text),
                "words": _count_words(title_text),
            },
            "description": {
                "text": desc_text,
                "chars": len(desc_text),
                "words": _count_words(desc_text),
            },
            "robots": robots_val,
        },
        "headings": headings,
        "content": {
            "raw_html_bytes": raw_html_bytes,
            "visible_text": visible_text,
            "visible_text_chars": len(visible_text),
            "visible_text_words": _count_words(visible_text),
        },
        # AAA-42: transient — the raw HTML string for in-pipeline measurement
        # (agent_friendly_measurements). Convention: keys starting with "_"
        # are NOT archived; test_agent.audit() pops this before persistence.
        "_raw_html_transient": raw_html,
        "i18n": i18n,
        "brand_context": brand_context,
        "main_content": main_content,
        "boilerplate_detected": boilerplate_detected,
        "googlebot_index_limit": googlebot_index_limit,
        "schema_markup": {
            "json_ld_present": len(json_ld_blocks) > 0,
            "json_ld_blocks": json_ld_blocks,
            "microdata_present": microdata_present,
            "schema_types_detected": schema_types_detected,
        },
        "links": {
            "internal_count": internal_count,
            "external_count": external_count,
            "internal_anchors": internal_anchors,
            # AAA-294 HALT-7 canonical contact-affordance contract: always present in
            # newly generated crawl output; [] = detection performed, none found.
            "contact_anchors": contact_anchors,
        },
        "images": {
            "total_count": total_images,
            "with_alt": with_alt,
            "without_alt": without_alt,
            "alt_coverage_percent": alt_coverage,
        },
        "javascript_indicators": {
            "script_tag_count": len(scripts),
            "external_script_count": external_script_count,
            "inline_script_bytes": inline_script_bytes,
            "total_script_bytes": total_script_bytes,
            "script_to_html_ratio": script_to_html_ratio,
            "noscript_block_present": tree.css_first("noscript") is not None,
            "spa_indicators": _detect_spa_indicators(raw_html, tree),
        },
    }


async def crawl_html(url: str, locale: str | None = None) -> dict:
    """Fetch a URL and extract structured SEO/GEO data.

    AAA-179: the crawl ``Accept-Language`` is derived deterministically from the
    audit's requested ``locale`` (explicit arg, else the module-global set by
    run_one) — NEVER an ambient en-US default. Unsupported/absent locale → the
    header is OMITTED so the site serves its own default language (logged).

    Returns the report dict on success. On failure returns
    ``{"url": url, "error": str, "error_type": ...}`` where ``error_type`` is
    one of ``timeout``, ``http_error``, ``parse_error``, ``network_error``.
    """
    # AAA-255 — reuse the Gate-A pre-flight fetch for this URL (no double fetch).
    _pre = _consume_prefetched_crawl(url)
    if _pre is not None:
        return _pre
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    loc = locale if locale is not None else _REQUESTED_LOCALE
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    accept_lang = accept_language_for(loc)
    if accept_lang:
        headers["Accept-Language"] = accept_lang  # hard-set from requested locale
    else:
        logger.warning("crawl_html: no Accept-Language for locale=%r — omitting "
                       "header so the site serves its own default (url=%s)", loc, url)

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            timeout=timeout,
            headers=headers,
        ) as client:
            response = await client.get(url)
        fetch_time_ms = int((time.perf_counter() - start) * 1000)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return {
                "url": url,
                "status_code": response.status_code,
                "error": str(exc),
                "error_type": "http_error",
            }

        try:
            return _build_report(url, response, fetch_time_ms)
        except Exception as exc:  # noqa: BLE001 - parsing is best-effort
            return {
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
                "error_type": "parse_error",
            }

    except httpx.TimeoutException as exc:
        return {"url": url, "error": str(exc) or "request timed out",
                "error_type": "timeout"}
    except httpx.TooManyRedirects as exc:
        return {"url": url, "error": str(exc), "error_type": "network_error"}
    except httpx.RequestError as exc:
        return {"url": url, "error": str(exc) or type(exc).__name__,
                "error_type": "network_error"}


class _ResponseShim:
    """Minimal httpx.Response stand-in so _build_report can parse an
    already-rendered HTML string (e.g. from Playwright) into the exact
    same schema crawl_html produces."""

    def __init__(self, url: str, html: str, status_code: int) -> None:
        self.text = html
        self.content = html.encode("utf-8", errors="replace")
        self.url = url
        self.status_code = status_code
        self.history: list = []
        self.encoding = "utf-8"


def parse_rendered_html(
    url: str, html: str, status_code: int = 200, fetch_time_ms: int = 0
) -> dict:
    """Run the full crawl_html parsing pipeline on a pre-rendered HTML string.

    Used by the Playwright escalation path: render_url() supplies the
    JS-executed DOM, this produces the identical crawl_html output schema.
    """
    if not html:
        return {"url": url, "error": "empty rendered html",
                "error_type": "parse_error"}
    try:
        return _build_report(
            url, _ResponseShim(url, html, status_code), fetch_time_ms
        )
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": f"{type(exc).__name__}: {exc}",
                "error_type": "parse_error"}
