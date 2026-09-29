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
  legalább `DEMO_SHARE` részben demó-környezetben állnak: kódblokk, „lorem ipsum” szöveg, vagy
  olyan oldal, ahol ugyanennek az entitásnak kódblokkos említése is van (a példa kimenete).
  Jelölés: `flags` demo.
- Sablonismétlés (6. pont): az említés egysége (kódblokkban a sora, máshol a blokk szövege,
  kulcs szerint, blokkfajtánként) legalább `TEMPLATE_MIN_GROUPS` és az oldalcsoportok
  `TEMPLATE_MIN_SHARE` részén áll → `page_entities.flags` template; az entitás template, ha
  minden blokkos említése az. A title-blokk kimarad.
- Minden összevonás a `merge_log`-ba kerül; futásonként egy `entity_runs` sor (method = site).
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
from aaa2.entities.pages import (
    NAV_POSITIONS,
    ROLE_TYPE,
    PageInfo,
    entity_groups,
    page_roles,
    representative,
    same_page,
    site_language,
)
from aaa2.entities.rules import (
    SOURCE_STRENGTH,
    TITLE_SEPARATORS,
    alias_key,
    find_name,
    stronger_source,
    title_endings,
    trivial_anchor,
)

ANCHOR_MAX_WORDS = 6
CARD_LOOKBACK = 3
CARD_TITLE_WORDS = 5
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
    demo: list[str] = field(default_factory=list)
    template_mentions: int = 0
    template_entities: int = 0
    thresholds: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# összevonás
# ---------------------------------------------------------------------------


class Merger:
    """Entitások összevonása a `merge_log`-gal: az említések, a források, a bizonyítékok, a
    kapcsolatok és az aliasok a megtartott entitáshoz kerülnek."""

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
                "AND k.char_end IS NOT DISTINCT FROM r.char_end AND k.position = r.position "
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
    site_lang = site_language(con)
    con.begin()
    try:
        (run_id,) = con.execute(
            "INSERT INTO entity_runs (started_at, method, llm_calls) VALUES (?, 'site', 0) "
            "RETURNING run_id", [started]).fetchone()
        run = SiteRun(run_id, dict(Counter(info.role for info in roles.values())))
        merger = Merger(con, run_id, clock)
        context = _Context(con, roles, site_lang, run_id)
        anchored = _page_entities(context, merger, run)
        _packages(context, merger, run, anchored)
        _steps(con, run)
        _demo(con, run)
        _template(context, run)
        run.merges = merger.counts
        con.execute(
            "UPDATE entity_runs SET finished_at = ?, pages = ?, entities = ?, row_count = ?, "
            "skipped = ? WHERE run_id = ?",
            [clock(), len(roles), run.page_entities + run.packages, run.anchor_mentions,
             json.dumps({"roles": run.roles, "merges": dict(run.merges),
                         "packages": run.packages, "steps": run.steps, "demo": run.demo,
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
        self.groups = entity_groups(roles)
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
    """Csoportonként az oldalhoz kötött entitás azonosítója."""
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
    közötti szeletei, a site-nevűek nélkül."""
    if not title:
        return []
    clean = title.strip()
    for ending in title_endings(clean)[1:]:
        if alias_key(ending) in site_keys:
            clean = clean[: clean.rfind(ending)].rstrip(" |-–—·:»•").strip()
            break
    forms = [clean] if clean and alias_key(clean) not in site_keys else []
    pattern = "|".join(re.escape(sep) for sep in TITLE_SEPARATORS)
    for piece in re.split(pattern, clean):
        piece = piece.strip()
        if piece and piece not in forms and alias_key(piece) not in site_keys:
            forms.append(piece)
    return forms


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
# demó és sablon
# ---------------------------------------------------------------------------


def _demo(con: duckdb.DuckDBPyConnection, run: SiteRun) -> None:
    rows = con.execute(
        "SELECT pe.entity_id, pe.page_id, b.kind, b.text FROM page_entities pe "
        "JOIN entities e USING (entity_id) JOIN blocks b USING (block_id) "
        "WHERE list_contains(?, e.type)", [list(DEMO_TYPES)]).fetchall()
    code_pages: dict[int, set[int]] = defaultdict(set)
    for entity_id, page_id, kind, _ in rows:
        if kind == "code":
            code_pages[entity_id].add(page_id)
    total: Counter[int] = Counter()
    demo: Counter[int] = Counter()
    for entity_id, page_id, kind, text in rows:
        total[entity_id] += 1
        if kind == "code" or LOREM.search(text or "") or page_id in code_pages[entity_id]:
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
    neve és aliasai."""
    keys = set()
    for name, aliases in con.execute(
            "SELECT name, aliases FROM entities WHERE role = 'brand' "
            "OR (type = 'brand' AND source IN ('rule', 'schema'))").fetchall():
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
