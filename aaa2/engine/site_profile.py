"""Site-profil a crawl végén: célország, nyelvek, oldalszám, nyers tech-jelek.

Csak a sikeres (2xx, hiba nélküli, renderelt DOM-mal bíró) oldalakból dolgozik.

Nyelv oldalanként, a v1 `_detect_i18n` sorrendjében: `html[lang]`, content-language meta,
`og:locale`, végül nyelvdetekció a main contentből (`language.py`). A site nyelvei az oldalak
elsődleges nyelvi címkéi oldalszám szerint csökkenő sorrendben; utánuk azok, amelyek csak
hreflangban szerepelnek (az `x-default` nélkül).

Célország, az első döntő jel szerint:

1. országkódos TLD, a generikusan használtak (`.io`, `.co`, `.me`, `.tv`, `.ai`, ...) nélkül;
2. a hreflang régiókódjai, ha egy régió abszolút többségben van;
3. a site nyelvei között pontosan egy olyan, amely csak egy országban hivatalos (`hu` → HU);
4. az oldalak nyelvi címkéjének régiókódja, ha mindegyiké ugyanaz;

különben None.

`page_count`: a sikeres oldalak száma.

`tech_signals`: nyers jelek, osztályozás nélkül (az az M4 resolver dolga), az oldalak száma
szerint csökkenő sorrendben: `generator:<meta generator>`, `script:<script-src host>` (a site
saját hostja nélkül), `path:<jellemző útvonal>` (`config/tech_paths.txt`), `dom:<keretrendszer
jele>` (`ng-version=…`, `__NEXT_DATA__`, `__NUXT__`, `data-reactroot`, `astro-island`).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import duckdb
import zstandard
from selectolax.parser import HTMLParser

from aaa2.engine.language import detect_language
from aaa2.engine.normalize import public_suffix

TECH_PATHS_FILE = Path(__file__).parent / "config" / "tech_paths.txt"

# Országkódos TLD-k, amelyeket jellemzően nem az országra szabott site-ok használnak.
GENERIC_CCTLDS = frozenset({
    "io", "co", "me", "tv", "ai", "fm", "ly", "to", "cc", "ws", "gg", "sh", "ac", "gl",
    "la", "vc", "nu", "eu", "su",
})
# Nyelvek, amelyek pontosan egy országban hivatalosak (országos szinten).
SINGLE_COUNTRY_LANGUAGES = {
    "hu": "HU", "cs": "CZ", "sk": "SK", "pl": "PL", "sl": "SI", "bg": "BG", "da": "DK",
    "fi": "FI", "et": "EE", "lv": "LV", "lt": "LT", "uk": "UA", "ja": "JP", "he": "IL",
    "nb": "NO", "nn": "NO", "no": "NO", "is": "IS", "th": "TH", "vi": "VN", "ka": "GE",
    "hy": "AM", "mk": "MK", "lb": "LU",
}
DOM_MARKERS = (
    (re.compile(r"\sng-version=\"([^\"]+)\""), "ng-version={}"),
    (re.compile(r"__NEXT_DATA__"), "__NEXT_DATA__"),
    (re.compile(r"__NUXT__"), "__NUXT__"),
    (re.compile(r"\sdata-reactroot\b"), "data-reactroot"),
    (re.compile(r"<astro-island\b"), "astro-island"),
)
_REGION = re.compile(r"^[A-Za-z]{2}$")


@dataclass(frozen=True)
class SiteProfile:
    target_country: str | None
    languages: tuple[str, ...]
    page_count: int
    tech_signals: tuple[str, ...]


def update_site_profile(con: duckdb.DuckDBPyConnection) -> SiteProfile:
    """A profil kiszámítása és beírása a `site` sorba; a hívó tranzakciójában fut."""
    profile = build_profile(con)
    con.execute(
        "UPDATE site SET target_country = ?, languages = ?, page_count = ?, tech_signals = ?",
        [profile.target_country, list(profile.languages), profile.page_count,
         list(profile.tech_signals)],
    )
    return profile


def build_profile(con: duckdb.DuckDBPyConnection) -> SiteProfile:
    site = con.execute("SELECT domain, seed_url FROM site").fetchone()
    if site is None:
        return SiteProfile(None, (), 0, ())
    domain, seed_url = site
    seed_host = (urlsplit(seed_url).hostname or "").lower()
    rows = con.execute(
        "SELECT rendered_html, main_content, hreflang FROM pages "
        "WHERE status BETWEEN 200 AND 299 AND error IS NULL AND rendered_html IS NOT NULL"
    ).fetchall()
    decompressor = zstandard.ZstdDecompressor()
    page_tags: list[str | None] = []
    hreflang_codes: list[str] = []
    signals: Counter[str] = Counter()
    for compressed, main_content, hreflang in rows:
        html = decompressor.decompress(compressed).decode("utf-8", "replace")
        tree = HTMLParser(html)
        page_tags.append(page_language(tree, main_content or ""))
        hreflang_codes.extend(pair.split("|", 1)[0] for pair in hreflang or ())
        signals.update(page_tech_signals(tree, html, seed_host))
    languages = site_languages(page_tags, hreflang_codes)
    return SiteProfile(
        target_country=target_country(domain, hreflang_codes, languages, page_tags),
        languages=languages,
        page_count=len(rows),
        tech_signals=tuple(signal for signal, _ in sorted(
            signals.items(), key=lambda item: (-item[1], item[0]))),
    )


def page_language(tree: HTMLParser, main_content: str) -> str | None:
    """Egy oldal nyelvi címkéje (`hu-HU`, `en`), vagy None, ha semmi nem mondja meg."""
    html = tree.css_first("html")
    candidates = [
        html.attributes.get("lang") if html is not None else None,
        _meta(tree, "http-equiv", "content-language"),
        _meta(tree, "name", "content-language"),
        _meta(tree, "property", "og:locale"),
    ]
    for candidate in candidates:
        tag = _clean_tag(candidate)
        if tag:
            return tag
    return detect_language(main_content)


def site_languages(page_tags: list[str | None], hreflang_codes: list[str]) -> tuple[str, ...]:
    by_pages = Counter(_primary(tag) for tag in page_tags if tag)
    ordered = [lang for lang, _ in sorted(by_pages.items(), key=lambda item: (-item[1], item[0]))]
    for code in hreflang_codes:
        lang = _primary(code)
        if lang and lang != "x" and lang not in ordered:
            ordered.append(lang)
    return tuple(ordered)


def target_country(
    domain: str, hreflang_codes: list[str], languages: tuple[str, ...],
    page_tags: list[str | None],
) -> str | None:
    tld = public_suffix(domain).rsplit(".", 1)[-1]
    if len(tld) == 2 and tld.isalpha() and tld not in GENERIC_CCTLDS:
        return "GB" if tld == "uk" else tld.upper()
    regions = Counter(region for code in hreflang_codes if (region := _region(code)))
    if regions:
        region, count = regions.most_common(1)[0]
        if count * 2 > sum(regions.values()):
            return region
    national = {SINGLE_COUNTRY_LANGUAGES[lang] for lang in languages
                if lang in SINGLE_COUNTRY_LANGUAGES}
    if len(national) == 1:
        return national.pop()
    page_regions = {_region(tag) for tag in page_tags if tag}
    if len(page_regions) == 1 and None not in page_regions:
        return page_regions.pop()
    return None


def page_tech_signals(tree: HTMLParser, html: str, own_host: str) -> set[str]:
    """Egy oldal nyers tech-jelei; oldalanként egyszer számít mindegyik."""
    found: set[str] = set()
    for meta in tree.css("meta[name]"):
        if (meta.attributes.get("name") or "").strip().lower() == "generator":
            content = (meta.attributes.get("content") or "").strip()
            if content:
                found.add(f"generator:{content}")
    for script in tree.css("script[src]"):
        host = (urlsplit((script.attributes.get("src") or "").strip()).hostname or "").lower()
        if host and host != own_host:
            found.add(f"script:{host}")
    for pattern in tech_path_patterns():
        found.update(f"path:{match}" for match in pattern.findall(html))
    for pattern, label in DOM_MARKERS:
        match = pattern.search(html)
        if match:
            found.add(f"dom:{label.format(*match.groups())}")
    return found


@lru_cache(maxsize=1)
def tech_path_patterns() -> tuple[re.Pattern[str], ...]:
    lines = TECH_PATHS_FILE.read_text(encoding="utf-8").splitlines()
    return tuple(re.compile(line) for line in lines if line.strip() and not line.startswith("#"))


def _meta(tree: HTMLParser, attribute: str, value: str) -> str | None:
    for meta in tree.css(f"meta[{attribute}]"):
        if (meta.attributes.get(attribute) or "").strip().lower() == value:
            return meta.attributes.get("content")
    return None


def _clean_tag(value: str | None) -> str | None:
    tag = (value or "").strip().replace("_", "-").split(",")[0].strip()
    return tag or None


def _primary(tag: str) -> str:
    return tag.split("-", 1)[0].lower()


def _region(tag: str) -> str | None:
    parts = tag.replace("_", "-").split("-")
    if len(parts) >= 2 and _REGION.match(parts[1]) and parts[0].lower() != "x":
        return parts[1].upper()
    return None
