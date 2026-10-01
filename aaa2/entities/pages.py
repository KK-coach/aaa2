"""Oldalcsoportok és oldalszerepek a site-szintű entitásokhoz (M2 spec, M2/6, 3. pont).

- Oldalcsoport: a hreflang-párok egy csoport (az oldal `hreflang` URL-jei szerint); ahol nincs
  hreflang, a lekérdezés és a töredék nélküli URL (a fülek, pl. `?tab=api`, egy csoport).
- Szerep oldalanként, sorrendben:
  - `support`: kezdőoldal (`site.home_urls`), jogi, köszönő és hibaoldal (`support_url`), vagy
    `ContactPage` / `AboutPage` / `ProfilePage` / `CollectionPage` / `SearchResultsPage`
    csomópont;
  - `offer`: az oldalra mutató (`url` vagy `@id` a töredék nélkül) JSON-LD `Service`; kivéve a
    gyűjtőoldalt (`offer_hub`, szerepe `support`): a saját `Service` csomópont `hasPart`-ja
    legalább `HUB_MIN_PARTS` olyan szolgáltatást sorol, amelynek saját oldala van a site-on,
    és maga nem része másik szolgáltatásnak (nincs `isPartOf`): ajánlatokat listáz, maga nem
    ajánlat;
  - `product`: az oldalra mutató JSON-LD `Product`;
  - `offer` az általános `Article` csomópontú oldal is (a WordPress SEO-bővítménye a statikus
    oldalakra is ráteszi), ha nincs rajta bejegyzés-típus (`BlogPosting`, `NewsArticle`,
    `TechArticle`), és a kinyerés első fő entitása (`primary_entities`) szolgáltatás, amelyet
    a H1 vagy a title megnevez (`service_pages`; LLM-kinyerés nélkül az oldal cikk marad);
  - `article`: `Article` / `BlogPosting` / `NewsArticle` / `TechArticle` csomópont;
  - `component`: dokumentációs oldal: van kódblokkja (a `blocks` táblából, tehát a blokkok
    felépítése után), a H1 legfeljebb `COMPONENT_H1_WORDS`
    szó, és legalább `COMPONENT_MIN_LINKERS` más csoportból mutat rá navigációs (nav, aside)
    link;
  - különben `support` (nincs entitásoldal-bizonyíték).
- A csoport szerepe a tagjaié közül az erősebb (offer > product > component > article); ha a
  csoport bármely tagja kezdőoldal, jogi vagy köszönőoldal, a csoport `support`.
- Oldaltípus (`page_types`, a crawl-jelentéshez és a webshop-szintekhez): termék, kategória,
  márka × kategória, blog, szolgáltatás, egyéb.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qsl, urldefrag, urlsplit, urlunsplit

import duckdb

ENTITY_ROLES = ("offer", "product", "component", "article")
ROLE_TYPE = {"offer": ("service", None), "product": ("product", None),
             "component": ("tech", "component"), "article": ("work", "article")}
ARTICLE_TYPES = frozenset({"Article", "BlogPosting", "NewsArticle", "TechArticle"})
POST_TYPES = ARTICLE_TYPES - {"Article"}
SUPPORT_TYPES = frozenset({"ContactPage", "AboutPage", "ProfilePage", "CollectionPage",
                           "SearchResultsPage", "CheckoutPage"})
# A jogi, köszönő és hibaoldal ismert slugjai (kisbetűvel, „_” helyett „-”); egy teljes
# útvonalszegmens vagy lekérdezés-érték egyezik velük (`support_url`), szórészlet nem.
SUPPORT_SLUGS = frozenset({
    "privacy", "privacy-policy", "privacy-notice", "privacy-centre", "privacy-center",
    "cookie-policy", "cookies", "terms", "terms-of-service", "terms-of-use",
    "terms-and-conditions", "impressum", "impresszum", "adatvedelem", "adatvedelmi-tajekoztato",
    "adatkezelesi-tajekoztato", "adatkezeles", "jogi-nyilatkozat", "sutik", "suti-tajekoztato",
    "aszf", "felhasznalasi-feltetelek", "thank-you", "thanks",
    "koszonjuk", "grazie", "404", "et-code-snippet", "et-code-snippet-type"})
NAV_POSITIONS = ("nav", "aside", "footer")
PAGE_TYPES = ("product", "category", "brand_category", "blog", "service", "other")
BLOG_TYPES = frozenset({"BlogPosting", "NewsArticle"})
HUB_MIN_PARTS = 2
COMPONENT_H1_WORDS = 4
COMPONENT_MIN_LINKERS = 2


@dataclass(frozen=True)
class PageInfo:
    page_id: int
    url: str
    lang: str | None
    title: str | None
    h1: str | None
    group: str
    role: str
    reason: str


def page_url(url: str) -> str:
    """Lekérdezés és töredék nélkül, a záró perjel nélkül (a csoportkulcshoz)."""
    parts = urlsplit(urldefrag(url)[0])
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", "", ""))


def same_page(a: str | None, b: str) -> bool:
    return bool(a) and urlsplit(urldefrag(a)[0])._replace(query="").geturl().rstrip("/") \
        == urlsplit(b)._replace(query="", fragment="").geturl().rstrip("/")


def primary_lang(tag: str | None) -> str | None:
    return tag.strip().replace("_", "-").split("-", 1)[0].lower() if tag else None


def group_key(url: str, hreflang: list[str] | None) -> str:
    urls = sorted({entry.split("|", 1)[-1] for entry in hreflang or [] if entry})
    return "hreflang:" + " ".join(page_url(u) for u in urls) if urls else page_url(url)


def page_roles(con: duckdb.DuckDBPyConnection) -> dict[int, PageInfo]:
    """Az alkalmas oldalak csoportja és szerepe (lásd a modul leírását)."""
    rows = con.execute(
        "SELECT page_id, url, lang, title, h1, hreflang FROM pages "
        "WHERE status BETWEEN 200 AND 299 AND error IS NULL AND rendered_html IS NOT NULL "
        "ORDER BY page_id").fetchall()
    homes = {page_url(u) for u in home_urls(con)}
    nodes = schema_nodes(con)
    code_pages = {page_id for (page_id,) in con.execute(
        "SELECT DISTINCT page_id FROM blocks WHERE kind = 'code' AND region = 'content'"
    ).fetchall()}
    groups = {page_id: group_key(url, hreflang) for page_id, url, _, _, _, hreflang in rows}
    site_pages = {page_url(url) for _, url, _, _, _, _ in rows}
    linkers: dict[str, set[str]] = defaultdict(set)
    for from_id, to_id in con.execute(
            "SELECT from_page_id, to_page_id FROM links WHERE to_page_id IS NOT NULL "
            "AND list_contains(?, position)", [list(NAV_POSITIONS)]).fetchall():
        if from_id in groups and to_id in groups and groups[from_id] != groups[to_id]:
            linkers[groups[to_id]].add(groups[from_id])
    services = service_pages(con)
    own: dict[int, tuple[str, str]] = {}
    for page_id, url, _, _, h1, _ in rows:
        own[page_id] = _own_role(page_id, url, h1, homes, nodes.get(page_id, []),
                                 page_id in code_pages, len(linkers[groups[page_id]]),
                                 page_id in services, site_pages)
    members: dict[str, list[int]] = defaultdict(list)
    for page_id in own:
        members[groups[page_id]].append(page_id)
    out: dict[int, PageInfo] = {}
    for key, page_ids in members.items():
        roles = [own[p] for p in page_ids]
        if any(reason in ("home", "support_url") for _, reason in roles):
            role = "support"
        else:
            role = min((r for r, _ in roles), key=lambda r: (ENTITY_ROLES + ("support",)).index(r))
        for page_id, url, lang, title, h1, _ in rows:
            if page_id in page_ids:
                mine, reason = own[page_id]
                out[page_id] = PageInfo(page_id, url, primary_lang(lang), title, h1, key, role,
                                        reason if mine == role else f"group:{role}")
    return out


def page_types(con: duckdb.DuckDBPyConnection,
               patterns: Mapping[str, Sequence[str]] | None = None) -> dict[int, str]:
    """Oldaltípus (`PAGE_TYPES`) az alkalmas oldalakra, sorrendben:

    1. a site-fájl `[page_types]` mintái (típus → regexek a normalizált URL-re, `re.search`),
       a `PAGE_TYPES` sorrendjében; a site szerkezetéből, pl. a márka × kategória oldal;
    2. az oldalra mutató JSON-LD `Product` → product, `Service` → service;
    3. `BlogPosting` / `NewsArticle` csomópont → blog (az `Article` nem: a WordPress SEO-bővítménye
       minden oldalra teszi);
    4. különben other."""
    compiled = {kind: [re.compile(p) for p in (patterns or {}).get(kind, ())]
                for kind in PAGE_TYPES}
    nodes = schema_nodes(con)
    found: dict[int, str] = {}
    for page_id, url in con.execute(
            "SELECT page_id, url FROM pages WHERE status BETWEEN 200 AND 299 AND error IS NULL "
            "AND rendered_html IS NOT NULL ORDER BY page_id").fetchall():
        kind = next((k for k in PAGE_TYPES if any(p.search(url) for p in compiled[k])), None)
        own = nodes.get(page_id, [])
        for schema, name in (("Product", "product"), ("Service", "service")):
            if kind is None and any(
                    schema in {_short(t) for t in _as_list(node.get("@type"))}
                    and (same_page(node.get("url"), url) or same_page(node.get("@id"), url))
                    for node in own):
                kind = name
        types = {_short(t) for node in own for t in _as_list(node.get("@type"))}
        if kind is None and types & BLOG_TYPES:
            kind = "blog"
        found[page_id] = kind or "other"
    return found


def breadcrumbs(con: duckdb.DuckDBPyConnection) -> dict[int, list[tuple[str, str | None]]]:
    """Oldalanként az első JSON-LD `BreadcrumbList` elemei sorrendben: (név, URL vagy None)."""
    found: dict[int, list[tuple[str, str | None]]] = {}
    for page_id, nodes in schema_nodes(con).items():
        for node in nodes:
            if "BreadcrumbList" not in {_short(t) for t in _as_list(node.get("@type"))}:
                continue
            items = sorted((i for i in _as_list(node.get("itemListElement"))
                            if isinstance(i, dict)), key=lambda i: _position(i.get("position")))
            trail = []
            for item in items:
                target = item.get("item")
                url = target.get("@id") or target.get("url") if isinstance(target, dict) \
                    else target
                name = item.get("name") or (target.get("name") if isinstance(target, dict)
                                            else None)
                if name:
                    trail.append((str(name).strip(), str(url) if url else None))
            found.setdefault(page_id, trail)
    return found


def _position(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def entity_groups(roles: dict[int, PageInfo]) -> dict[str, list[PageInfo]]:
    """Az entitásoldal-csoportok tagjai oldalszám szerint."""
    found: dict[str, list[PageInfo]] = defaultdict(list)
    for info in sorted(roles.values(), key=lambda i: i.page_id):
        if info.role in ENTITY_ROLES:
            found[info.group].append(info)
    return dict(found)


def representative(members: list[PageInfo], site_lang: str | None) -> PageInfo:
    """A csoport oldala a site elsődleges nyelvén (ha nincs, bármelyik), azon belül a
    lekérdezés nélküli URL, aztán a legkisebb oldalszám."""
    pool = [m for m in members if m.lang and m.lang == site_lang] or members
    return min(pool, key=lambda m: ("?" in m.url, m.page_id))


def support_url(url: str) -> bool:
    """Jogi, köszönő vagy hibaoldal az URL szerint: egy útvonalszegmens vagy lekérdezés-érték
    (kisbetűvel, „_” helyett „-”) egy `SUPPORT_SLUGS` slug; a „/privacy-policy/” és a
    „?tab=privacy_policy” igen, az „/eprivacy-and-gdpr-diagnostics/” nem."""
    parts = urlsplit(url)
    pieces = [p for p in parts.path.split("/") if p]
    pieces += [value for _, value in parse_qsl(parts.query)]
    return any(piece.lower().replace("_", "-") in SUPPORT_SLUGS for piece in pieces)


def service_pages(con: duckdb.DuckDBPyConnection) -> set[int]:
    """Azok az oldalak, ahol a kinyerő modell legutóbbi kész rekordjában az első fő entitás
    (`primary_entities`) szolgáltatás típusú, és a H1 vagy a title megnevezi."""
    from aaa2.entities.gate import occurs

    found: set[int] = set()
    seen: set[int] = set()
    for page_id, record, h1, title in con.execute(
            "SELECT p.page_id, coalesce(p.refined, p.extraction), g.h1, g.title "
            "FROM entity_run_pages p JOIN entity_runs r USING (run_id) "
            "JOIN pages g ON g.page_id = p.page_id WHERE r.method = 'llm' AND p.status = 'done' "
            "ORDER BY p.run_id DESC, p.finished_at DESC").fetchall():
        if page_id in seen or record is None:
            continue
        seen.add(page_id)
        data = json.loads(record)
        names = [n for n in data.get("primary_entities") or [] if isinstance(n, str)]
        if not names:
            continue
        kinds = {e.get("type") for e in data.get("entities") or []
                 if isinstance(e, dict) and e.get("canonical_name") == names[0]}
        if kinds == {"service"} and any(text and occurs(names[0], text)
                                        for text in (h1, title)):
            found.add(page_id)
    return found


def _own_role(page_id: int, url: str, h1: str | None, homes: set[str], nodes: list[dict],
              has_code: bool, linkers: int, service_named: bool = False,
              site_pages: frozenset[str] | set[str] = frozenset()) -> tuple[str, str]:
    if page_url(url) in homes:
        return "support", "home"
    if support_url(url):
        return "support", "support_url"
    types = {_short(t) for node in nodes for t in _as_list(node.get("@type"))}
    for kind, role in (("Service", "offer"), ("Product", "product")):
        own = [node for node in nodes
               if kind in {_short(t) for t in _as_list(node.get("@type"))}
               and (same_page(node.get("url"), url) or same_page(node.get("@id"), url))]
        if own and kind == "Service" and any(offer_hub(node, url, site_pages) for node in own):
            return "support", "offer_hub"
        if own:
            return role, f"schema_{kind.lower()}"
    if types & ARTICLE_TYPES and not types & POST_TYPES and service_named:
        return "offer", "article_service"
    if types & ARTICLE_TYPES:
        return "article", "schema_article"
    if types & SUPPORT_TYPES:
        return "support", "schema_support"
    if has_code and h1 and len(h1.split()) <= COMPONENT_H1_WORDS \
            and linkers >= COMPONENT_MIN_LINKERS:
        return "component", "docs_component"
    return "support", "no_entity_evidence"


def offer_hub(node: dict, url: str, site_pages: frozenset[str] | set[str]) -> bool:
    """Gyűjtőoldal-e a saját `Service` csomópont: a `hasPart`-ja legalább `HUB_MIN_PARTS` más,
    a site-on saját oldallal bíró szolgáltatást sorol (`@id` vagy `url`, a töredék nélkül), és
    maga nem része másiknak (nincs `isPartOf`)."""
    if node.get("isPartOf") is not None:
        return False
    own = page_url(url)
    parts = set()
    for part in _as_list(node.get("hasPart")):
        ref = part.get("@id") or part.get("url") if isinstance(part, dict) else part
        if isinstance(ref, str) and page_url(ref) in site_pages and page_url(ref) != own:
            parts.add(page_url(ref))
    return len(parts) >= HUB_MIN_PARTS


def schema_nodes(con: duckdb.DuckDBPyConnection) -> dict[int, list[dict]]:
    """Oldalanként a JSON-LD blokkok legfelső szintű típusos csomópontjai (és a `@graph`
    elemei)."""
    found: dict[int, list[dict]] = defaultdict(list)
    for page_id, raw in con.execute(
            "SELECT page_id, json FROM schema_blocks WHERE type IS DISTINCT FROM 'invalid' "
            "ORDER BY page_id, ordinal").fetchall():
        try:
            block = json.loads(raw)
        except ValueError:
            continue
        for node in _as_list(block):
            if isinstance(node, dict):
                graph = node.get("@graph")
                found[page_id] += [n for n in _as_list(graph) if isinstance(n, dict)] \
                    if graph is not None else [node]
    return found


def home_urls(con: duckdb.DuckDBPyConnection) -> set[str]:
    row = con.execute("SELECT home_urls, seed_url FROM site").fetchone()
    if row is None:
        return set()
    return set(row[0]) if row[0] is not None else {row[1]}


def _short(value: object) -> str:
    text = str(value).strip()
    return re.split(r"[/#:]", text)[-1] if text else text


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value] if value is not None else []
