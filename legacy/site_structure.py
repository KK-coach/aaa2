# -*- coding: utf-8 -*-
"""AAA-260 — site-structure primitive. A REUSABLE, URL-agnostic signal derived
PURELY from a single crawl (so it runs identically on the client AND on any
competitor URL — no homepage/client hardcoding). Deterministic, $0, no LLM.

A HUB page (typically a homepage) orients visitors via internal links into a
service/topic taxonomy; it does NOT itself cover those topics in depth. Judging a
hub on its own thin per-topic text (e.g. §4.4 topical coverage) is misleading —
its breadth lives on the linked section pages. This primitive extracts that
taxonomy + a hub flag so consumers (topical map, keyword selection) can branch.

build_site_structure(crawl, page_type=None) -> dict | None
  page_type            — from the caller's classification (optional; inferred from
                         the URL when absent, so it works on un-classified competitors).
  is_hub               — deterministic (see _is_hub).
  sections             — the site's own taxonomy from internal anchors, nav-chrome
                         dropped, grouped by target URL: [{label, url, slug}].
  umbrella_candidates  — candidate umbrella terms from title + H1 + sections.
  _signals             — the raw derivation inputs (transparency).

Links absent / unusable → returns None (consumers degrade, never crash)."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# nav-chrome path slugs that are NOT part of the site's topical taxonomy
_CHROME_SLUGS = {
    "", "hu", "en", "de", "fr", "es",                       # home + language switches
    "about", "about-us", "rolunk", "rolam", "company",
    "contact", "contacts", "kapcsolat", "lets-talk",
    "legal", "privacy", "privacy-policy", "adatvedelem", "adatkezeles",
    "terms", "terms-of-service", "aszf", "impressum", "imprint",
    "cookie", "cookies", "cookie-policy", "gdpr",
    "sitemap", "search", "login", "signin", "sign-in", "register",
    "careers", "career", "jobs", "press", "newsroom",
}
# repeated call-to-action anchor texts that are chrome, not a section label
_CTA_RE = re.compile(
    r"^(read (the )?(blog ?post|more|article)|let.?s talk|lets talk|book a call|"
    r"get started|contact us|learn more|tovább|olvass tovább|kapcsolat|"
    r"beszéljünk|tudj meg többet)\.?$", re.I)
# trailing role-noun phrase for umbrella extraction (EN + a few HU): up to 4
# preceding tokens before the role noun (post-stripped of leading function words).
_ROLE_RE = re.compile(
    r"((?:[\wÁÉÍÓÖŐÚÜŰáéíóöőúüű&\-]+\s+){0,4}"
    r"(?:consultant|consultancy|consulting|agency|studio|services?|specialist|"
    r"expert|partner|advisor|tanácsadó|ügynökség|szakértő))\b", re.I)
_LEAD_STOP = {"with", "a", "an", "the", "for", "your", "our", "and", "to", "of",
              "is", "as", "scale", "grow", "boost", "drive", "revenue", "data",
              "skálázd", "növeld", "&"}

_MAX_SECTIONS = 24
_PER_TOPIC_THIN_WORDS = 400   # hub spreads < ~400 own words per linked topic
_MIN_HUB_INTERNAL = 8         # a hub orients via many internal links
_MAX_HUB_EXTERNAL = 2         # and ~no outbound external links


def _path_slug(url: str) -> str:
    try:
        path = urlsplit(url).path or ""
    except Exception:  # noqa: BLE001
        path = url or ""
    segs = [s for s in path.strip("/").split("/") if s]
    return (segs[-1] if segs else "").lower()


def _looks_like_homepage(crawl: dict) -> bool:
    u = crawl.get("url") or ""
    try:
        path = (urlsplit(u).path or "").strip("/")
    except Exception:  # noqa: BLE001
        path = ""
    return path in ("", "index", "home", "index.html")


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("”", "'").replace("’", "'")).strip()


def _strip_brand(title: str, brand: str | None) -> str:
    """Drop a trailing/leading ' | Brand' style suffix from a title."""
    t = _clean_text(title)
    t = re.split(r"\s*[|–—\-–—·]\s*", t)
    # the longest segment is usually the descriptive phrase (drop a short brand tail)
    parts = [p for p in t if p]
    if not parts:
        return ""
    if brand:
        bl = brand.strip().lower()
        parts = [p for p in parts if p.strip().lower() != bl] or parts
    return max(parts, key=len)


def _umbrella_candidates(title: str, h1: str, sections: list, brand: str | None) -> list:
    """Candidate umbrella terms: the trailing role-noun phrase from title/H1 (the
    cleanest umbrella, e.g. 'Organic Growth Consultant'), then the brand-stripped
    title as a fallback. Deterministic; no fragile prefix-stripping."""
    cands: list = []

    def _add(x):
        x = _clean_text(x).strip(" &-–—|")
        if x and x.lower() not in {c.lower() for c in cands} and len(x) > 2:
            cands.append(x)

    def _strip_lead(p):
        toks = _clean_text(p).split()
        while toks and toks[0].lower().strip("&-") in _LEAD_STOP:
            toks.pop(0)
        return " ".join(toks)

    for src in (title, h1):
        m = _ROLE_RE.search(src or "")
        if m:
            _add(_strip_lead(m.group(1)))
    _add(_strip_lead(_strip_brand(title, brand)))
    return [c for c in cands if c][:6]


def _is_hub(page_type, homepage_ish: bool, internal: int, external: int,
           n_sections: int, mc_words: int) -> bool:
    if not ((page_type == "homepage") or (page_type is None and homepage_ish)):
        return False
    if internal < _MIN_HUB_INTERNAL or external > _MAX_HUB_EXTERNAL:
        return False
    if n_sections < 3:
        return False
    per_topic = mc_words / max(n_sections, 1)
    return per_topic < _PER_TOPIC_THIN_WORDS


def build_site_structure(crawl: dict, page_type=None) -> dict | None:
    """Pure, URL-agnostic. Returns the site-structure signal, or None when there
    are no internal links to derive a taxonomy from."""
    try:
        crawl = crawl or {}
        links = crawl.get("links") or {}
        anchors = links.get("internal_anchors") or []
        if not anchors:
            return None
        internal = links.get("internal_count")
        internal = int(internal) if isinstance(internal, (int, float)) else len(anchors)
        external = links.get("external_count")
        external = int(external) if isinstance(external, (int, float)) else 0

        # --- sections: group by URL PATH (folds relative+absolute / trailing-slash
        #     duplicates onto one section), drop nav-chrome ---
        def _path_key(h):
            try:
                return (urlsplit(h).path or "/").rstrip("/").lower() or "/"
            except Exception:  # noqa: BLE001
                return (h or "").lower()

        by_url: dict = {}
        for a in anchors:
            if not isinstance(a, dict):
                continue
            href = a.get("href") or a.get("url") or ""
            text = _clean_text(a.get("text") or a.get("anchor") or "")
            if not href:
                continue
            slug = _path_slug(href)
            if slug in _CHROME_SLUGS:
                continue
            key = _path_key(href)
            rec = by_url.setdefault(key, {"url": href, "slug": slug, "labels": []})
            # prefer an absolute URL for display if we first saw a relative one
            if href.startswith("http") and not rec["url"].startswith("http"):
                rec["url"] = href
            if text and not _CTA_RE.match(text):
                rec["labels"].append(text)

        sections = []
        for rec in by_url.values():
            labels = [l for l in rec["labels"] if l]
            if not labels:
                continue  # only chrome/CTA pointed here → not a real section
            # prefer the SHORTEST label (the nav label) over descriptive blurbs
            label = min(labels, key=len)
            sections.append({"label": label, "url": rec["url"], "slug": rec["slug"]})
        # stable order: by slug
        sections.sort(key=lambda s: s["slug"])
        sections = sections[:_MAX_SECTIONS]

        meta = crawl.get("meta") or {}
        title = ((meta.get("title") or {}) if isinstance(meta.get("title"), dict)
                 else {}).get("text") or (meta.get("title") if isinstance(meta.get("title"), str) else "")
        h1_list = (crawl.get("headings") or {}).get("h1") or []
        h1 = h1_list[0] if isinstance(h1_list, list) and h1_list else (h1_list if isinstance(h1_list, str) else "")
        brand = (crawl.get("site_profile") or {}).get("brand")  # usually absent on a raw crawl

        mc_words = ((crawl.get("main_content") or {}).get("words")) or 0
        homepage_ish = _looks_like_homepage(crawl)
        is_hub = _is_hub(page_type, homepage_ish, internal, external,
                         len(sections), int(mc_words or 0))

        # shared-prefix taxonomy hint (e.g. /solutions/* → a service constellation)
        prefixes: dict = {}
        for s in sections:
            try:
                segs = [x for x in urlsplit(s["url"]).path.strip("/").split("/") if x]
            except Exception:  # noqa: BLE001
                segs = []
            if len(segs) >= 2:
                prefixes[segs[0]] = prefixes.get(segs[0], 0) + 1
        taxonomy_prefix = next((p for p, n in sorted(prefixes.items(),
                                key=lambda kv: -kv[1]) if n >= 2), None)

        return {
            "page_type": page_type,
            "is_hub": is_hub,
            "sections": sections,
            "umbrella_candidates": _umbrella_candidates(title, h1, sections, brand),
            "taxonomy_prefix": taxonomy_prefix,
            "_signals": {
                "internal_count": internal, "external_count": external,
                "n_sections": len(sections), "main_content_words": int(mc_words or 0),
                "per_topic_words": round(int(mc_words or 0) / max(len(sections), 1), 1),
                "homepage_ish": homepage_ish, "page_type": page_type,
            },
        }
    except Exception:  # noqa: BLE001 — never crash a consumer
        return None
