"""Site-profil a crawl végén: célország, piaci hatókör, nyelvek, oldalszám, nyers tech-jelek.

Csak a sikeres (2xx, hiba nélküli, renderelt DOM-mal bíró) oldalakból dolgozik.

- Célország: `target_country.py`, országjelek minden oldalról, súlyozott szavazás.
- Piaci hatókör: `market_scope.py`; kezdőoldalak a seed oldal (átirányítás után) és a
  hreflang-alternatívái.
- `languages`: a `pages.lang` elsődleges nyelvi címkéi (`html[lang]`, különben a `language.py`
  detekciója) oldalszám szerint csökkenő sorrendben; utánuk azok, amelyek csak hreflangban
  szerepelnek, az `x-default` nélkül. A nyelvből nem lesz ország, és az országból sem nyelv.
- `page_count`: a sikeres oldalak száma.
- `tech_signals`: nyers jelek, osztályozás nélkül (az az M4 resolver dolga), az oldalak száma
  szerint csökkenő sorrendben: `generator:<meta generator>`, `script:<script-src host>` (a site
  saját hostja nélkül), `path:<jellemző útvonal>` (`config/tech_paths.txt`), `dom:<keretrendszer
  jele>` (`ng-version=…`, `__NEXT_DATA__`, `__NUXT__`, `data-reactroot`, `astro-island`).
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import duckdb
import zstandard
from selectolax.parser import HTMLParser

from aaa2.engine.market_scope import ScopePage, market_scope
from aaa2.engine.normalize import UrlPolicy, normalize, slash_alternate
from aaa2.engine.target_country import (
    Candidate,
    leader,
    page_country_signals,
    tld_country,
    vote,
)

TECH_PATHS_FILE = Path(__file__).parent / "config" / "tech_paths.txt"
MAX_SEED_REDIRECTS = 5

DOM_MARKERS = (
    (re.compile(r"\sng-version=\"([^\"]+)\""), "ng-version={}"),
    (re.compile(r"__NEXT_DATA__"), "__NEXT_DATA__"),
    (re.compile(r"__NUXT__"), "__NUXT__"),
    (re.compile(r"\sdata-reactroot\b"), "data-reactroot"),
    (re.compile(r"<astro-island\b"), "astro-island"),
)


@dataclass(frozen=True)
class SiteProfile:
    target_country: str | None
    target_country_confidence: str | None
    target_country_candidates: tuple[Candidate, ...]
    market_scope: str | None
    market_scope_city: str | None
    languages: tuple[str, ...]
    page_count: int
    tech_signals: tuple[str, ...]


EMPTY_PROFILE = SiteProfile(None, None, (), None, None, (), 0, ())


def update_site_profile(con: duckdb.DuckDBPyConnection) -> SiteProfile:
    """A profil kiszámítása és beírása a `site` sorba; a hívó tranzakciójában fut."""
    profile = build_profile(con)
    con.execute(
        "UPDATE site SET target_country = ?, target_country_confidence = ?, "
        "target_country_candidates = ?, market_scope = ?, market_scope_city = ?, "
        "languages = ?, page_count = ?, tech_signals = ?",
        [profile.target_country, profile.target_country_confidence,
         json.dumps([candidate.as_dict() for candidate in profile.target_country_candidates]),
         profile.market_scope, profile.market_scope_city, list(profile.languages),
         profile.page_count, list(profile.tech_signals)],
    )
    return profile


def build_profile(con: duckdb.DuckDBPyConnection) -> SiteProfile:
    site = con.execute("SELECT domain, seed_url FROM site").fetchone()
    if site is None:
        return EMPTY_PROFILE
    domain, seed_url = site
    seed_host = (urlsplit(seed_url).hostname or "").lower()
    rows = con.execute(
        "SELECT page_id, url, title, meta_description, lang, hreflang, main_content, "
        "rendered_html FROM pages "
        "WHERE status BETWEEN 200 AND 299 AND error IS NULL AND rendered_html IS NOT NULL"
    ).fetchall()
    schema = _schema_items(con)
    decompressor = zstandard.ZstdDecompressor()
    page_langs: list[str | None] = []
    hreflang_codes: list[str] = []
    country_signals: list[dict[str, set[str]]] = []
    tech: Counter[str] = Counter()
    for page_id, url, title, description, lang, hreflang, main_content, compressed in rows:
        html = decompressor.decompress(compressed).decode("utf-8", "replace")
        tree = HTMLParser(html)
        codes = [pair.split("|", 1)[0] for pair in hreflang or ()]
        page_langs.append(lang)
        hreflang_codes.extend(codes)
        text = " ".join([main_content or "", title or "", description or ""])
        country_signals.append(page_country_signals(
            url, text, _og_locale(tree), codes, schema.get(page_id, ())))
        tech.update(page_tech_signals(tree, html, seed_host))

    tld = tld_country(domain)
    target = vote(tld, country_signals)
    declared_country = tld or leader(Counter(
        country for page in country_signals for country in page.get("schema", ())))
    home = _home_pages(con, seed_url, {row[1]: row for row in rows}, schema)
    scope = market_scope(home, [item for items in schema.values() for item in items],
                         declared_country)
    return SiteProfile(
        target_country=target.country,
        target_country_confidence=target.confidence,
        target_country_candidates=target.candidates,
        market_scope=scope.scope,
        market_scope_city=scope.city,
        languages=site_languages(page_langs, hreflang_codes),
        page_count=len(rows),
        tech_signals=tuple(signal for signal, _ in sorted(
            tech.items(), key=lambda item: (-item[1], item[0]))),
    )


def site_languages(page_langs: list[str | None], hreflang_codes: list[str]) -> tuple[str, ...]:
    by_pages = Counter(_primary(lang) for lang in page_langs if lang and _primary(lang))
    ordered = [lang for lang, _ in sorted(by_pages.items(), key=lambda item: (-item[1], item[0]))]
    for code in hreflang_codes:
        lang = _primary(code)
        if lang and lang != "x" and lang not in ordered:
            ordered.append(lang)
    return tuple(ordered)


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


def _home_pages(
    con: duckdb.DuckDBPyConnection, seed_url: str, successful: dict[str, tuple],
    schema: dict[int, list[object]],
) -> list[ScopePage]:
    """A seed oldal (az átirányításait követve) és a hreflang-alternatívái, ha sikeresek."""
    https_redirect, trailing_slash = con.execute(
        "SELECT https_redirect, trailing_slash FROM site").fetchone()
    policy = UrlPolicy.from_seed(
        seed_url, https_redirect=bool(https_redirect), trailing_slash=trailing_slash)

    def find(url: str | None) -> tuple | None:
        for candidate in (url, slash_alternate(url) if url else None):
            if candidate in successful:
                return successful[candidate]
        return None

    url = normalize(seed_url, policy)
    seed_row = find(url)
    for _ in range(MAX_SEED_REDIRECTS):
        if seed_row is not None or url is None:
            break
        redirect = con.execute(
            "SELECT final_url FROM pages WHERE url IN (?, ?) AND final_url IS NOT NULL",
            [url, slash_alternate(url) or url],
        ).fetchone()
        url = normalize(redirect[0], policy) if redirect else None
        seed_row = find(url)
    if seed_row is None:
        return []
    rows = [seed_row]
    for pair in seed_row[5] or ():
        alternate = find(normalize(pair.split("|", 1)[-1], policy))
        if alternate is not None and alternate not in rows:
            rows.append(alternate)
    headings = _headings(con, [row[0] for row in rows])
    return [
        ScopePage(
            head_text=" ".join([title or "", description or "", *headings.get(page_id, [])]),
            main_content=main_content or "",
            schema_items=tuple(schema.get(page_id, ())),
        )
        for page_id, _, title, description, _, _, main_content, _ in rows
    ]


def _headings(con: duckdb.DuckDBPyConnection, page_ids: list[int]) -> dict[int, list[str]]:
    found: dict[int, list[str]] = defaultdict(list)
    for page_id, text in con.execute(
        "SELECT page_id, text FROM headings WHERE level <= 3 AND list_contains(?, page_id) "
        "ORDER BY page_id, ordinal", [page_ids],
    ).fetchall():
        found[page_id].append(text or "")
    return found


def _schema_items(con: duckdb.DuckDBPyConnection) -> dict[int, list[object]]:
    items: dict[int, list[object]] = defaultdict(list)
    for page_id, raw in con.execute(
        "SELECT page_id, json FROM schema_blocks WHERE type IS DISTINCT FROM 'invalid' "
        "ORDER BY page_id, ordinal"
    ).fetchall():
        try:
            items[page_id].append(json.loads(raw))
        except ValueError:
            continue
    return items


def _og_locale(tree: HTMLParser) -> str | None:
    for meta in tree.css("meta[property]"):
        if (meta.attributes.get("property") or "").strip().lower() == "og:locale":
            return meta.attributes.get("content")
    return None


def _primary(tag: str) -> str:
    return tag.strip().replace("_", "-").split("-", 1)[0].lower()
