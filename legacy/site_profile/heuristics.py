"""Fast, API-free site signals derived from the crawl_result.

These run BEFORE Gemini and are fused with it later. Everything here is
deterministic and cheap.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from site_profile.constants import (
    COUNTRY_BY_CODE,
    CURRENCY_ISO,
    CURRENCY_SYMBOLS,
    GENERIC_TLDS,
    PHONE_PREFIX_COUNTRY,
    TLD_COUNTRY,
    TWO_LABEL_SUFFIXES,
)

# Prefixes longest-first so "+420" matches before "+4"/"+1".
_PHONE_PREFIXES = sorted(PHONE_PREFIX_COUNTRY, key=len, reverse=True)
_PHONE_RE = re.compile(
    r"(?:\+|00)\s?(\d{1,3})(?:[\s.\-/()]?\d){5,}"
)


def _split_host(host: str) -> tuple[str, str]:
    """Return (domain_root, tld) e.g. 'www.shop.agrobook.hu' -> (agrobook, hu).

    Handles two-label public suffixes like co.uk / com.au.
    """
    host = (host or "").lower().split(":")[0].lstrip(".")
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in TWO_LABEL_SUFFIXES:
        return labels[-3], ".".join(labels[-2:])
    if len(labels) >= 2:
        return labels[-2], labels[-1]
    return host, ""


def _tld_country(tld: str) -> str | None:
    if tld in TLD_COUNTRY:
        return TLD_COUNTRY[tld]
    last = tld.split(".")[-1]
    if last in GENERIC_TLDS:
        return None
    return TLD_COUNTRY.get(last)


def _norm_country(value) -> str | None:
    """Normalise a schema addressCountry ('HU' or 'Hungary' or {name})."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id") or ""
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    if v.upper() in COUNTRY_BY_CODE:
        return COUNTRY_BY_CODE[v.upper()]
    for country in set(COUNTRY_BY_CODE.values()):
        if v.lower() == country.lower():
            return country
    return v  # unknown but keep it for transparency


def _walk_jsonld(node, found: dict) -> None:
    """Pull site name + address country out of JSON-LD blocks."""
    if isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(x in ("Organization", "WebSite", "LocalBusiness") for x in types):
            name = node.get("name")
            if isinstance(name, str) and name.strip() and not found.get("name"):
                found["name"] = name.strip()
        addr = node.get("address")
        for a in (addr if isinstance(addr, list) else [addr]):
            if isinstance(a, dict) and a.get("addressCountry") and not found.get(
                "country"
            ):
                found["country"] = a["addressCountry"]
        for v in node.values():
            _walk_jsonld(v, found)
    elif isinstance(node, list):
        for v in node:
            _walk_jsonld(v, found)


def extract_heuristic_signals(crawl_result: dict) -> dict:
    final_url = crawl_result.get("url") or ""
    parsed = urlparse(final_url)
    host = parsed.netloc.lower()
    domain_root, tld = _split_host(host)

    i18n = crawl_result.get("i18n") or {}
    schema = crawl_result.get("schema_markup") or {}
    main_text = (crawl_result.get("main_content") or {}).get("text", "") or ""
    meta = crawl_result.get("meta") or {}
    scan_text = " ".join(
        [
            main_text,
            meta.get("title", {}).get("text", ""),
            meta.get("description", {}).get("text", ""),
        ]
    )

    # --- language ---
    hreflang_entries = i18n.get("hreflang") or []
    hreflang_langs, hreflang_countries = [], []
    for e in hreflang_entries:
        hl = (e.get("hreflang") or "").strip().lower()
        if not hl or hl == "x-default":
            continue
        parts = re.split(r"[-_]", hl)
        if parts[0] and parts[0] not in hreflang_langs:
            hreflang_langs.append(parts[0])
        if len(parts) > 1:
            cc = parts[1].upper()
            country = COUNTRY_BY_CODE.get(cc)
            if country and country not in hreflang_countries:
                hreflang_countries.append(country)

    # --- path prefix (/de/, /at/, /en-us/) ---
    path_prefix_country = None
    seg = [s for s in parsed.path.split("/") if s][:1]
    if seg:
        token = seg[0].lower()
        m = re.fullmatch(r"([a-z]{2})(?:[-_]([a-z]{2}))?", token)
        if m:
            cc = (m.group(2) or m.group(1)).upper()
            path_prefix_country = COUNTRY_BY_CODE.get(cc)

    # --- phone prefixes ---
    phone_prefixes = []
    seen_prefix = set()
    for raw in _PHONE_RE.finditer(scan_text):
        digits = "+" + raw.group(1)
        for pref in _PHONE_PREFIXES:
            if digits.startswith(pref) or raw.group(0).replace(" ", "").startswith(
                "00" + pref[1:]
            ):
                if pref not in seen_prefix:
                    seen_prefix.add(pref)
                    phone_prefixes.append(
                        {"prefix": pref, "country": PHONE_PREFIX_COUNTRY[pref]}
                    )
                break

    # --- currencies ---
    # Symbols: literal (distinctive glyphs, no collisions).
    # ISO codes: word-boundary + case-sensitive so "kn"/"lei"/"USD" can't
    # match inside ordinary words.
    currencies: list[str] = []
    for sym, iso in CURRENCY_SYMBOLS.items():
        if sym in scan_text and iso not in currencies:
            currencies.append(iso)
    for iso in CURRENCY_ISO:
        if re.search(rf"\b{iso}\b", scan_text) and iso not in currencies:
            currencies.append(iso)

    # --- og:locale country (e.g. "en_GB" -> United Kingdom) ---
    og_locale_country = None
    og_locale_raw = i18n.get("og_locale")
    if og_locale_raw:
        m = re.search(r"[a-z]{2}[-_]([A-Za-z]{2})", og_locale_raw.strip())
        if m:
            og_locale_country = COUNTRY_BY_CODE.get(m.group(1).upper())

    # --- JSON-LD: site name + address country ---
    jl = {}
    for block in schema.get("json_ld_blocks", []) or []:
        _walk_jsonld(block, jl)
    schema_country = _norm_country(jl.get("country"))

    return {
        "language_hints": {
            "html_lang": (i18n.get("html_lang") or None),
            "content_language_meta": (i18n.get("content_language") or None),
            "hreflang_languages": hreflang_langs,
        },
        "location_hints": {
            "tld": f".{tld}" if tld else "",
            "tld_country": _tld_country(tld),
            "hreflang_countries": hreflang_countries,
            "path_prefix_country": path_prefix_country,
            "phone_prefixes": phone_prefixes,
            "currencies_found": currencies,
            "schema_address_country": schema_country,
            "og_locale": og_locale_raw or None,
            "og_locale_country": og_locale_country,
        },
        "brand_hints": {
            "site_name": jl.get("name") or i18n.get("og_site_name") or None,
            "domain_root": domain_root,
        },
    }
