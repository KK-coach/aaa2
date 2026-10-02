"""Ajánlatok és csomagok: az oldalhoz kötött entitások és neveik, a csomagok és a módszertani lépések, a fogalom és az ajánlat szétválasztása (`offers`), a site-szintű felülbírálat."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

import duckdb

from aaa2.db.stable_json import dumps
from aaa2.engine import queries as crawl
from aaa2.entities.extract import surface_offsets
from aaa2.entities.rules import (
    SOURCE_STRENGTH,
    alias_key,
)
from aaa2.resolver.context import SiteRun, _Context
from aaa2.resolver.merge import Merger, _entity_rows, _rank, resolve
from aaa2.resolver.names import (
    Name,
    _as_list,
    _catalog_names,
    _exists,
    _name_like,
    _now,
    _short,
    _texts,
    _title_forms,
    _typed_nodes,
    _unescape,
    cut_off,
    expansions,
    label_parts,
    normal_key,
)
from aaa2.resolver.navigation import (
    _add_mention,
    _anchor_blocks,
    _card_headings,
    _page_mentions,
    _qualified_anchors,
)
from aaa2.resolver.overrides import SiteConfig
from aaa2.resolver.pages import (
    ENTITY_ROLES,
    NAV_POSITIONS,
    ROLE_TYPE,
    PageInfo,
    page_url,
    representative,
    same_page,
)

IDENTITY_EXCLUDED_TYPES = ("person", "org", "brand", "place")


PRICE_LOOKBACK = 4


PAGE_ALIAS_SOURCES = ("h1", "title", "nav", "anchor", "schema", "hreflang")


COMPATIBLE = {"offer": ("service",), "product": ("product",), "article": ("work",),
              "component": ("tech", "concept")}


SCHEMA_SELF_TYPES = {"offer": ("Service",), "product": ("Product",),
                     "article": ("Article", "BlogPosting", "NewsArticle", "TechArticle")}


PRICE = re.compile(r"\d[\d\s.,–-]*\s*(?:Ft|HUF|€|EUR|\$|USD|£)|(?:€|\$|£)\s*\d", re.IGNORECASE)


RATE = re.compile(r"óradíj|hourly rate|\brate\s*:|\bdíj\s*:|munkadíj|billed at|"
                  r"/\s*(?:óra|hour)\b", re.IGNORECASE)


OFFER_LABEL_SOURCES = ("nav", "schema", "anchor")


def _page_entities(ctx: _Context, merger: Merger, run: SiteRun) -> dict[str, int]:
    """Csoportonként az oldalhoz kötött entitás azonosítója. A kitöltőszöveg-oldalhoz egy korábbi
    futásban kötött entitás oldalkötése megszűnik (újrafuttatáskor is ugyanaz, mint frissen); ha
    a csoport szerepe megváltozott, a korábbi szerep típusával (pl. cikk) hozzá kötött entitásé
    is; ha az oldal már nem entitásoldal (pl. gyűjtőoldal lett), a korábban hozzá kötött
    entitás kötése megszűnik, és a neve a JSON-LD-név lesz, ha van (a menücímke alias marad)."""
    if ctx.placeholder:
        ctx.con.execute("UPDATE entities SET anchor_page_id = NULL, tier = NULL "
                        "WHERE list_contains(?, anchor_page_id)", [sorted(ctx.placeholder)])
    loose = sorted(p for p, info in ctx.roles.items() if info.role not in ENTITY_ROLES)
    for kind, subtype in ROLE_TYPE.values():
        for entity_id, name in ctx.con.execute(
                "UPDATE entities SET anchor_page_id = NULL, tier = NULL "
                "WHERE list_contains(?, anchor_page_id) AND type = ? "
                "AND (? IS NULL OR subtype = ?) RETURNING entity_id, name",
                [loose, kind, subtype, subtype]).fetchall():
            named = ctx.con.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id = ? AND source = 'schema' "
                "ORDER BY length(alias), alias LIMIT 1", [entity_id]).fetchone()
            if named and named[0] != name:
                ctx.con.execute(
                    "UPDATE entities SET name = ?, aliases = list_sort(list_distinct(list_filter("
                    "list_concat(coalesce(aliases, []), [?]), x -> x <> ?))) WHERE entity_id = ?",
                    [named[0], name, named[0], entity_id])
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
            "list_sort(list_distinct(list_filter(list_concat(coalesce(aliases, []), ?), x -> x <> ?))) "
            "WHERE entity_id = ?",
            [canonical, kind, subtype, "core" if role == "offer" else None, rep.page_id,
             rep.lang or ctx.site_lang, forms, canonical, entity_id])
        for other, (stale_kind, stale_subtype) in ROLE_TYPE.items():
            if other != role and stale_kind != kind:
                ctx.con.execute(
                    "UPDATE entities SET anchor_page_id = NULL, tier = NULL WHERE entity_id <> ? "
                    "AND list_contains(?, anchor_page_id) AND type = ? "
                    "AND (? IS NULL OR subtype = ?)",
                    [entity_id, [m.page_id for m in members], stale_kind, stale_subtype,
                     stale_subtype])
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
        "AND block_id IS NOT NULL ORDER BY ALL", [entity_id]).fetchall()}
    if not evidence:
        return
    for other, name, aliases, blocks in ctx.con.execute(
            "SELECT e.entity_id, e.name, e.aliases, list_sort(list(DISTINCT pe.block_id)) FROM entities e "
            "JOIN page_entities pe USING (entity_id) WHERE e.entity_id <> ? "
            "AND pe.block_id IS NOT NULL AND NOT list_contains(?, e.type) "
            "GROUP BY e.entity_id, e.name, e.aliases ORDER BY ALL",
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


SENTENCE = re.compile(r"[.!?:;](?:\s|$)|,\s")


def h1_names_offer(h1: str | None, names: list[Name]) -> bool:
    """A H1 megnevezi-e a szolgáltatást: nem mondat (nincs benne mondatvégi írásjel vagy
    vessző), és áll benne egy más forrású megnevezés (JSON-LD-név, title-szelet, menücímke),
    vagy maga áll egy ilyenben; a szlogen („Ideas don’t create growth. Execution does.”) nem."""
    from aaa2.entities.gate import occurs

    text = (h1 or "").strip()
    if not text or SENTENCE.search(text):
        return False
    others = [n.text for n in names if n.source in ("schema", "title", "nav") and n.text]
    return any(occurs(form, text) or occurs(text, form) for form in others)


def _canonical(role: str, rep: PageInfo, names: list[Name]) -> str:
    """A csoport kanonikus neve. Ajánlatnál: a H1, ha megnevezi a szolgáltatást
    (`h1_names_offer`); különben a JSON-LD-név; különben a title legrövidebb szelete; a
    menücímke csak alias (és végső tartalék). Terméknél: a menücímke, a JSON-LD-név, a title.
    Egyébként a H1, a title vagy az URL."""
    local = [n for n in names if n.lang == rep.lang]
    schema = [n.text for n in local if n.source == "schema"]
    titles = [n.text for n in local if n.source == "title"]
    chrome = [n for n in local if n.source == "nav"]
    longest = max(chrome, key=lambda n: (n.count, len(n.text.split()), len(n.text))).text \
        if chrome else None
    if role == "offer":
        if h1_names_offer(rep.h1, local):
            return rep.h1.strip()
        if schema:
            return schema[0]
        if titles:
            return min(titles, key=len)
        if longest:
            return longest
    if role == "product":
        if longest:
            return longest
        if schema:
            return schema[0]
        if titles:
            return min(titles, key=len)
    return (rep.h1 or rep.title or rep.url).strip()


def _self_nodes(con: duckdb.DuckDBPyConnection, members: list[PageInfo]) -> dict[int, list[dict]]:
    """Tagoldalanként a szerepkörös JSON-LD csomópontok, amelyek az oldalra mutatnak (`url` vagy
    `@id` a töredék nélkül), bármelyik oldal JSON-LD-jében; cikknél a tagoldal saját cikk-
    csomópontja is."""
    found: dict[int, list[dict]] = defaultdict(list)
    urls = {m.page_id: m.url for m in members}
    types = set(SCHEMA_SELF_TYPES.get(members[0].role, ()))
    if not types:
        return found
    for item in crawl.json_ld(con):
        page_id = item.page_id
        for node in _typed_nodes(item.data):
            node_types = {_short(t) for t in _as_list(node.get("@type"))}
            if not node_types & types:
                continue
            for member, url in urls.items():
                own_article = page_id == member and node_types & set(SCHEMA_SELF_TYPES["article"])
                if own_article or same_page(node.get("url"), url) \
                        or same_page(node.get("@id"), url):
                    found[member].append(node)
    return found


def _write_aliases(con: duckdb.DuckDBPyConnection, entity_id: int, names: list[Name]) -> None:
    con.execute("DELETE FROM entity_aliases WHERE entity_id = ? AND list_contains(?, source)",
                [entity_id, list(PAGE_ALIAS_SOURCES)])
    rows = {(entity_id, n.text, n.lang, n.source) for n in names if n.text}
    con.executemany("INSERT INTO entity_aliases (entity_id, alias, lang, source) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING", sorted(rows, key=str))


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
    (run.packages,) = ctx.con.execute("SELECT count(*) FROM entities WHERE tier = 'package' ORDER BY ALL"
                                      ).fetchone()


def _package_entity(ctx: _Context, core: int, name: str, info: PageInfo,
                    source: str, primary: bool = False) -> int | None:
    """A csomag entitása (azonos kulcsú nem fő ajánlat, vagy új); az elsődleges nyelvű oldal
    árazási sora adja a nevét."""
    key = alias_key(name)
    (core_name, core_aliases) = ctx.con.execute(
        "SELECT name, aliases FROM entities WHERE entity_id = ? ORDER BY ALL", [core]).fetchone()
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
                    "END, aliases = list_sort(list_distinct(list_filter(list_append(coalesce(aliases, []), "
                    "name), x -> x <> CASE WHEN ? THEN ? ELSE name END))) WHERE entity_id = ?",
                    [primary, name, primary, name, entity_id])
    ctx.con.execute(
        "INSERT INTO entity_relations (from_id, to_id, type, source, evidence) "
        "VALUES (?, ?, 'part_of', ?, ?) ON CONFLICT DO NOTHING",
        [entity_id, core, source, dumps({"page_id": info.page_id, "name": name},
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


def llm_types(con: duckdb.DuckDBPyConnection) -> dict[tuple[int, int, int, int], tuple[str, str]]:
    """A legutóbbi LLM-futás tárolt rekordjaiból említésenként (oldal, blokk, kezdet, vég) az
    LLM típusa és kanonikus neve."""
    (run_id,) = con.execute("SELECT max(run_id) FROM entity_runs WHERE method = 'llm' ORDER BY ALL"
                            ).fetchone()
    found: dict[tuple[int, int, int, int], tuple[str, str]] = {}
    if run_id is None:
        return found
    for page_id, refined, extraction in con.execute(
            "SELECT page_id, refined, extraction FROM entity_run_pages WHERE run_id = ? "
            "AND status = 'done' ORDER BY ALL", [run_id]).fetchall():
        record = json.loads(refined or extraction or "{}")
        blocks = {f"b{ordinal}": (block_id, text) for block_id, ordinal, text in con.execute(
            "SELECT block_id, ordinal, text FROM blocks WHERE page_id = ? ORDER BY ALL", [page_id]).fetchall()}
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
        (source_name,) = con.execute("SELECT name FROM entities WHERE entity_id = ? ORDER BY ALL",
                                     [entity_id]).fetchone()
        (concept_name,) = con.execute("SELECT name FROM entities WHERE entity_id = ? ORDER BY ALL",
                                      [concept]).fetchone()
        con.execute(
            "INSERT INTO merge_log (run_id, kept_id, removed_id, kept_name, removed_name, rule, "
            "evidence, merged_at) VALUES (?, ?, NULL, ?, ?, 'type_split', ?, ?)",
            [merger.run_id, concept, concept_name, source_name,
             dumps({"from_id": entity_id, "mentions": count}), merger.clock()])
        merger.counts["type_split"] += 1
    return moved


def _service_names(con: duckdb.DuckDBPyConnection) -> dict[int, set[str]]:
    """Service → a neve, az aliasai és az `entity_aliases` sorai normalizált kulccsal."""
    found: dict[int, set[str]] = defaultdict(set)
    for entity_id, name, aliases in con.execute(
            "SELECT entity_id, name, aliases FROM entities WHERE type = 'service' ORDER BY ALL").fetchall():
        found[entity_id] |= {normal_key(f) for f in [name, *(aliases or [])]}
    for entity_id, alias in con.execute(
            "SELECT a.entity_id, a.alias FROM entity_aliases a JOIN entities e "
            "USING (entity_id) WHERE e.type = 'service' ORDER BY ALL").fetchall():
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
                      "WHERE mention_id = ? ORDER BY ALL", [mention_id]).fetchone()
    same = con.execute(
        "SELECT mention_id FROM page_entities WHERE page_id = ? AND block_id IS NOT DISTINCT "
        "FROM ? AND char_start IS NOT DISTINCT FROM ? AND char_end IS NOT DISTINCT FROM ? "
        "AND entity_id = ? ORDER BY ALL", [*row, entity_id]).fetchone()
    if same is None:
        con.execute("UPDATE page_entities SET entity_id = ? WHERE mention_id = ?",
                    [entity_id, mention_id])
        return
    con.execute("INSERT INTO mention_sources (mention_id, source, run_id, llm_call_id, count) "
                "SELECT ?, source, run_id, llm_call_id, count FROM mention_sources "
                "WHERE mention_id = ? ON CONFLICT DO NOTHING", [same[0], mention_id])
    con.execute("DELETE FROM mention_sources WHERE mention_id = ?", [mention_id])
    con.execute("DELETE FROM page_entities WHERE mention_id = ?", [mention_id])


def _offers(con: duckdb.DuckDBPyConnection, split: dict[int, set[int]]) -> int:
    """`offers` a fő ajánlatból és a csomagból a fogalmakhoz: a leválasztott fogalmak
    (`type_split`), és a fogalom, amelynek normalizált kulcsa az ajánlat nevének, navigációs,
    JSON-LD vagy anchor-aliasának, vagy ezek egy címkerészének kulcsa (`name`)."""
    concepts = _concept_index(con)
    con.execute("DELETE FROM entity_relations WHERE type = 'offers'")
    rows = set()
    for entity_id, name, aliases in con.execute(
            "SELECT entity_id, name, aliases FROM entities WHERE type = 'service' "
            "AND tier IN ('core', 'package', 'work_mode') ORDER BY ALL").fetchall():
        forms = {name} | {alias for (alias,) in con.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = ? AND list_contains(?, source) ORDER BY ALL",
            [entity_id, list(OFFER_LABEL_SOURCES)]).fetchall()}
        for form in forms:
            for part in label_parts(form):
                concept = concepts.get(normal_key(part))
                if concept is not None:
                    rows.add((entity_id, concept, "name", dumps({"name": part},
                                                                     ensure_ascii=False)))
        for concept in {resolve(con, c) for c in split.get(entity_id, ())}:
            if concept is not None:
                rows.add((entity_id, concept, "type_split", dumps({})))
    unique: dict[tuple[int, int], tuple] = {}
    for row in sorted(rows):
        unique.setdefault(row[:2], row)
    if unique:
        con.executemany("INSERT INTO entity_relations (from_id, to_id, type, source, evidence) "
                        "VALUES (?, ?, 'offers', ?, ?) ON CONFLICT DO NOTHING",
                        [(a, b, s, e) for a, b, s, e in unique.values()])
    return len(unique)


def apply_overrides(ctx: _Context, merger: Merger, config: SiteConfig) -> int:
    """A `core/sites/<domain>.toml` ajánlat-felülbírálatai: a megnevezett (vagy az URL
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
        name, kind, tier = con.execute("SELECT name, type, tier FROM entities WHERE entity_id = ? ORDER BY ALL",
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
                        [keep, core, dumps({"part_of": override.part_of},
                                                ensure_ascii=False)])
        con.execute(
            "INSERT INTO merge_log (run_id, kept_id, removed_id, kept_name, removed_name, rule, "
            "evidence, merged_at) VALUES (?, ?, NULL, ?, ?, 'override', ?, ?)",
            [merger.run_id, keep, name, name, dumps(
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
            "SELECT entity_id FROM entities WHERE list_contains(?, anchor_page_id) ORDER BY ALL",
            [members]).fetchall()}
    keys = {normal_key(n) for n in names} - {""}
    if keys:
        for entity_id, name, aliases in con.execute(
                "SELECT entity_id, name, aliases FROM entities WHERE type <> 'person' AND "
                "(anchor_page_id IS NOT NULL OR entity_id IN (SELECT entity_id FROM "
                "page_entities)) ORDER BY ALL").fetchall():
            if {normal_key(f) for f in [name, *(aliases or [])]} & keys:
                found.add(entity_id)
        for entity_id, alias in con.execute(
                "SELECT a.entity_id, a.alias FROM entity_aliases a JOIN entities e "
                "USING (entity_id) WHERE e.type <> 'person' ORDER BY ALL").fetchall():
            if normal_key(alias) in keys:
                found.add(entity_id)
    return found
