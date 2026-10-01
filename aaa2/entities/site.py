"""Site-szintű entitások (M2 spec, „M2/6 — Site-szintű entitások”): az oldalankénti kinyerés
(szabálykör és LLM-kör) után, LLM-hívás nélkül.

- Oldalhoz kötött entitás (3. pont): minden entitásoldal-csoporthoz (`pages.entity_groups`)
  egy entitás. Nevei: a tagoldalak H1-e és title-je (a site-név nélkül, és az első elválasztó
  előtti része), az oldal saját JSON-LD csomópontjának neve (`name`, `headline`,
  `alternateName`), a más csoportból rá mutató anchorok (nem triviális, az anchor szövege az
  egész site-on csak ide mutat, legfeljebb `ANCHOR_MAX_WORDS` szó vagy egy tagoldal H1-e vagy
  title-je), a kártyacímek (az anchor
  blokkja előtti rövid heading, `_card_headings`); a nem elsődleges nyelvű
  tagoldal H1-e és title-je `hreflang` forrással. Kanonikus név: ajánlatnál és terméknél a
  leggyakoribb navigációs (nav, aside, footer) anchor az elsődleges nyelvű oldalra (azonos
  számnál a több szavú), különben a JSON-LD név, a title első része, a H1; komponensnél és
  cikknél a H1. Típus a szerepből (`pages.ROLE_TYPE`), ajánlatnál `tier = core`. Az azonos nevű
  (név vagy alias kulcsa) meglévő, kompatibilis típusú entitások ebbe olvadnak
  (`page_identity`); komponensnél a concept is kompatibilis (a típus ingadozik), ajánlatnál
  nem (a fogalom külön entitás). A JSON-LD név bármelyik oldal olyan csomópontjából jön, amely
  a tagoldalra mutat (`url` vagy `@id`). Említés: a tagoldalak title- és H1-blokkja, az
  anchorok blokkjai és a kártyacímek. Az a más típusú, azonos nevű entitás, amelynek minden
  blokkos említése ezekben az azonosító blokkokban áll, beolvad (`page_identity_position`).
- Csomag (4. pont): egy ajánlatoldal árazási sora (táblázatsor vagy kártya, ahol az árat
  tartalmazó blokk előtt legfeljebb `PRICE_LOOKBACK` blokkal rövid név áll; az óradíj-sorok
  kimaradnak) és az oldal saját `Service` csomópontjának `hasOfferCatalog` elemei:
  `tier = package`, `part_of` a fő ajánlathoz. A hreflang-pár oldalak azonos sorszámú árazási
  sora ugyanaz a csomag, ha a két oldal árazási sorainak száma egyezik
  (`hreflang_pricing_row`).
- Módszertani lépés (4. pont): az LLM-ből jött, fő ajánlathoz és csomaghoz nem kötött service
  → concept / method, `tier = step` (`type_changed_from = service`).
- Demótartalom (6. pont): nem-tech típusú entitás (`DEMO_TYPES`), amelynek blokkos említései
  legalább `DEMO_SHARE` részben demó-környezetben állnak: kódblokk, „lorem ipsum” szöveg,
  kitöltőszöveg-oldal (`placeholder.placeholder_pages`: a szövege döntően lorem ipsum), vagy
  olyan oldal, ahol ugyanennek az entitásnak kódblokkos említése is van (a példa kimenete).
  Jelölés: `flags` demo. A kitöltőszöveg-oldal oldalhoz kötött entitást sem hoz létre.
- Sablonismétlés (6. pont): az említés egysége (kódblokkban a sora, máshol a blokk szövege,
  kulcs szerint, blokkfajtánként) legalább `TEMPLATE_MIN_GROUPS` és az oldalcsoportok
  `TEMPLATE_MIN_SHARE` részén áll → `page_entities.flags` template; az entitás template, ha
  minden blokkos említése az. A title-blokk kimarad.
- Összevonás (7. pont), fuzzy nélkül: a hreflang-pár oldalak azonos helyű headingjei (a
  H2-szakaszok elölről és hátulról párban, amíg a H3-ak száma egyezik; a headinget egészében
  lefedő egyetlen említés entitása, azonos típus, vagy service és csak headingben álló
  concept: `hreflang_place`), és az írásmód-normalizált név (kis-nagybetű, ékezet, szóköz,
  aláhúzás, kötőjel, perjel, gondolatjel, zárójel nélkül, „&” = „és” = „and”; a zárójeles
  rövidítésből csak a hosszú kifejtés; „/” vagy „@” tartalmú névnél elválasztó-normalizálás
  nincs; szervezetnél a név végi jogi forma nélkül is, `LEGAL_FORMS`; azonos típus:
  `normalized_name`). Két különböző oldalhoz kötött entitás, két eltérő
  szint (core, package) és két nem kompatibilis altípus (package kontra component) nem olvad
  össze; a hreflang-párban a kanonikus nyelvű oldal entitása marad.
- Fogalom és ajánlat (9. pont): a service típusú entitás LLM-említései, amelyeket az LLM
  fogalomként talált (a tárolt rekordok szerint), fogalom-entitáshoz kerülnek (`type_split`),
  kivéve a heading-, title- vagy kártyablokkot egészében lefedő említést és az ajánlat
  valamelyik több szavas nevére szóló említést;
  a fő ajánlat és a csomag `offers` kapcsolattal kötődik ezekhez, és az olyan fogalomhoz,
  amelynek normalizált kulcsa a nevének vagy összetett címkéje egy részének kulcsa
  (`label_parts`: „UX & Konverzióoptimalizálás” → UX, Konverzióoptimalizálás).
- Webshop-szintek (9a pont, `shop.py`): kategória, márka, termékcsalád, a termék tulajdonságai
  és kapcsolatai, a csomagok után.
- Site-szintű felülbírálat (4. pont, `overrides.py`, `config/sites/<domain>.toml`): az
  ajánlat szintje (core, package, work_mode) név vagy URL szerint, a szintszabály után
  (`apply_overrides`). A kanonikus név nyelve a beállításé, különben a site gyökér-URL-jéé
  (`overrides.canonical_language`).
- Minden összevonás, leválasztás és felülbírálat a `merge_log`-ba kerül; futásonként egy
  `entity_runs` sor (method = site).
"""
from __future__ import annotations

import html as html_lib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import duckdb
import zstandard

from aaa2.entities.dom import parse_blocks
from aaa2.entities.extract import surface_offsets
from aaa2.entities.overrides import (
    SiteConfig,
    canonical_language,
    load_site_config,
    site_domain,
)
from aaa2.entities.pages import (
    NAV_POSITIONS,
    ROLE_TYPE,
    PageInfo,
    entity_groups,
    page_roles,
    page_url,
    representative,
    same_page,
)
from aaa2.entities.placeholder import placeholder_pages
from aaa2.entities.rules import (
    SOURCE_STRENGTH,
    TITLE_SEPARATORS,
    alias_key,
    find_name,
    stronger_source,
    title_endings,
    trivial_anchor,
)
from aaa2.entities.shop import run_shop

ANCHOR_MAX_WORDS = 6
CARD_LOOKBACK = 3
CARD_TITLE_WORDS = 5
SITE_PREFIX_MIN = 4
IDENTITY_EXCLUDED_TYPES = ("person", "org", "brand", "place")
PRICE_LOOKBACK = 4
PACKAGE_NAME_WORDS = 6
DEMO_TYPES = ("person", "place", "org", "brand", "work", "product", "event")
DEMO_SHARE = 0.9
TEMPLATE_MIN_GROUPS = 3
TEMPLATE_MIN_SHARE = 0.15
PAGE_ALIAS_SOURCES = ("h1", "title", "nav", "anchor", "schema", "hreflang")
COMPATIBLE = {"offer": ("service",), "product": ("product",), "article": ("work",),
              "component": ("tech", "concept")}
SCHEMA_SELF_TYPES = {"offer": ("Service",), "product": ("Product",),
                     "article": ("Article", "BlogPosting", "NewsArticle", "TechArticle")}

PRICE = re.compile(r"\d[\d\s.,–-]*\s*(?:Ft|HUF|€|EUR|\$|USD|£)|(?:€|\$|£)\s*\d", re.IGNORECASE)
RATE = re.compile(r"óradíj|hourly rate|\brate\s*:|\bdíj\s*:|munkadíj|billed at|"
                  r"/\s*(?:óra|hour)\b", re.IGNORECASE)
LOREM = re.compile(r"lorem ipsum", re.IGNORECASE)
PARENTHETICAL = re.compile(r"(.+?)\s*\(([^()]+)\)")
NORMAL_DROP = re.compile(r"[\s_\-/–—‐()]+")
LEGAL_FORMS = frozenset({"kft", "zrt", "bt", "ltd", "llc", "gmbh", "inc"})
# az `alias_key` után: az „és” ékezet nélkül „es”
CONJUNCTION = re.compile(r"\s*(?:\bes\b|\band\b|&)\s*")
SUBTYPE_CLASS = {"package": "distribution", "library": "distribution",
                 "framework": "distribution", "software": "distribution",
                 "platform": "distribution", "language": "distribution",
                 "component": "code", "api_symbol": "code", "feature": "code",
                 "line": "line", "variant": "variant"}
LABEL_SPLIT = re.compile(r"\s+(?:&|és|and|\+|–|—|-)\s+|\s*[/,]\s*")
TIER_ORDER = {"core": 0, "package": 1, "work_mode": 1, None: 2, "step": 3}
TIER_GROUP = {"work_mode": "package"}
OFFER_LABEL_SOURCES = ("nav", "schema", "anchor")
ACRONYM = re.compile(r"[A-Z0-9][A-Z0-9&.+-]{1,5}")


@dataclass(frozen=True)
class Name:
    text: str
    source: str
    lang: str | None
    count: int = 1


@dataclass
class SiteRun:
    run_id: int
    roles: dict[str, int]
    page_entities: int = 0
    packages: int = 0
    steps: int = 0
    merges: Counter[str] = field(default_factory=Counter)
    anchor_mentions: int = 0
    offers: int = 0
    overrides: int = 0
    shop: dict = field(default_factory=dict)
    demo: list[str] = field(default_factory=list)
    placeholder_pages: list[str] = field(default_factory=list)
    template_mentions: int = 0
    template_entities: int = 0
    thresholds: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# összevonás
# ---------------------------------------------------------------------------


class Merger:
    """Entitások összevonása a `merge_log`-gal: az említések, a források, a bizonyítékok, a
    kapcsolatok és az aliasok a megtartott entitáshoz kerülnek. Az azonos helyű (oldal, blokk,
    szövegrész) említésből egy marad, a pozíciójától függetlenül (ez a `page_entities` kulcsa)."""

    def __init__(self, con: duckdb.DuckDBPyConnection, run_id: int,
                 clock: Callable[[], datetime]):
        self.con, self.run_id, self.clock = con, run_id, clock
        self.counts: Counter[str] = Counter()

    def merge(self, keep: int, remove: int, rule: str, evidence: dict | None = None) -> None:
        if keep == remove:
            return
        con = self.con
        kept = con.execute("SELECT name, aliases, source FROM entities WHERE entity_id = ?",
                           [keep]).fetchone()
        gone = con.execute("SELECT name, aliases, source FROM entities WHERE entity_id = ?",
                           [remove]).fetchone()
        if kept is None or gone is None:
            return
        for old, new in con.execute(
                "SELECT r.mention_id, k.mention_id FROM page_entities r JOIN page_entities k "
                "ON k.page_id = r.page_id AND k.block_id IS NOT DISTINCT FROM r.block_id "
                "AND k.char_start IS NOT DISTINCT FROM r.char_start "
                "AND k.char_end IS NOT DISTINCT FROM r.char_end "
                "AND (k.position = r.position OR r.block_id IS NOT NULL) "
                "WHERE r.entity_id = ? AND k.entity_id = ?", [remove, keep]).fetchall():
            con.execute(
                "INSERT INTO mention_sources (mention_id, source, run_id, llm_call_id, count) "
                "SELECT ?, source, run_id, llm_call_id, count FROM mention_sources "
                "WHERE mention_id = ? ON CONFLICT DO NOTHING", [new, old])
            con.execute("DELETE FROM mention_sources WHERE mention_id = ?", [old])
            con.execute("DELETE FROM page_entities WHERE mention_id = ?", [old])
        con.execute("UPDATE page_entities SET entity_id = ? WHERE entity_id = ?", [keep, remove])
        con.execute("UPDATE soft_checks SET entity_id = ? WHERE entity_id = ?", [keep, remove])
        for column, other in (("from_id", "to_id"), ("to_id", "from_id")):
            con.execute(
                f"DELETE FROM entity_relations r WHERE r.{column} = ? AND EXISTS (SELECT 1 FROM "
                f"entity_relations k WHERE k.{column} = ? AND k.{other} = r.{other} "
                f"AND k.type = r.type)", [remove, keep])
            con.execute(f"UPDATE entity_relations SET {column} = ? WHERE {column} = ?",
                        [keep, remove])
        con.execute("DELETE FROM entity_relations WHERE from_id = to_id")
        con.execute(
            "INSERT INTO entity_aliases (entity_id, alias, lang, source) SELECT ?, alias, lang, "
            "source FROM entity_aliases WHERE entity_id = ? ON CONFLICT DO NOTHING",
            [keep, remove])
        con.execute("DELETE FROM entity_aliases WHERE entity_id = ?", [remove])
        forms = [gone[0], *(gone[1] or [])]
        con.execute(
            "UPDATE entities SET aliases = list_distinct(list_filter(list_concat(coalesce("
            "aliases, []), ?), x -> x <> name)), source = ? WHERE entity_id = ?",
            [forms, stronger_source(kept[2], gone[2]), keep])
        con.execute("INSERT INTO entity_aliases (entity_id, alias, lang, source) "
                    "VALUES (?, ?, NULL, 'merge') ON CONFLICT DO NOTHING", [keep, gone[0]])
        con.execute(
            "INSERT INTO merge_log (run_id, kept_id, removed_id, kept_name, removed_name, rule, "
            "evidence, merged_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [self.run_id, keep, remove, kept[0], gone[0], rule,
             json.dumps(evidence or {}, ensure_ascii=False), self.clock()])
        con.execute("DELETE FROM entities WHERE entity_id = ?", [remove])
        self.counts[rule] += 1


# ---------------------------------------------------------------------------
# a futás
# ---------------------------------------------------------------------------


def run_site(con: duckdb.DuckDBPyConnection,
             clock: Callable[[], datetime] | None = None) -> SiteRun:
    clock = clock or _now
    started = clock()
    roles = page_roles(con)
    config = load_site_config(site_domain(con))
    site_lang = canonical_language(con, config)
    con.begin()
    try:
        (run_id,) = con.execute(
            "INSERT INTO entity_runs (started_at, method, llm_calls) VALUES (?, 'site', 0) "
            "RETURNING run_id", [started]).fetchone()
        run = SiteRun(run_id, dict(Counter(info.role for info in roles.values())))
        # a korábbi körök óta törölt entitásokra mutató kapcsolatok (újrafuttatáskor a
        # szabálykör az említés nélküli szabály-entitásokat törli)
        con.execute("DELETE FROM entity_relations WHERE from_id NOT IN (SELECT entity_id FROM "
                    "entities) OR to_id NOT IN (SELECT entity_id FROM entities)")
        merger = Merger(con, run_id, clock)
        context = _Context(con, roles, site_lang, run_id)
        anchored = _page_entities(context, merger, run)
        _packages(context, merger, run, anchored)
        run.shop = run_shop(context, merger, config).as_dict()
        _hreflang_place(context, merger)
        _normalized_merges(con, merger)
        split = _type_split(con, merger)
        _steps(con, run)
        _normalized_merges(con, merger)
        run.overrides = apply_overrides(context, merger, config)
        run.offers = _offers(con, split)
        run.placeholder_pages = sorted(roles[p].url for p in context.placeholder if p in roles)
        _demo(con, run, context.placeholder)
        _template(context, run)
        run.merges = merger.counts
        con.execute(
            "UPDATE entity_runs SET finished_at = ?, pages = ?, entities = ?, row_count = ?, "
            "skipped = ? WHERE run_id = ?",
            [clock(), len(roles), run.page_entities + run.packages, run.anchor_mentions,
             json.dumps({"roles": run.roles, "merges": dict(run.merges),
                         "packages": run.packages, "steps": run.steps, "offers": run.offers,
                         "overrides": run.overrides, "shop": run.shop,
                         "demo": run.demo, "placeholder_pages": run.placeholder_pages,
                         "template_mentions": run.template_mentions,
                         "template_entities": run.template_entities,
                         "thresholds": run.thresholds}, ensure_ascii=False), run_id])
        con.commit()
    except Exception:
        con.rollback()
        raise
    return run


class _Context:
    def __init__(self, con: duckdb.DuckDBPyConnection, roles: dict[int, PageInfo],
                 site_lang: str | None, run_id: int):
        self.con, self.roles, self.site_lang, self.run_id = con, roles, site_lang, run_id
        self.placeholder = placeholder_pages(con)
        self.groups = {group: members for group, members in entity_groups(roles).items()
                       if not all(m.page_id in self.placeholder for m in members)}
        self.site_keys = _site_name_keys(con)
        self._dom: dict[int, list] = {}

    def dom(self, page_id: int) -> list:
        if page_id not in self._dom:
            title, blob = self.con.execute(
                "SELECT title, rendered_html FROM pages WHERE page_id = ?", [page_id]).fetchone()
            html = zstandard.ZstdDecompressor().decompress(blob).decode("utf-8", "replace")
            self._dom[page_id] = parse_blocks(html, title)
        return self._dom[page_id]

    def block_id(self, page_id: int, ordinal: int) -> int | None:
        row = self.con.execute("SELECT block_id FROM blocks WHERE page_id = ? AND ordinal = ?",
                               [page_id, ordinal]).fetchone()
        return row[0] if row else None


# ---------------------------------------------------------------------------
# oldalhoz kötött entitások
# ---------------------------------------------------------------------------


def _page_entities(ctx: _Context, merger: Merger, run: SiteRun) -> dict[str, int]:
    """Csoportonként az oldalhoz kötött entitás azonosítója. A kitöltőszöveg-oldalhoz egy korábbi
    futásban kötött entitás oldalkötése megszűnik (újrafuttatáskor is ugyanaz, mint frissen)."""
    if ctx.placeholder:
        ctx.con.execute("UPDATE entities SET anchor_page_id = NULL, tier = NULL "
                        "WHERE list_contains(?, anchor_page_id)", [sorted(ctx.placeholder)])
    anchors = _qualified_anchors(ctx)
    cards = _card_headings(ctx)
    anchored: dict[str, int] = {}
    for group, members in ctx.groups.items():
        rep = representative(members, ctx.site_lang)
        role = rep.role
        kind, subtype = ROLE_TYPE[role]
        names = _page_names(ctx, members, rep, anchors.get(group, []))
        names += [Name(text, "anchor", lang) for _, _, text, lang in cards.get(group, [])]
        canonical = _canonical(role, rep, names)
        keys = {alias_key(n.text) for n in names} - {""}
        entity_id = _find_page_entity(ctx.con, members, role, keys, merger, canonical)
        if entity_id is None:
            (entity_id,) = ctx.con.execute(
                "INSERT INTO entities (name, lang, type, subtype, aliases, source, created_at) "
                "VALUES (?, ?, ?, ?, [], 'rule', ?) RETURNING entity_id",
                [canonical, rep.lang or ctx.site_lang, kind, subtype, merger.clock()]
            ).fetchone()
        forms = sorted({n.text for n in names} - {canonical})
        ctx.con.execute(
            "UPDATE entities SET name = ?, type = ?, subtype = coalesce(?, subtype), "
            "tier = ?, anchor_page_id = ?, lang = coalesce(lang, ?), aliases = "
            "list_distinct(list_filter(list_concat(coalesce(aliases, []), ?), x -> x <> ?)) "
            "WHERE entity_id = ?",
            [canonical, kind, subtype, "core" if role == "offer" else None, rep.page_id,
             rep.lang or ctx.site_lang, forms, canonical, entity_id])
        _write_aliases(ctx.con, entity_id, names)
        for info in members:
            _page_mentions(ctx, entity_id, info, names)
        for page_id, ordinal, text in _anchor_blocks(ctx, anchors.get(group, [])):
            run.anchor_mentions += _add_mention(ctx, entity_id, page_id, ordinal, text, "anchor")
        for page_id, ordinal, text, _ in cards.get(group, []):
            run.anchor_mentions += _add_mention(ctx, entity_id, page_id, ordinal, text,
                                                "heading")
        _position_identity(ctx, merger, entity_id, keys)
        anchored[group] = entity_id
        run.page_entities += 1
    return anchored


def _find_page_entity(con: duckdb.DuckDBPyConnection, members: list[PageInfo], role: str,
                      keys: set[str], merger: Merger, canonical: str) -> int | None:
    """A csoport meglévő entitásai (korábbi futás oldalkötése vagy kompatibilis típus és azonos
    kulcsú név vagy alias); egybe olvasztva, a megtartott azonosítója."""
    page_ids = [m.page_id for m in members]
    found: list[tuple] = []
    for entity_id, name, kind, aliases, source, anchor, mentions in con.execute(
            "SELECT e.entity_id, e.name, e.type, e.aliases, e.source, e.anchor_page_id, "
            "(SELECT count(*) FROM page_entities pe WHERE pe.entity_id = e.entity_id) "
            "FROM entities e WHERE list_contains(?, e.type) ORDER BY e.entity_id",
            [list(COMPATIBLE[role])]).fetchall():
        own = anchor is not None and anchor in page_ids
        if own or {alias_key(f) for f in [name, *(aliases or [])]} & keys:
            found.append((not own, SOURCE_STRENGTH.get(source, 9), -mentions, entity_id, name))
    if not found:
        return None
    found.sort()
    keep = found[0][3]
    for *_, entity_id, name in found[1:]:
        merger.merge(keep, entity_id, "page_identity",
                     {"pages": page_ids, "name": name, "canonical": canonical})
    return keep


def _position_identity(ctx: _Context, merger: Merger, entity_id: int, keys: set[str]) -> None:
    """Más entitás, amelynek a neve az oldalhoz kötött entitás egyik neve, és minden blokkos
    említése az oldalhoz kötött entitás azonosító blokkjaiban áll (title, H1, anchor,
    kártyacím), beolvad (`page_identity_position`); a szöveg közben is használt azonos nevű
    fogalom külön marad."""
    evidence = {row[0] for row in ctx.con.execute(
        "SELECT DISTINCT block_id FROM page_entities WHERE entity_id = ? "
        "AND block_id IS NOT NULL", [entity_id]).fetchall()}
    if not evidence:
        return
    for other, name, aliases, blocks in ctx.con.execute(
            "SELECT e.entity_id, e.name, e.aliases, list(DISTINCT pe.block_id) FROM entities e "
            "JOIN page_entities pe USING (entity_id) WHERE e.entity_id <> ? "
            "AND pe.block_id IS NOT NULL AND NOT list_contains(?, e.type) "
            "GROUP BY e.entity_id, e.name, e.aliases",
            [entity_id, list(IDENTITY_EXCLUDED_TYPES)]).fetchall():
        if set(blocks) <= evidence and alias_key(name) in keys:
            merger.merge(entity_id, other, "page_identity_position",
                         {"name": name, "blocks": sorted(blocks)})


def _page_names(ctx: _Context, members: list[PageInfo], rep: PageInfo,
                anchors: list[tuple[int, int, str, str, str]]) -> list[Name]:
    names: list[Name] = []
    nodes = _self_nodes(ctx.con, members)
    for info in members:
        primary = info.lang == rep.lang
        h1_source, title_source = ("h1", "title") if primary else ("hreflang", "hreflang")
        if info.h1:
            names.append(Name(info.h1.strip(), h1_source, info.lang))
        for form in _title_forms(info.title, ctx.site_keys):
            if not cut_off(form, info.h1):
                names.append(Name(form, title_source, info.lang))
        for node in nodes.get(info.page_id, []):
            for value in (node.get("name"), node.get("headline"), node.get("alternateName")):
                for text in _texts(value):
                    names.append(Name(_unescape(text), "schema", info.lang))
    counts: Counter[tuple[str, str, str | None]] = Counter()
    for _, _, text, position, lang in anchors:
        counts[(text, "nav" if position in NAV_POSITIONS else "anchor", lang)] += 1
    names += [Name(text, source, lang, count) for (text, source, lang), count in counts.items()]
    return names + [Name(part, n.source, n.lang, n.count) for n in names
                    for part in expansions(n.text)]


def expansions(text: str) -> list[str]:
    """A zárójeles kifejtés (M2/6, 7. pont) részei külön névként, ha az egyik rész rövidítés
    (2–6 nagybetű vagy szám): „Keresőoptimalizálás (SEO)” → Keresőoptimalizálás, SEO;
    „SEO (Keresőoptimalizálás)” → ugyanígy. Más zárójeles alak nem bomlik."""
    match = PARENTHETICAL.fullmatch(text.strip())
    if not match:
        return []
    base, inner = match.group(1).strip(), match.group(2).strip()
    if ACRONYM.fullmatch(inner) or ACRONYM.fullmatch(base):
        return [base, inner]
    return []


def _canonical(role: str, rep: PageInfo, names: list[Name]) -> str:
    if role in ("offer", "product"):
        chrome = [n for n in names if n.source == "nav" and n.lang == rep.lang]
        if chrome:
            return max(chrome, key=lambda n: (n.count, len(n.text.split()), len(n.text))).text
        for source in ("schema", "title"):
            found = [n.text for n in names if n.source == source and n.lang == rep.lang]
            if found:
                return min(found, key=len) if source == "title" else found[0]
    return (rep.h1 or rep.title or rep.url).strip()


def _title_forms(title: str | None, site_keys: set[str]) -> list[str]:
    """A title a site-nevet tartalmazó végződés nélkül, és az elválasztók (`TITLE_SEPARATORS`)
    közötti szeletei, a site-nevűek nélkül. Site-név a csonkolt alak is (a title hosszkorlátja
    levágja: „… - DUEX” a „DUEX Hungary Webshop” helyett; `site_name_form`)."""
    if not title:
        return []
    clean = title.strip()
    for ending in title_endings(clean)[1:]:
        if site_name_form(ending, site_keys, minimum=1):
            clean = clean[: clean.rfind(ending)].rstrip(" |-–—·:»•").strip()
            break
    forms = [clean] if clean and not site_name_form(clean, site_keys) else []
    pattern = "|".join(re.escape(sep) for sep in TITLE_SEPARATORS)
    for piece in re.split(pattern, clean):
        piece = piece.strip()
        if piece and piece not in forms and not site_name_form(piece, site_keys):
            forms.append(piece)
    return forms


def site_name_form(text: str, site_keys: set[str], minimum: int = SITE_PREFIX_MIN) -> bool:
    """A szöveg a site egyik neve, vagy annak legalább `minimum` jeles eleje (csonkolt alak; a
    title végén, a site-név helyén bármilyen rövid: „… - D”)."""
    key = alias_key(text)
    return key in site_keys or (len(key) >= minimum
                                and any(site.startswith(key) for site in site_keys))


def cut_off(form: str, full: str | None) -> bool:
    """A `form` a `full` levágott eleje, szó közben (a title hosszkorlátja: „… Hmv Tartá” a
    „… Hmv Tartályal 14KW …” H1 helyett); az ilyen alak nem név."""
    if not full:
        return False
    key, whole = alias_key(form), alias_key(full)
    return len(key) < len(whole) and whole.startswith(key) and whole[len(key)].isalnum()


def _self_nodes(con: duckdb.DuckDBPyConnection, members: list[PageInfo]) -> dict[int, list[dict]]:
    """Tagoldalanként a szerepkörös JSON-LD csomópontok, amelyek az oldalra mutatnak (`url` vagy
    `@id` a töredék nélkül), bármelyik oldal JSON-LD-jében; cikknél a tagoldal saját cikk-
    csomópontja is."""
    found: dict[int, list[dict]] = defaultdict(list)
    urls = {m.page_id: m.url for m in members}
    types = set(SCHEMA_SELF_TYPES.get(members[0].role, ()))
    if not types:
        return found
    for page_id, raw in con.execute(
            "SELECT page_id, json FROM schema_blocks WHERE type IS DISTINCT FROM 'invalid' "
            "ORDER BY page_id, ordinal").fetchall():
        for node in _typed_nodes(_loads(raw)):
            node_types = {_short(t) for t in _as_list(node.get("@type"))}
            if not node_types & types:
                continue
            for member, url in urls.items():
                own_article = page_id == member and node_types & set(SCHEMA_SELF_TYPES["article"])
                if own_article or same_page(node.get("url"), url) \
                        or same_page(node.get("@id"), url):
                    found[member].append(node)
    return found


def _qualified_anchors(ctx: _Context) -> dict[str, list[tuple[int, int, str, str, str]]]:
    """Csoportonként a más csoportból rá mutató, alias-képes anchorok: nem triviális, a szövege
    az egész site-on csak ide mutat, és legfeljebb `ANCHOR_MAX_WORDS` szó, vagy egyezik egy
    tagoldal H1-ével vagy title-jével (cikkcímek). (forrásoldal, céloldal, szöveg, pozíció, a
    forrásoldal nyelve)."""
    roles = ctx.roles
    rows = ctx.con.execute(
        "SELECT from_page_id, to_page_id, anchor, position FROM links "
        "WHERE anchor IS NOT NULL AND to_page_id IS NOT NULL ORDER BY from_page_id, ordinal"
    ).fetchall()
    targets: dict[str, set[str]] = defaultdict(set)
    for from_id, to_id, anchor, _ in rows:
        if from_id in roles and to_id in roles and roles[from_id].group != roles[to_id].group:
            targets[alias_key(anchor)].add(roles[to_id].group)
    titles: dict[str, set[str]] = defaultdict(set)
    for group, members in ctx.groups.items():
        for info in members:
            titles[group] |= {alias_key(t) for t in [info.h1 or "",
                                                      *_title_forms(info.title, ctx.site_keys)]}
    found: dict[str, list] = defaultdict(list)
    for from_id, to_id, anchor, position in rows:
        if from_id not in roles or to_id not in roles:
            continue
        source, target = roles[from_id], roles[to_id]
        text = " ".join(anchor.split())
        key = alias_key(text)
        long = len(text.split()) > ANCHOR_MAX_WORDS and key not in titles[target.group]
        if source.group == target.group or target.group not in ctx.groups \
                or trivial_anchor(text) or long or len(targets[key]) != 1:
            continue
        found[target.group].append((from_id, to_id, text, position, source.lang))
    return found


def _card_headings(ctx: _Context) -> dict[str, list[tuple[int, int, str, str | None]]]:
    """Kártyacímek: ha egy blokk linkje egy entitásoldal-csoportra mutat (a link célja a
    `links` sorrendjéből: az oldal azonos szövegű linkjei a DOM-sorrendben), az előtte álló
    legfeljebb `CARD_LOOKBACK` blokk közül a legközelebbi, legalább 3. szintű, rövid heading
    (legfeljebb `CARD_TITLE_WORDS` szó) a csoport aliasa, ha közben nincs más link.
    Csoportonként: (oldal, heading-sorszám, szöveg, az oldal nyelve)."""
    roles = ctx.roles
    queues: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for from_id, anchor, to_id in ctx.con.execute(
            "SELECT from_page_id, anchor, to_page_id FROM links WHERE anchor IS NOT NULL "
            "ORDER BY from_page_id, ordinal").fetchall():
        if from_id in roles:
            queues[from_id][alias_key(anchor)].append(to_id)
    found: dict[str, list] = defaultdict(list)
    for page_id in sorted(queues):
        own = roles[page_id].group
        blocks = ctx.dom(page_id)
        targets: list[set[str]] = []
        for block in blocks:
            groups = set()
            for anchor in block.anchors:
                queue = queues[page_id].get(alias_key(anchor))
                to_id = queue.pop(0) if queue else None
                if to_id in roles and roles[to_id].group in ctx.groups \
                        and roles[to_id].group != own:
                    groups.add(roles[to_id].group)
            targets.append(groups)
        for index, groups in enumerate(targets):
            if len(groups) != 1:
                continue
            for back in range(1, CARD_LOOKBACK + 1):
                if index - back < 0 or blocks[index - back].anchors:
                    break
                prev = blocks[index - back]
                if prev.kind == "heading":
                    if (prev.level or 0) >= 3 and 0 < len(prev.text.split()) <= CARD_TITLE_WORDS:
                        found[next(iter(groups))].append(
                            (page_id, prev.ordinal, prev.text.strip(), roles[page_id].lang))
                    break
    return found


def _anchor_blocks(ctx: _Context, anchors: list) -> Iterable[tuple[int, int, str]]:
    """Az anchorok blokkjai a forrásoldalon: (oldal, blokk-sorszám, anchor-szöveg)."""
    wanted: dict[int, set[str]] = defaultdict(set)
    for from_id, _, text, _, _ in anchors:
        wanted[from_id].add(text)
    for page_id, texts in wanted.items():
        keys = {alias_key(t): t for t in texts}
        for block in ctx.dom(page_id):
            for anchor in block.anchors:
                if alias_key(anchor) in keys:
                    yield page_id, block.ordinal, keys[alias_key(anchor)]


def _page_mentions(ctx: _Context, entity_id: int, info: PageInfo, names: list[Name]) -> None:
    for ordinal, kind, level, text in ctx.con.execute(
            "SELECT b.ordinal, b.kind, b.level, b.text FROM blocks b WHERE b.page_id = ? "
            "AND b.region = 'content' AND (b.kind = 'title' OR (b.kind = 'heading' "
            "AND b.level = 1)) ORDER BY b.ordinal", [info.page_id]).fetchall():
        position = "title" if kind == "title" else "h1"
        candidates = sorted({n.text for n in names}, key=len, reverse=True)
        target = next((c for c in candidates if find_name(text, alias_key(c))), None)
        if target is not None:
            _add_mention(ctx, entity_id, info.page_id, ordinal, target, position)


def _add_mention(ctx: _Context, entity_id: int, page_id: int, ordinal: int, name: str,
                 position: str) -> int:
    """Egy említés a blokkban a név helyén (source = rule, a site-futás); visszaad: 1, ha új
    forrás került be."""
    block = ctx.con.execute("SELECT block_id, text FROM blocks WHERE page_id = ? AND ordinal = ?",
                            [page_id, ordinal]).fetchone()
    if block is None:
        return 0
    span = find_name(block[1], alias_key(name))
    if span is None:
        return 0
    start, end = span
    found = ctx.con.execute(
        "SELECT mention_id FROM page_entities WHERE page_id = ? AND block_id = ? "
        "AND char_start = ? AND char_end = ? AND entity_id = ?",
        [page_id, block[0], start, end, entity_id]).fetchone()
    if found is None:
        (mention_id,) = ctx.con.execute(
            "INSERT INTO page_entities (page_id, entity_id, block_id, char_start, char_end, "
            "surface_form, position) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING mention_id",
            [page_id, entity_id, block[0], start, end, block[1][start:end], position]
        ).fetchone()
    else:
        mention_id = found[0]
    inserted = ctx.con.execute(
        "INSERT INTO mention_sources (mention_id, source, run_id) VALUES (?, 'rule', ?) "
        "ON CONFLICT DO NOTHING RETURNING mention_id", [mention_id, ctx.run_id]).fetchall()
    return len(inserted)


def _write_aliases(con: duckdb.DuckDBPyConnection, entity_id: int, names: list[Name]) -> None:
    con.execute("DELETE FROM entity_aliases WHERE entity_id = ? AND list_contains(?, source)",
                [entity_id, list(PAGE_ALIAS_SOURCES)])
    rows = {(entity_id, n.text, n.lang, n.source) for n in names if n.text}
    con.executemany("INSERT INTO entity_aliases (entity_id, alias, lang, source) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING", sorted(rows, key=str))


# ---------------------------------------------------------------------------
# csomagok és lépések
# ---------------------------------------------------------------------------


def pricing_rows(blocks: list[tuple[int, str, str, str | None]]) -> list[tuple[int, str]]:
    """(blokk-sorszám, név) az árazási sorokra, dokumentum-sorrendben: táblázatsornál az első
    cella a „ · ” előtt; máshol az ár előtti legfeljebb `PRICE_LOOKBACK` blokk közül a
    legközelebbi rövid, számjegy nélküli szöveg. Az óradíj-sor kimarad."""
    found: list[tuple[int, str]] = []
    for index, (ordinal, kind, text, cells) in enumerate(blocks):
        if not PRICE.search(text) or RATE.search(text):
            continue
        if kind == "table_row":
            first = json.loads(cells)[0]["value"] if cells else text
            name = re.split(r" · | \| ", first, maxsplit=1)[0].strip()
            if _name_like(name):
                found.append((ordinal, name))
            continue
        for back in range(1, PRICE_LOOKBACK + 1):
            if index - back < 0:
                break
            o, k, t, _ = blocks[index - back]
            if PRICE.search(t):
                break
            if k in ("heading", "paragraph", "card", "other", "list_item") and _name_like(t):
                found.append((o, t.strip()))
                break
    return found


def _packages(ctx: _Context, merger: Merger, run: SiteRun, anchored: dict[str, int]) -> None:
    for group, members in ctx.groups.items():
        if members[0].role != "offer":
            continue
        core = anchored[group]
        by_page: dict[int, list[int]] = {}
        nodes = _self_nodes(ctx.con, members)
        for info in members:
            blocks = ctx.con.execute(
                "SELECT ordinal, kind, text, cells FROM blocks WHERE page_id = ? "
                "AND region = 'content' ORDER BY ordinal", [info.page_id]).fetchall()
            ids = []
            primary = info.page_id == representative(members, ctx.site_lang).page_id
            for ordinal, name in pricing_rows(blocks):
                entity_id = _package_entity(ctx, core, name, info, "pricing", primary)
                if entity_id is not None:
                    _add_mention(ctx, entity_id, info.page_id, ordinal, name, "body")
                    ids.append(entity_id)
            by_page[info.page_id] = ids
            for node in nodes.get(info.page_id, []):
                for name in _catalog_names(node):
                    _package_entity(ctx, core, name, info, "schema_catalog")
        _pair_rows(ctx, merger, members, by_page)
    (run.packages,) = ctx.con.execute("SELECT count(*) FROM entities WHERE tier = 'package'"
                                      ).fetchone()


def _package_entity(ctx: _Context, core: int, name: str, info: PageInfo,
                    source: str, primary: bool = False) -> int | None:
    """A csomag entitása (azonos kulcsú nem fő ajánlat, vagy új); az elsődleges nyelvű oldal
    árazási sora adja a nevét."""
    key = alias_key(name)
    (core_name, core_aliases) = ctx.con.execute(
        "SELECT name, aliases FROM entities WHERE entity_id = ?", [core]).fetchone()
    if key in {alias_key(f) for f in [core_name, *(core_aliases or [])]}:
        return None
    entity_id = next((entity_id for entity_id, name_, aliases in ctx.con.execute(
        "SELECT entity_id, name, aliases FROM entities WHERE type = 'service' "
        "AND coalesce(tier, '') <> 'core' ORDER BY entity_id").fetchall()
        if key in {alias_key(f) for f in [name_, *(aliases or [])]}), None)
    if entity_id is None:
        (entity_id,) = ctx.con.execute(
            "INSERT INTO entities (name, lang, type, aliases, source, created_at) "
            "VALUES (?, ?, 'service', [], 'rule', ?) RETURNING entity_id",
            [name, info.lang or ctx.site_lang, _now()]).fetchone()
    ctx.con.execute("UPDATE entities SET tier = 'package', name = CASE WHEN ? THEN ? ELSE name "
                    "END, aliases = list_distinct(list_filter(list_append(coalesce(aliases, []), "
                    "name), x -> x <> CASE WHEN ? THEN ? ELSE name END)) WHERE entity_id = ?",
                    [primary, name, primary, name, entity_id])
    ctx.con.execute(
        "INSERT INTO entity_relations (from_id, to_id, type, source, evidence) "
        "VALUES (?, ?, 'part_of', ?, ?) ON CONFLICT DO NOTHING",
        [entity_id, core, source, json.dumps({"page_id": info.page_id, "name": name},
                                             ensure_ascii=False)])
    return entity_id


def _pair_rows(ctx: _Context, merger: Merger, members: list[PageInfo],
               by_page: dict[int, list[int]]) -> None:
    """A hreflang-pár oldalak azonos sorszámú árazási sora egy csomag, ha a sorok száma
    egyezik; a megtartott az elsődleges nyelvű oldalé."""
    rep = representative(members, ctx.site_lang)
    for info in members:
        if info.lang == rep.lang or info.page_id not in by_page:
            continue
        mine, theirs = by_page[rep.page_id], by_page[info.page_id]
        if not mine or len(mine) != len(theirs):
            continue
        for index, (keep, other) in enumerate(zip(mine, theirs, strict=True)):
            if keep != other and _exists(ctx.con, keep) and _exists(ctx.con, other):
                merger.merge(keep, other, "hreflang_pricing_row",
                             {"pages": [rep.page_id, info.page_id], "row": index + 1})


def _steps(con: duckdb.DuckDBPyConnection, run: SiteRun) -> None:
    rows = con.execute(
        "UPDATE entities SET type = 'concept', subtype = 'method', tier = 'step', "
        "type_changed_from = 'service' WHERE type = 'service' AND source = 'llm' "
        "AND tier IS NULL RETURNING entity_id").fetchall()
    run.steps = len(rows)



# ---------------------------------------------------------------------------
# összevonás: írásmód és a hreflang-pár azonos helye (7. pont)
# ---------------------------------------------------------------------------


def normal_key(text: str) -> str:
    """Az írásmód-normalizált kulcs: kis-nagybetű és ékezet nélkül (`alias_key`), az „és” és az
    „and” „&”-ként, az elválasztók (szóköz, aláhúzás, kötőjel, perjel, gondolatjel) és a
    zárójelek nélkül. A „/” vagy „@” tartalmú név (csomag- és útvonalszerű) csak az
    `alias_key`-t kapja: ott az elválasztó a név része."""
    key = alias_key(text)
    if "/" in key or "@" in key:
        return key
    return NORMAL_DROP.sub("", CONJUNCTION.sub("&", key))


def without_legal_form(name: str) -> str:
    """A szervezetnév a végén álló jogi forma (`LEGAL_FORMS`: Kft., Zrt., Bt., Ltd, LLC, GmbH,
    Inc.) nélkül; ha más nem marad, a név."""
    tokens = name.split()
    while len(tokens) > 1 and alias_key(tokens[-1]).strip(".,") in LEGAL_FORMS:
        tokens.pop()
    return " ".join(tokens).rstrip(",")


def long_form(text: str) -> str | None:
    """A zárójeles rövidítés-kifejtés hosszú része („GEO (Generative Engine Optimization)” →
    Generative Engine Optimization), vagy None. A rövidítés önmagában nem von össze."""
    parts = expansions(text)
    if not parts:
        return None
    base, inner = parts
    return inner if ACRONYM.fullmatch(base) else base


def _rank(row: tuple) -> tuple:
    """Megtartási sorrend: oldalhoz kötött, core > package > nincs > step, erősebb forrás, több
    említés. `row`: (entity_id, anchor, tier, source, mentions)."""
    entity_id, anchor, tier, source, mentions = row[:5]
    return (anchor is None, TIER_ORDER.get(tier, 2), SOURCE_STRENGTH.get(source, 9), -mentions,
            entity_id)


def _entity_rows(con: duckdb.DuckDBPyConnection) -> dict[int, tuple]:
    """entity_id → (entity_id, anchor, tier, source, említésszám, típus, altípus)."""
    return {row[0]: row for row in con.execute(
        "SELECT e.entity_id, e.anchor_page_id, e.tier, e.source, (SELECT count(*) FROM "
        "page_entities pe WHERE pe.entity_id = e.entity_id), e.type, e.subtype "
        "FROM entities e").fetchall()}


def _mergeable(a: tuple, b: tuple) -> bool:
    """Nem olvad össze: két különböző oldalhoz kötött entitás, két eltérő, nem üres, nem step
    szint, és két nem kompatibilis altípus (terjesztési szint: package, library, framework …
    kontra kódszint: component, api_symbol, feature; `SUBTYPE_CLASS`)."""
    if a[1] is not None and b[1] is not None and a[1] != b[1]:
        return False
    tiers = {TIER_GROUP.get(a[2], a[2]), TIER_GROUP.get(b[2], b[2])} - {None, "step"}
    if len(tiers) > 1:
        return False
    classes = {SUBTYPE_CLASS.get(a[6]), SUBTYPE_CLASS.get(b[6])} - {None}
    return len(classes) <= 1


def _normalized_merges(con: duckdb.DuckDBPyConnection, merger: Merger) -> None:
    """Azonos típusú entitások azonos normalizált kulccsal (név, aliasok és a zárójeles
    rövidítés-kifejtés) egy entitás (`normalized_name`); személy kimarad (a szabálykör
    kezeli)."""
    rows = _entity_rows(con)
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for entity_id, name, kind, aliases in con.execute(
            "SELECT entity_id, name, type, aliases FROM entities WHERE type <> 'person' "
            "AND (anchor_page_id IS NOT NULL OR entity_id IN (SELECT entity_id FROM "
            "page_entities)) ORDER BY entity_id").fetchall():
        forms = {name, *(aliases or [])}
        forms |= {long for form in list(forms) if (long := long_form(form))}
        if kind == "org":
            forms |= {without_legal_form(form) for form in list(forms)}
        for key in {normal_key(f) for f in forms} - {""}:
            groups[(kind, key)].append(entity_id)
    done: set[int] = set()
    for (kind, key), ids in sorted(groups.items()):
        ids = [i for i in dict.fromkeys(ids) if i not in done and i in rows]
        if len(ids) < 2:
            continue
        ordered = sorted((rows[i] for i in ids), key=_rank)
        keep = ordered[0]
        for other in ordered[1:]:
            if _mergeable(keep, other) and _exists(con, other[0]):
                merger.merge(keep[0], other[0], "normalized_name", {"key": key, "type": kind})
                done.add(other[0])


def _sections(con: duckdb.DuckDBPyConnection, page_id: int) -> list[tuple[int, list[int]]]:
    """Az oldal H2-szakaszai: (a H2 blokkja, a H3 blokkjai), dokumentum-sorrendben."""
    sections: list[tuple[int, list[int]]] = []
    for block_id, level in con.execute(
            "SELECT block_id, level FROM blocks WHERE page_id = ? AND region = 'content' "
            "AND kind = 'heading' AND level IN (2, 3) ORDER BY ordinal", [page_id]).fetchall():
        if level == 2:
            sections.append((block_id, []))
        elif sections:
            sections[-1][1].append(block_id)
    return sections


def aligned_headings(left: list[tuple[int, list[int]]],
                     right: list[tuple[int, list[int]]]) -> list[tuple[int, int]]:
    """A két oldal azonos helyű headingjei: a H2-szakaszok elölről és hátulról párban, amíg a
    H3-ak száma egyezik; a párba állított szakaszok H2-je és H3-ai sorszám szerint."""
    pairs: list[tuple[int, int]] = []
    front = 0
    while front < min(len(left), len(right)) and len(left[front][1]) == len(right[front][1]):
        front += 1
    back = 0
    while back < min(len(left), len(right)) - front \
            and len(left[-1 - back][1]) == len(right[-1 - back][1]):
        back += 1
    indexes = [(i, i) for i in range(front)] + [(len(left) - 1 - k, len(right) - 1 - k)
                                                 for k in range(back)]
    for i, j in indexes:
        pairs.append((left[i][0], right[j][0]))
        pairs += list(zip(left[i][1], right[j][1], strict=True))
    return pairs


def _heading_entity(con: duckdb.DuckDBPyConnection, block_id: int) -> int | None:
    """A headinget egészében lefedő említés entitása, ha pontosan egy ilyen van."""
    (text,) = con.execute("SELECT text FROM blocks WHERE block_id = ?", [block_id]).fetchone()
    found = {entity_id for entity_id, surface in con.execute(
        "SELECT entity_id, surface_form FROM page_entities WHERE block_id = ?",
        [block_id]).fetchall() if alias_key(surface) == alias_key(text)}
    return found.pop() if len(found) == 1 else None


def _hreflang_place(ctx: _Context, merger: Merger) -> None:
    """A hreflang-pár oldalak azonos helyű headingjeinek entitása egy entitás
    (`hreflang_place`), ha a típusuk egyezik, vagy az egyik service és a másik csak
    headingben álló concept."""
    con = ctx.con
    groups: dict[str, list[PageInfo]] = defaultdict(list)
    for info in ctx.roles.values():
        groups[info.group].append(info)
    for members in groups.values():
        langs = {m.lang for m in members}
        if len(langs) < 2:
            continue
        rep = representative(members, ctx.site_lang)
        left = _sections(con, rep.page_id)
        for info in members:
            if info.lang == rep.lang:
                continue
            for mine, theirs in aligned_headings(left, _sections(con, info.page_id)):
                a, b = _heading_entity(con, mine), _heading_entity(con, theirs)
                if a is None or b is None or a == b or not (_exists(con, a) and _exists(con, b)):
                    continue
                keep, other = _place_pair(con, a, b, prefer=a)
                if keep is not None:
                    merger.merge(keep, other, "hreflang_place",
                                 {"pages": [rep.page_id, info.page_id],
                                  "blocks": [mine, theirs]})


def _place_pair(con: duckdb.DuckDBPyConnection, a: int, b: int,
                prefer: int | None = None) -> tuple[int | None, int]:
    """(megtartott, beolvadó): azonos típusnál az oldalhoz kötött, a magasabb szintű, azonos
    szinten a `prefer` (az elsődleges nyelvű oldalé); service és csak headingben álló
    concept esetén a service."""
    rows = _entity_rows(con)
    types = dict(con.execute("SELECT entity_id, type FROM entities WHERE list_contains(?, "
                             "entity_id)", [[a, b]]).fetchall())
    if not _mergeable(rows[a], rows[b]):
        return None, b
    if types[a] == types[b]:
        keep, other = sorted((rows[a], rows[b]), key=lambda r: (
            r[1] is None, TIER_ORDER.get(r[2], 2), r[0] != prefer, *_rank(r)))
        return keep[0], other[0]
    for service, concept in ((a, b), (b, a)):
        if types[service] == "service" and types[concept] == "concept" \
                and _only_headings(con, concept):
            return service, concept
    return None, b


def _only_headings(con: duckdb.DuckDBPyConnection, entity_id: int) -> bool:
    (others,) = con.execute(
        "SELECT count(*) FROM page_entities pe JOIN blocks b USING (block_id) "
        "WHERE pe.entity_id = ? AND b.kind <> 'heading'", [entity_id]).fetchone()
    return others == 0


# ---------------------------------------------------------------------------
# fogalom és ajánlat (9. pont)
# ---------------------------------------------------------------------------


def llm_types(con: duckdb.DuckDBPyConnection) -> dict[tuple[int, int, int, int], tuple[str, str]]:
    """A legutóbbi LLM-futás tárolt rekordjaiból említésenként (oldal, blokk, kezdet, vég) az
    LLM típusa és kanonikus neve."""
    (run_id,) = con.execute("SELECT max(run_id) FROM entity_runs WHERE method = 'llm'"
                            ).fetchone()
    found: dict[tuple[int, int, int, int], tuple[str, str]] = {}
    if run_id is None:
        return found
    for page_id, refined, extraction in con.execute(
            "SELECT page_id, refined, extraction FROM entity_run_pages WHERE run_id = ? "
            "AND status = 'done'", [run_id]).fetchall():
        record = json.loads(refined or extraction or "{}")
        blocks = {f"b{ordinal}": (block_id, text) for block_id, ordinal, text in con.execute(
            "SELECT block_id, ordinal, text FROM blocks WHERE page_id = ?", [page_id]).fetchall()}
        for raw in record.get("entities") or []:
            block = blocks.get(raw.get("block_id"))
            spans = surface_offsets(raw.get("surface_form", ""), block[1]) if block else []
            if spans:
                found.setdefault((page_id, block[0], *spans[0]),
                                 (raw["type"], raw["canonical_name"]))
    return found


def _type_split(con: duckdb.DuckDBPyConnection, merger: Merger) -> dict[int, set[int]]:
    """A service típusú entitás LLM-említései, amelyeket az LLM fogalomként talált, egy
    fogalom-entitáshoz kerülnek (az LLM kanonikus nevével; meglévőhöz, ha a normalizált kulcs
    egyezik). Visszaad: service → a leválasztott fogalmak."""
    raw = llm_types(con)
    concepts = _concept_index(con)
    names = _service_names(con)
    moved: dict[int, set[int]] = defaultdict(set)
    counts: Counter[tuple[int, int]] = Counter()
    for mention_id, entity_id, page_id, block_id, start, end, kind_, text in con.execute(
            "SELECT pe.mention_id, pe.entity_id, pe.page_id, pe.block_id, pe.char_start, "
            "pe.char_end, b.kind, b.text FROM page_entities pe JOIN entities e USING (entity_id) "
            "JOIN blocks b USING (block_id) WHERE e.type = 'service' AND pe.mention_id IN "
            "(SELECT mention_id FROM mention_sources WHERE source = 'llm') "
            "ORDER BY pe.mention_id").fetchall():
        kind, name = raw.get((page_id, block_id, start, end), (None, None))
        if kind != "concept":
            continue
        whole = kind_ in ("heading", "title", "card") and alias_key(text[start:end]) \
            == alias_key(text)
        own = normal_key(name) in names.get(entity_id, set()) and len(name.split()) > 1
        if whole or own:
            continue
        key = normal_key(name)
        concept = concepts.get(key)
        if concept is None:
            (concept,) = con.execute(
                "INSERT INTO entities (name, type, aliases, source, created_at) "
                "VALUES (?, 'concept', [], 'llm', ?) RETURNING entity_id",
                [name.strip(), merger.clock()]).fetchone()
            concepts[key] = concept
        _move_mention(con, mention_id, concept)
        moved[entity_id].add(concept)
        counts[(entity_id, concept)] += 1
    for (entity_id, concept), count in counts.items():
        (source_name,) = con.execute("SELECT name FROM entities WHERE entity_id = ?",
                                     [entity_id]).fetchone()
        (concept_name,) = con.execute("SELECT name FROM entities WHERE entity_id = ?",
                                      [concept]).fetchone()
        con.execute(
            "INSERT INTO merge_log (run_id, kept_id, removed_id, kept_name, removed_name, rule, "
            "evidence, merged_at) VALUES (?, ?, NULL, ?, ?, 'type_split', ?, ?)",
            [merger.run_id, concept, concept_name, source_name,
             json.dumps({"from_id": entity_id, "mentions": count}), merger.clock()])
        merger.counts["type_split"] += 1
    return moved


def _service_names(con: duckdb.DuckDBPyConnection) -> dict[int, set[str]]:
    """Service → a neve, az aliasai és az `entity_aliases` sorai normalizált kulccsal."""
    found: dict[int, set[str]] = defaultdict(set)
    for entity_id, name, aliases in con.execute(
            "SELECT entity_id, name, aliases FROM entities WHERE type = 'service'").fetchall():
        found[entity_id] |= {normal_key(f) for f in [name, *(aliases or [])]}
    for entity_id, alias in con.execute(
            "SELECT a.entity_id, a.alias FROM entity_aliases a JOIN entities e "
            "USING (entity_id) WHERE e.type = 'service'").fetchall():
        found[entity_id].add(normal_key(alias))
    return found


def _concept_index(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    found: dict[str, int] = {}
    for entity_id, name, aliases in con.execute(
            "SELECT entity_id, name, aliases FROM entities WHERE type = 'concept' "
            "ORDER BY entity_id").fetchall():
        for form in [name, *(aliases or [])]:
            found.setdefault(normal_key(form), entity_id)
    found.pop("", None)
    return found


def _move_mention(con: duckdb.DuckDBPyConnection, mention_id: int, entity_id: int) -> None:
    """Az említés átkerül az entitáshoz; ha ott már van azonos helyű említés, a forrásai
    oda kerülnek."""
    row = con.execute("SELECT page_id, block_id, char_start, char_end FROM page_entities "
                      "WHERE mention_id = ?", [mention_id]).fetchone()
    same = con.execute(
        "SELECT mention_id FROM page_entities WHERE page_id = ? AND block_id IS NOT DISTINCT "
        "FROM ? AND char_start IS NOT DISTINCT FROM ? AND char_end IS NOT DISTINCT FROM ? "
        "AND entity_id = ?", [*row, entity_id]).fetchone()
    if same is None:
        con.execute("UPDATE page_entities SET entity_id = ? WHERE mention_id = ?",
                    [entity_id, mention_id])
        return
    con.execute("INSERT INTO mention_sources (mention_id, source, run_id, llm_call_id, count) "
                "SELECT ?, source, run_id, llm_call_id, count FROM mention_sources "
                "WHERE mention_id = ? ON CONFLICT DO NOTHING", [same[0], mention_id])
    con.execute("DELETE FROM mention_sources WHERE mention_id = ?", [mention_id])
    con.execute("DELETE FROM page_entities WHERE mention_id = ?", [mention_id])


def label_parts(text: str) -> list[str]:
    """Az összetett ajánlatcímke részei (M2/6, 9. pont): az „&”, „és”, „and”, „+”, „/”, a
    gondolatjel és a vessző mentén, és a zárójeles rövidítés-kifejtés."""
    parts = [p.strip() for p in LABEL_SPLIT.split(text) if p.strip()]
    return list(dict.fromkeys([*parts, *(e for p in [text, *parts] for e in expansions(p))]))


def _offers(con: duckdb.DuckDBPyConnection, split: dict[int, set[int]]) -> int:
    """`offers` a fő ajánlatból és a csomagból a fogalmakhoz: a leválasztott fogalmak
    (`type_split`), és a fogalom, amelynek normalizált kulcsa az ajánlat nevének, navigációs,
    JSON-LD vagy anchor-aliasának, vagy ezek egy címkerészének kulcsa (`name`)."""
    concepts = _concept_index(con)
    con.execute("DELETE FROM entity_relations WHERE type = 'offers'")
    rows = set()
    for entity_id, name, aliases in con.execute(
            "SELECT entity_id, name, aliases FROM entities WHERE type = 'service' "
            "AND tier IN ('core', 'package', 'work_mode')").fetchall():
        forms = {name} | {alias for (alias,) in con.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = ? AND list_contains(?, source)",
            [entity_id, list(OFFER_LABEL_SOURCES)]).fetchall()}
        for form in forms:
            for part in label_parts(form):
                concept = concepts.get(normal_key(part))
                if concept is not None:
                    rows.add((entity_id, concept, "name", json.dumps({"name": part},
                                                                     ensure_ascii=False)))
        for concept in {resolve(con, c) for c in split.get(entity_id, ())}:
            if concept is not None:
                rows.add((entity_id, concept, "type_split", json.dumps({})))
    unique: dict[tuple[int, int], tuple] = {}
    for row in sorted(rows):
        unique.setdefault(row[:2], row)
    if unique:
        con.executemany("INSERT INTO entity_relations (from_id, to_id, type, source, evidence) "
                        "VALUES (?, ?, 'offers', ?, ?) ON CONFLICT DO NOTHING",
                        [(a, b, s, e) for a, b, s, e in unique.values()])
    return len(unique)


def resolve(con: duckdb.DuckDBPyConnection, entity_id: int) -> int | None:
    """Az entitás a `merge_log` szerinti összevonások után (a megtartotté), vagy None."""
    seen = set()
    while entity_id not in seen:
        seen.add(entity_id)
        if _exists(con, entity_id):
            return entity_id
        row = con.execute("SELECT kept_id FROM merge_log WHERE removed_id = ? "
                          "ORDER BY merge_id DESC LIMIT 1", [entity_id]).fetchone()
        if row is None:
            return None
        entity_id = row[0]
    return None


# ---------------------------------------------------------------------------
# site-szintű felülbírálat (4. pont)
# ---------------------------------------------------------------------------


def apply_overrides(ctx: _Context, merger: Merger, config: SiteConfig) -> int:
    """A `config/sites/<domain>.toml` ajánlat-felülbírálatai: a megnevezett (vagy az URL
    oldalához kötött) entitások egy entitássá olvadnak (`override`), a szintjük a megadott, a
    típusuk service, `part_of` a megadott fő ajánlathoz; a `merge_log` egy `override` sorral
    rögzíti a korábbi típust és szintet. Visszaad: hány felülbírálat talált entitást."""
    con = ctx.con
    applied = 0
    for override in config.offers:
        ids = _override_entities(ctx, override.names, override.url)
        if not ids:
            continue
        rows = _entity_rows(con)
        keep = min((rows[i] for i in ids), key=_rank)[0]
        for other in sorted(ids - {keep}):
            merger.merge(keep, other, "override", {"names": list(override.names),
                                                   "url": override.url})
        name, kind, tier = con.execute("SELECT name, type, tier FROM entities WHERE entity_id = ?",
                                       [keep]).fetchone()
        con.execute(
            "UPDATE entities SET type = 'service', subtype = CASE WHEN type = 'service' THEN "
            "subtype END, tier = ?, type_changed_from = CASE WHEN type <> 'service' THEN type "
            "ELSE type_changed_from END WHERE entity_id = ?", [override.tier, keep])
        core = None
        if override.part_of:
            targets = _override_entities(ctx, (override.part_of,), override.part_of
                                         if "://" in override.part_of else None) - {keep}
            core = min(targets) if targets else None
        if core is not None:
            con.execute("DELETE FROM entity_relations WHERE from_id = ? AND type = 'part_of'",
                        [keep])
            con.execute("INSERT INTO entity_relations (from_id, to_id, type, source, evidence) "
                        "VALUES (?, ?, 'part_of', 'override', ?) ON CONFLICT DO NOTHING",
                        [keep, core, json.dumps({"part_of": override.part_of},
                                                ensure_ascii=False)])
        con.execute(
            "INSERT INTO merge_log (run_id, kept_id, removed_id, kept_name, removed_name, rule, "
            "evidence, merged_at) VALUES (?, ?, NULL, ?, ?, 'override', ?, ?)",
            [merger.run_id, keep, name, name, json.dumps(
                {"tier": override.tier, "previous_type": kind, "previous_tier": tier,
                 "part_of": core}, ensure_ascii=False), merger.clock()])
        merger.counts["override"] += 1
        applied += 1
    return applied


def _override_entities(ctx: _Context, names: tuple[str, ...], url: str | None) -> set[int]:
    """A nevek (normalizált kulcs: név, aliasok, `entity_aliases`) vagy az URL oldalához kötött
    entitások; személy nem."""
    con = ctx.con
    found: set[int] = set()
    if url:
        pages = [info.page_id for info in ctx.roles.values()
                 if page_url(info.url) == page_url(url)]
        groups = {ctx.roles[p].group for p in pages}
        members = [info.page_id for info in ctx.roles.values() if info.group in groups]
        found |= {e for (e,) in con.execute(
            "SELECT entity_id FROM entities WHERE list_contains(?, anchor_page_id)",
            [members]).fetchall()}
    keys = {normal_key(n) for n in names} - {""}
    if keys:
        for entity_id, name, aliases in con.execute(
                "SELECT entity_id, name, aliases FROM entities WHERE type <> 'person' AND "
                "(anchor_page_id IS NOT NULL OR entity_id IN (SELECT entity_id FROM "
                "page_entities))").fetchall():
            if {normal_key(f) for f in [name, *(aliases or [])]} & keys:
                found.add(entity_id)
        for entity_id, alias in con.execute(
                "SELECT a.entity_id, a.alias FROM entity_aliases a JOIN entities e "
                "USING (entity_id) WHERE e.type <> 'person'").fetchall():
            if normal_key(alias) in keys:
                found.add(entity_id)
    return found

# ---------------------------------------------------------------------------
# demó és sablon
# ---------------------------------------------------------------------------


def _demo(con: duckdb.DuckDBPyConnection, run: SiteRun,
          placeholder: set[int] = frozenset()) -> None:
    rows = con.execute(
        "SELECT pe.entity_id, pe.page_id, b.kind, b.text FROM page_entities pe "
        "JOIN entities e USING (entity_id) LEFT JOIN blocks b USING (block_id) "
        "WHERE list_contains(?, e.type)", [list(DEMO_TYPES)]).fetchall()
    code_pages: dict[int, set[int]] = defaultdict(set)
    for entity_id, page_id, kind, _ in rows:
        if kind == "code":
            code_pages[entity_id].add(page_id)
    total: Counter[int] = Counter()
    demo: Counter[int] = Counter()
    for entity_id, page_id, kind, text in rows:
        total[entity_id] += 1
        if (kind == "code" or LOREM.search(text or "") or page_id in placeholder
                or page_id in code_pages[entity_id]):
            demo[entity_id] += 1
    flagged = [e for e in total if demo[e] / total[e] >= DEMO_SHARE]
    _set_flag(con, "demo", flagged)
    run.demo = [name for (name,) in con.execute(
        "SELECT name FROM entities WHERE list_contains(?, entity_id) ORDER BY name",
        [flagged]).fetchall()]
    run.thresholds["demo_share"] = DEMO_SHARE


def _template(ctx: _Context, run: SiteRun) -> None:
    con = ctx.con
    groups = {info.page_id: info.group for info in ctx.roles.values()}
    total = len(set(groups.values()))
    needed = max(TEMPLATE_MIN_GROUPS, TEMPLATE_MIN_SHARE * total)
    run.thresholds.update(template_min_groups=TEMPLATE_MIN_GROUPS,
                          template_min_share=TEMPLATE_MIN_SHARE, template_groups=total,
                          template_needed=needed)
    unit_groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for page_id, kind, text in con.execute(
            "SELECT page_id, kind, text FROM blocks WHERE region = 'content' "
            "AND kind <> 'title'").fetchall():
        if page_id in groups:
            for line in (text.split("\n") if kind == "code" else [text]):
                unit_groups[(kind, alias_key(line))].add(groups[page_id])
    con.execute("UPDATE page_entities SET flags = list_filter(flags, x -> x <> 'template') "
                "WHERE list_contains(flags, 'template')")
    flagged: list[int] = []
    by_entity: dict[int, list[bool]] = defaultdict(list)
    for mention_id, entity_id, kind, text, start in con.execute(
            "SELECT pe.mention_id, pe.entity_id, b.kind, b.text, pe.char_start "
            "FROM page_entities pe JOIN blocks b USING (block_id) "
            "WHERE b.region = 'content' AND b.kind <> 'title'").fetchall():
        unit = (kind, alias_key(_line(text, start) if kind == "code" else text))
        template = len(unit_groups[unit]) >= needed
        by_entity[entity_id].append(template)
        if template:
            flagged.append(mention_id)
    if flagged:
        con.execute("UPDATE page_entities SET flags = list_distinct(list_append(coalesce(flags, "
                    "[]), 'template')) WHERE list_contains(?, mention_id)", [flagged])
    entities = [e for e, marks in by_entity.items() if marks and all(marks)]
    _set_flag(con, "template", entities)
    run.template_mentions = len(flagged)
    run.template_entities = len(entities)


def _set_flag(con: duckdb.DuckDBPyConnection, flag: str, entity_ids: list[int]) -> None:
    con.execute("UPDATE entities SET flags = list_filter(flags, x -> x <> ?) "
                "WHERE list_contains(flags, ?)", [flag, flag])
    if entity_ids:
        con.execute("UPDATE entities SET flags = list_sort(list_distinct(list_append(coalesce("
                    "flags, []), ?))) WHERE list_contains(?, entity_id)", [flag, entity_ids])
    con.execute("UPDATE entities SET flags = NULL WHERE len(flags) = 0")


# ---------------------------------------------------------------------------
# segédek
# ---------------------------------------------------------------------------


def _site_name_keys(con: duckdb.DuckDBPyConnection) -> set[str]:
    """A site nevei: a brand szerepű (site-név) entitások és a brand típusú szabály-entitások
    neve és aliasai; a webshop termékmárkái (`brand_of` kapcsolattal) nem."""
    keys = set()
    for name, aliases in con.execute(
            "SELECT name, aliases FROM entities e WHERE (role = 'brand' "
            "OR (type = 'brand' AND source IN ('rule', 'schema'))) AND NOT EXISTS (SELECT 1 "
            "FROM entity_relations r WHERE r.from_id = e.entity_id AND r.type = 'brand_of')"
            ).fetchall():
        keys |= {alias_key(f) for f in [name, *(aliases or [])]}
    return keys - {""}


def _catalog_names(node: dict) -> list[str]:
    names = []
    for item in _as_list((node.get("hasOfferCatalog") or {}).get("itemListElement")):
        offered = item.get("itemOffered") if isinstance(item, dict) else None
        for candidate in (offered, item):
            if isinstance(candidate, dict) and {_short(t) for t in _as_list(
                    candidate.get("@type"))} & {"Service", "Product"}:
                names += [_unescape(t) for t in _texts(candidate.get("name"))]
                break
    return names


def _name_like(text: str) -> bool:
    words = text.split()
    return 0 < len(words) <= PACKAGE_NAME_WORDS and not re.search(r"\d", text) \
        and len(text) <= 80


def _line(text: str, start: int | None) -> str:
    start = start or 0
    begin = text.rfind("\n", 0, start) + 1
    end = text.find("\n", start)
    return text[begin: end if end != -1 else len(text)]


def _exists(con: duckdb.DuckDBPyConnection, entity_id: int) -> bool:
    return con.execute("SELECT count(*) FROM entities WHERE entity_id = ?",
                       [entity_id]).fetchone()[0] > 0


def _typed_nodes(value: object) -> Iterable[dict]:
    if isinstance(value, dict):
        if "@type" in value:
            yield value
        for child in value.values():
            yield from _typed_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _typed_nodes(child)


def _texts(value: object) -> list[str]:
    return [v.strip() for v in _as_list(value) if isinstance(v, str) and v.strip()]


def _unescape(text: str) -> str:
    return html_lib.unescape(text).strip()


def _loads(raw: str) -> object:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _short(value: object) -> str:
    text = str(value).strip()
    return re.split(r"[/#:]", text)[-1] if text else text


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value] if value is not None else []


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
