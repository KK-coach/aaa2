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
  márka × kategória, segédlista, blog, szolgáltatás, egyéb.
- Canonical-duplikátum: az az oldal, amelynek a canonicalja egy másik alkalmas oldalra mutat
  (`canonical_targets`), és a saját szerepe a cél csoportjának szerepe, a cél oldalcsoportjába
  tartozik, így nem kap külön, azonos nevű entitást; a csoport szerepét nem változtatja meg.
  Ha a cél más szerepű, a canonical hibás: az oldal külön csoport marad.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qsl, urldefrag, urljoin, urlsplit

import duckdb

from aaa2.engine import queries as crawl
from aaa2.engine.normalize import page_url
from aaa2.entities import queries as extract_queries
from aaa2.entities import store

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
# A jogi oldalak fajtái a meglét-ellenőrzéshez (`legal_kind`): fajtánként a teljes slugok és a
# slug-kezdetek. A slug egy útvonalszegmens vagy lekérdezés-érték kisbetűvel, „_” helyett „-”,
# a végi sorszám nélkül („garancia_7” → „garancia”).
LEGAL_KINDS: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "terms": (frozenset({"aszf", "altalanos-szerzodesi-feltetelek", "vasarlasi-feltetelek",
                         "felhasznalasi-feltetelek", "jogi-nyilatkozat", "terms",
                         "terms-of-service", "terms-of-use", "terms-and-conditions"}), ()),
    "privacy": (frozenset({"privacy", "privacy-policy", "privacy-notice", "privacy-centre",
                           "privacy-center", "cookie-policy", "cookies", "sutik",
                           "suti-tajekoztato", "information/personaldata"}),
                ("adatved", "adatkezel")),
    "withdrawal": (frozenset({"withdrawal", "returns", "return-policy"}), ("elallas",)),
    "shipping_payment": (frozenset({"shipping", "shipping-policy", "delivery", "payment",
                                    "csomagkuldes", "kedvezo-csomagkuldes"}),
                         ("szallitas", "fizetes")),
    "warranty": (frozenset({"warranty"}), ("garancia", "jotallas", "szavatossag")),
    "contact": (frozenset({"impresszum", "impressum", "imprint", "information/contact"}),
                ("kapcsolat", "contact")),
}
LEGAL_TAIL_WORDS = frozenset({
    "es", "nyilatkozat", "tajekoztato", "feltetelek", "jog", "szabalyzat", "informaciok",
    "modok", "fizetes", "fizetesi", "szallitas", "szallitasi", "iranyelvek", "policy", "us"})
# A segédlista (nem valódi kategória): a listaoldal neve szerint (az utolsó útvonalszegmens
# szavai így kezdődnek), és URL szerint (slug vagy lekérdezés).
HELPER_LIST_WORDS = ("kifuto", "akcio", "ujdonsag", "outlet", "ajandek")
HELPER_SLUGS = frozenset({"sitemap", "oldalterkep", "information/sitemap", "hibabejelentes",
                          "special=1"})
# Platformszabály: a Shoprenter a `<body>` osztályával jelöli az oldal fajtáját.
SHOPRENTER_BODY = {"category-list-body": "category", "special-list-body": "list",
                   "latest-list-body": "list"}
BODY_CLASS = re.compile(r"<body\b[^>]*\bclass=[\"']([^\"']*)[\"']", re.IGNORECASE)
NAV_POSITIONS = ("nav", "aside", "footer")
PAGE_TYPES = ("product", "category", "brand_category", "list", "blog", "service", "other")
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
    hreflang = {meta.page_id: meta.hreflang for meta in crawl.page_metas(con)}
    rows = [(page.page_id, page.url, page.lang, page.title, page.h1, hreflang[page.page_id])
            for page in crawl.rendered_pages(con)]
    homes = {page_url(u) for u in home_urls(con)}
    nodes = schema_nodes(con)
    code_pages = {b.page_id for b in extract_queries.blocks(con)
                  if b.kind == "code" and b.region == "content"}
    base = {page_id: group_key(url, hreflang) for page_id, url, _, _, _, hreflang in rows}
    groups = dict(base)
    site_pages = {page_url(url) for _, url, _, _, _, _ in rows}
    linkers: dict[str, set[str]] = defaultdict(set)
    for from_id, to_id in sorted({(link.from_page_id, link.to_page_id)
                                  for link in crawl.links(con)
                                  if link.to_page_id is not None
                                  and link.position in NAV_POSITIONS}):
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
        members[base[page_id]].append(page_id)
    group_role: dict[str, str] = {}
    for key, page_ids in members.items():
        roles = [own[p] for p in page_ids]
        if any(reason in ("home", "support_url") for _, reason in roles):
            group_role[key] = "support"
        else:
            group_role[key] = min((r for r, _ in roles),
                                  key=lambda r: (ENTITY_ROLES + ("support",)).index(r))
    # a canonical-duplikátum a cél csoportjába kerül, ha a saját szerepe a cél csoportjáé; a
    # csoport szerepét a duplikátum nem változtatja meg
    for page_id, target in canonical_targets(con, {p: url for p, url, *_ in rows}).items():
        if own[page_id][0] == group_role[base[target]]:
            groups[page_id] = base[target]
    out: dict[int, PageInfo] = {}
    for page_id, url, lang, title, h1, _ in rows:
        key = groups[page_id]
        role = group_role[key]
        mine, reason = own[page_id]
        out[page_id] = PageInfo(page_id, url, primary_lang(lang), title, h1, key, role,
                                reason if mine == role else f"group:{role}")
    return out


def canonical_key(url: str) -> str:
    """A canonical-összevetés kulcsa: töredék és záró perjel nélkül, a lekérdezéssel (a
    `?tab=` változatok külön oldalak)."""
    parts = urlsplit(url.split("#", 1)[0])
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/') or '/'}" \
        + (f"?{parts.query}" if parts.query else "")


def canonical_targets(con: duckdb.DuckDBPyConnection,
                      urls: Mapping[int, str]) -> dict[int, int]:
    """Oldal → a canonical szerinti eredeti oldal, az `urls` oldalai között (a láncot az
    eredetiig követve). Kimarad, akinek nincs canonicalja, akié önmagára vagy a készleten
    kívülre mutat, és akinek a lánca körbeér."""
    node = {canonical_key(url): page_id for page_id, url in urls.items()}
    declared: dict[int, int] = {}
    for meta in crawl.page_metas(con):
        if meta.canonical is None or meta.page_id not in urls:
            continue
        target = node.get(canonical_key(urljoin(urls[meta.page_id], meta.canonical)))
        if target is not None and target != meta.page_id:
            declared[meta.page_id] = target
    found: dict[int, int] = {}
    for page_id, target in declared.items():
        seen = {page_id}
        while target in declared and target not in seen:
            seen.add(target)
            target = declared[target]
        if target not in seen:
            found[page_id] = target
    return found


def _slugs(url: str) -> list[str]:
    """Az URL slugjai: az útvonalszegmensek, a lekérdezés-értékek és a `kulcs=érték` párok,
    kisbetűvel, „_” helyett „-”, a végi sorszám nélkül („garancia_7” → „garancia”)."""
    parts = urlsplit(url)
    pieces = [p for p in parts.path.split("/") if p]
    pieces += [value for _, value in parse_qsl(parts.query)]
    found = [re.sub(r"-\d+$", "", piece.lower().replace("_", "-")) for piece in pieces]
    return found + [f"{key.lower()}={value.lower()}" for key, value in parse_qsl(parts.query)]


def legal_kind(url: str) -> str | None:
    """A jogi oldal fajtája az URL szerint (`LEGAL_KINDS`: terms, privacy, withdrawal,
    shipping_payment, warranty, contact), vagy None. Egy slug teljes egyezése vagy a fajta
    slug-kezdete számít („adatvedelmi-nyilatkozat”, „vasarlasi_feltetelek_5”, „garancia_7”,
    „withdrawal”); a slug-kezdet után csak a `LEGAL_TAIL_WORDS` szavai állhatnak
    („szallitas-es-fizetes” igen, „garancialis-javitas-blog” nem)."""
    slugs = _slugs(url)
    for kind, (exact, prefixes) in LEGAL_KINDS.items():
        for slug in slugs:
            first, *rest = slug.split("-")
            if slug in exact or (prefixes and first.startswith(prefixes)
                                 and set(rest) <= LEGAL_TAIL_WORDS):
                return kind
    return None


def helper_list(url: str) -> bool:
    """Segédlista az URL szerint (`HELPER_SLUGS`): oldaltérkép, hibabejelentés, akciós lista
    (`special=1`)."""
    return any(slug in HELPER_SLUGS for slug in _slugs(url))


def helper_list_name(url: str) -> bool:
    """A listaoldal neve szerint segédlista: az utolsó útvonalszegmens egy szava
    `HELPER_LIST_WORDS` kezdetű (kifutó, akció / akciós, újdonság, outlet, ajándék). Csak
    listaoldalra kérdezendő: a cikk címében álló „ajándék” nem segédlista."""
    path = [p for p in urlsplit(url).path.split("/") if p]
    words = re.split(r"[-_]+", path[-1].lower()) if path else []
    return any(word.startswith(HELPER_LIST_WORDS) for word in words)


def platform_types(con: duckdb.DuckDBPyConnection, page_ids: Sequence[int]) -> dict[int, str]:
    """Oldaltípus a webshop-platform jeléből. Shoprenter (`SHOPRENTER_BODY`): a `<body>`
    `category-list-body` osztálya kategóriaoldal, a `special-list-body` és a
    `latest-list-body` segédlista. Más platformra nincs szabály: az oldal kimarad."""
    found: dict[int, str] = {}
    for page_id in page_ids:
        match = BODY_CLASS.search(crawl.rendered_dom(con, page_id) or "")
        classes = set(match.group(1).split()) if match else set()
        kind = next((kind for name, kind in SHOPRENTER_BODY.items() if name in classes), None)
        if kind is not None:
            found[page_id] = kind
    return found


def page_types(con: duckdb.DuckDBPyConnection,
               patterns: Mapping[str, Sequence[str]] | None = None) -> dict[int, str]:
    """Oldaltípus (`PAGE_TYPES`) az alkalmas oldalakra, sorrendben:

    1. a site-fájl `[page_types]` mintái (típus → regexek a normalizált URL-re, `re.search`),
       a `PAGE_TYPES` sorrendjében; a site szerkezetéből, pl. a márka × kategória oldal;
    2. segédlista az URL szerint (`helper_list`: oldaltérkép, hibabejelentés, akciós lista) →
       list;
    3. a webshop-platform jele (`platform_types`): kategória vagy segédlista; a kategóriaoldal,
       amelynek a neve segédlistát jelöl (`helper_list_name`: kifutó, akciós, újdonság, outlet,
       ajándék), szintén list. A segédlistának nincs kategória-entitása és fő entitása;
    4. az oldalra mutató JSON-LD vagy microdata `Product` → product, `Service` → service (a
       microdata forrása és az oldalra mutatás szabálya: `crawl.schema_items`);
    5. `BlogPosting` / `NewsArticle` csomópont → blog (az `Article` nem: a WordPress SEO-bővítménye
       minden oldalra teszi);
    6. különben other."""
    compiled = {kind: [re.compile(p) for p in (patterns or {}).get(kind, ())]
                for kind in PAGE_TYPES}
    nodes = schema_nodes(con)
    found: dict[int, str] = {}
    rendered = [(page.page_id, page.url) for page in crawl.rendered_pages(con)]
    platform = platform_types(con, [page_id for page_id, _ in rendered])
    for page_id, url in rendered:
        kind = next((k for k in PAGE_TYPES if any(p.search(url) for p in compiled[k])), None)
        if kind is None and helper_list(url):
            kind = "list"
        if kind is None and page_id in platform:
            kind = "list" if helper_list_name(url) else platform[page_id]
        elif kind == "category" and helper_list_name(url):
            kind = "list"
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
    """Oldalanként az első `BreadcrumbList` (JSON-LD, ennek híján microdata) elemei sorrendben:
    (név, URL vagy None)."""
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


def entity_page_ids(roles: Mapping[int, PageInfo]) -> set[int]:
    """Az entitásoldalak (`ENTITY_ROLES` szerepű oldalak) azonosítói; a szabálykör ezzel dönti el,
    hogy egy anchor entitásoldalra vagy segédoldalra mutat-e."""
    return {page_id for page_id, info in roles.items() if info.role in ENTITY_ROLES}


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
    (kisbetűvel, „_” helyett „-”) egy `SUPPORT_SLUGS` slug, vagy az URL jogi oldal
    (`legal_kind`, a kapcsolat-oldal nélkül: azt a gráf külön kezeli); a „/privacy-policy/”, a
    „?tab=privacy_policy” és a „/garancia_7” igen, az „/eprivacy-and-gdpr-diagnostics/” nem."""
    parts = urlsplit(url)
    pieces = [p for p in parts.path.split("/") if p]
    pieces += [value for _, value in parse_qsl(parts.query)]
    return any(piece.lower().replace("_", "-") in SUPPORT_SLUGS for piece in pieces) \
        or legal_kind(url) not in (None, "contact")


def service_pages(con: duckdb.DuckDBPyConnection) -> set[int]:
    """Azok az oldalak, ahol a kinyerő modell legutóbbi kész rekordjában az első fő entitás
    (`primary_entities`) szolgáltatás típusú, és a H1 vagy a title megnevezi."""
    from aaa2.entities.gate import occurs

    found: set[int] = set()
    seen: set[int] = set()
    texts = {page.page_id: (page.h1, page.title) for page in crawl.pages(con)}
    for page_id, record in store.entity_runs_for_service_pages(con):
        if page_id not in texts:
            continue
        h1, title = texts[page_id]
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
    """Oldalanként a schema.org-elemek (`crawl.schema_items`: JSON-LD, utána microdata) legfelső
    szintű típusos csomópontjai (és a `@graph` elemei)."""
    found: dict[int, list[dict]] = defaultdict(list)
    for item in crawl.schema_items(con):
        page_id, block = item.page_id, item.data
        for node in _as_list(block):
            if isinstance(node, dict):
                graph = node.get("@graph")
                found[page_id] += [n for n in _as_list(graph) if isinstance(n, dict)] \
                    if graph is not None else [node]
    return found


def home_urls(con: duckdb.DuckDBPyConnection) -> set[str]:
    site = crawl.site(con)
    if site is None:
        return set()
    return set(site.home_urls) if site.home_urls is not None else {site.seed_url}


def _short(value: object) -> str:
    text = str(value).strip()
    return re.split(r"[/#:]", text)[-1] if text else text


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value] if value is not None else []
