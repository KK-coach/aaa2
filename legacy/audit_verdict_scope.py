# -*- coding: utf-8 -*-
"""v1 `report/audit_verdict.py` — kivonat: csak a `scope_signals()` és a három konstansa
(`_CITY_TOKENS`, `_TLD_COUNTRY`, `_INTL_RE`). A modul többi része itt nem kell."""

from __future__ import annotations

import re

# ── AAA-294 — deterministic mixed-scope detection (single source for Audit scope +
#    page_role_read). City (local) is the gate; mixed = city + (country or international). ──
_CITY_TOKENS = (
    "london", "manchester", "birmingham", "leeds", "glasgow", "edinburgh", "bristol",
    "liverpool", "cardiff", "belfast", "budapest", "vienna", "berlin", "munich", "paris",
    "amsterdam", "dublin", "madrid", "barcelona", "milan", "rome", "zurich", "new york",
    "los angeles", "san francisco", "chicago", "boston", "toronto", "sydney", "melbourne",
    "singapore", "dubai")
_TLD_COUNTRY = {".co.uk": "United Kingdom", ".uk": "United Kingdom", ".hu": "Hungary",
                ".de": "Germany", ".fr": "France", ".es": "Spain", ".it": "Italy",
                ".nl": "Netherlands", ".ie": "Ireland", ".ca": "Canada", ".au": "Australia",
                ".at": "Austria", ".ch": "Switzerland"}
_INTL_RE = re.compile(r"\b(worldwide|international|internationally|globally|global|"
                      r"online|remotely|remote)\b", re.I)


def scope_signals(ao: dict) -> dict:
    """Deterministic geographic-scope read. Returns {state, city, country,
    has_international, scopes[], locked{en,hu}}. state='mixed' when a local CITY signal
    coexists with a national and/or international signal — described, never resolved."""
    import json as _json
    fb = ao.get("fact_base") or {}
    sp = ao.get("site_profile") or {}
    ltp = ao.get("language_targeting_profile") or {}
    tm = ao.get("title_meta_measurements") or {}
    title = ((tm.get("title") or {}).get("text") if isinstance(tm.get("title"), dict) else tm.get("title")) or ""
    md = tm.get("description")
    meta = (md.get("text") if isinstance(md, dict) else md) or ""
    ss = (ao.get("seo_snapshot") or {}).get("headings") or {}
    heads = []
    for hk in ("h1", "h2", "h3"):
        hv = ss.get(hk)
        if isinstance(hv, list):
            heads += [str(x) for x in hv]
        elif isinstance(hv, str):
            heads.append(hv)
    locality = ((fb.get("classification") or {}).get("locality") or {}).get("value") or ""
    head_text = " ".join([title, meta] + heads).lower()
    city = None
    for c in _CITY_TOKENS:
        if re.search(r"\b" + re.escape(c) + r"\b", head_text):
            city = c.title()
            break
    tld = ((sp.get("detected_signals") or {}).get("tld") or "").lower()
    # A country LABEL is rendered ONLY from STRONG page/domain signals: a country-coded
    # TLD or a detected postal/address country. The SERP/eval-market resolver
    # (ltp.target_market_country / site_profile.location) and the en-US locale are NOT
    # detected-market signals — an English / .com / .coach page is not "US" or "national".
    country = _TLD_COUNTRY.get(tld) or (sp.get("detected_signals") or {}).get("address_country")
    intl = bool(_INTL_RE.search(head_text)) or locality.lower() in ("global", "worldwide", "international")
    if not intl:  # client body copy (gated by city below → no false mixed on city-less pages)
        # AAA-294 — CURATED allowlist only: crawl.main_content.text (the pipeline's standard
        # scoped body-copy extraction, e.g. also used by page_analysis.keywords.select_body)
        # and crawl.schema_markup (explicit, structured self-declared business facts). NEVER
        # the raw/unfiltered crawl dump — that entangles footer/legal boilerplate (company
        # registration names, copyright text) and sibling-market ccTLD links (e.g. emag.ro
        # next to "Dante International SA" in emag.hu's footer) with genuine body content, and
        # those must never count as page-scope evidence for the audited page. Excludes
        # crawl.i18n (hreflang/sibling-language declarations), crawl.brand_context (footer/
        # address chrome), crawl.content (unfiltered whole-page text), crawl.links,
        # crawl.technical — all out of scope by construction (allowlist, not a blocklist).
        crawl = ao.get("crawl") or {}
        curated_text = (str(((crawl.get("main_content") or {}).get("text")) or "")
                       + " " + _json.dumps(crawl.get("schema_markup") or {}, ensure_ascii=False, default=str))
        intl = bool(_INTL_RE.search(curated_text.lower()))
    state = "mixed" if (city and (country or intl)) else "single"
    # market_scope is the SCOPE TYPE (never a raw locality enum like 'national').
    if state == "mixed":
        market_scope = "mixed"
    elif country:
        market_scope = "country_specific"
    elif intl:
        market_scope = "international_global"
    elif city:
        market_scope = "local"
    else:
        market_scope = "not_country_specific"
    scopes = [s for s in (city, country, ("international / online" if intl else None)) if s]
    locked = {"en": "", "hu": ""}
    if state == "mixed":
        tail_en = " and international / online delivery" if intl else ""
        tail_hu = " és nemzetközi / online kiszolgálást" if intl else ""
        locked["en"] = ("%s is foregrounded in the title and H1, while the page also signals "
                        "%s%s. This is noted, not resolved in this layer."
                        % (city, country or "a national market", tail_en))
        locked["hu"] = ("%s kerül előtérbe a címben és a H1-ben, miközben az oldal %s%s is "
                        "jelez. Ezt jelezzük, de ezen a szinten nem oldjuk fel."
                        % (city, country or "egy nemzeti piacot", tail_hu))
    return {"state": state, "market_scope": market_scope, "city": city, "country": country,
            "has_international": intl, "scopes": scopes, "locked": locked}
