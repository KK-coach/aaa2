"""Nyelvi változatok összevonása (`language_pair`): a hreflang-pár két oldalának fő témája egy
entitás, ha a két oldal ugyanarról szól, csak más nyelven nevezi meg.

Két entitás akkor olvad össze, ha mind teljesül:

1. a két oldal hreflang-pár: egy oldalcsoport két, eltérő nyelvű tagja, és mindkettő saját címe
   szerepel a saját hreflang-készletében (a canonical-duplikátum a cél hreflangját hordozza,
   a saját címe nincs benne: nem pár-tag);
2. mindkét entitás a saját oldalának fő témája: az oldal kinyerésének első megnevezett fő
   entitása (`primary_entities[0]`), név szerint feloldva (`_Topics`);
3. a típusuk és az altípusuk azonos, és a típus fogalom, szolgáltatás vagy technológia
   (`PAIR_TYPES`): a termék (minden altípusával) és a mű (cikk, kurzus) nem olvad össze, mert
   ott a két nyelvi oldal első fő entitása gyakran más-más tétel;
4. egyik oldal sem kezdőoldal (a csoportnak nincs kezdőoldal tagja) vagy listaoldal
   (`listing.ListShape.list_page`);
5. mindkét oldalnak van szövegtörzse: legalább egy tartalmi bekezdés (a csupa címsorból álló
   oldal, pl. étlap vagy borlap, felsorolás: az első tétele nem az oldal témája).

A megtartott entitás a site elsődleges nyelvén álló oldalé (ha a pár egyik tagja sem ilyen, a
nyelv, majd az oldalszám szerinti első), a neve változatlan; a másik név alias az oldala
nyelvével és `hreflang` forrással. Az összevonás a `merge_log`-ba kerül a szabály nevével és a
két oldal URL-jével. A két oldal külön oldal marad. Nem olvad össze, amit a `merge._mergeable`
kizár (két más-más oldalhoz kötött entitás, eltérő szint), és az sem, ahol csak a nem megtartott
entitás van oldalhoz kötve (a kötés elveszne).

Az összevonás megfeleltetés a két nyelvi alak között, következtetésből: a kimenet külön
fájlban sorolja fel (`queries.language_pair_merges`, `<név>-view-language-pairs.csv`)."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from itertools import combinations

import duckdb

from aaa2.engine import queries as crawl
from aaa2.engine.normalize import page_url
from aaa2.entities import store
from aaa2.entities.rules import alias_key
from aaa2.resolver import queries as resolver_queries
from aaa2.resolver.context import _Context
from aaa2.resolver.listing import ListShape
from aaa2.resolver.merge import Merger, _entity_rows, _mergeable
from aaa2.resolver.names import _exists, without_legal_form
from aaa2.resolver.overrides import SiteConfig
from aaa2.resolver.pages import PageInfo, home_urls, page_types

RULE = "language_pair"
PAIR_TYPES = ("concept", "service", "tech")
EXCLUDED_FLAGS = ("demo", "navigational")


class _Topics:
    """Az oldalak saját fő témája: a kinyerő modell legutóbbi kész rekordjának első fő entitása
    (`primary_entities[0]`), név szerint feloldva. Több azonos nevű entitás közül az, amelyiknek
    ez a neve (nem csak aliasa), az oldalon említett, a több említésű."""

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.entities = {row[0]: row for row in store.entities_for_language_pairs(con)}
        excluded = {entity_id for entity_id, row in self.entities.items()
                    if set(row[5] or []) & set(EXCLUDED_FLAGS)}
        self.index: dict[str, set[int]] = defaultdict(set)
        forms = [(entity_id, form) for entity_id, row in self.entities.items()
                 for form in [row[1], *(row[4] or [])]]
        forms += [(alias.entity_id, alias.alias) for alias in resolver_queries.aliases(con)]
        for entity_id, form in forms:
            if entity_id not in self.entities or entity_id in excluded or not form:
                continue
            variants = {form}
            if self.entities[entity_id][2] == "org":
                variants.add(without_legal_form(form))
            for variant in variants:
                if alias_key(variant):
                    self.index[alias_key(variant)].add(entity_id)
        self.on_page: dict[int, set[int]] = defaultdict(set)
        self.counts: Counter[int] = Counter()
        for page_id, entity_id, mentions in store.page_entities_for_language_pairs(con):
            self.on_page[page_id].add(entity_id)
            self.counts[entity_id] += mentions
        self.primary: dict[int, str] = {}
        for page_id, value in store.entity_runs_for_language_pairs(con):
            if page_id in self.primary or value is None:
                continue
            names = [n for n in json.loads(value) if isinstance(n, str)]
            self.primary[page_id] = names[0] if names else ""

    def own(self, page_id: int) -> int | None:
        key = alias_key(self.primary.get(page_id) or "")
        pool = self.index.get(key, set()) if key else set()
        if not pool:
            return None
        on_page = self.on_page.get(page_id, set())
        return min(pool, key=lambda e: (alias_key(self.entities[e][1] or "") != key,
                                        e not in on_page, -self.counts[e], e))


def pair_members(ctx: _Context) -> list[list[PageInfo]]:
    """A hreflang-párok tagjai oldalcsoportonként: azok az oldalak, amelyeknek a saját címe
    szerepel a saját hreflang-készletükben; a csoportban legalább két nyelv. Sorrend: a site
    elsődleges nyelve elöl, aztán a nyelv és az oldalszám."""
    alternates = {meta.page_id: {page_url(entry.split("|", 1)[-1]) for entry in meta.hreflang}
                  for meta in crawl.page_metas(ctx.con)}
    groups: dict[str, list[PageInfo]] = defaultdict(list)
    for info in ctx.roles.values():
        if page_url(info.url) in alternates.get(info.page_id, set()):
            groups[info.group].append(info)
    return [sorted(members, key=lambda m: (m.lang != ctx.site_lang, m.lang or "", m.page_id))
            for _, members in sorted(groups.items())
            if len({m.lang for m in members}) >= 2]


def _language_pairs(ctx: _Context, merger: Merger, config: SiteConfig) -> None:
    con = ctx.con
    groups = pair_members(ctx)
    if not groups:
        return
    homes = {page_url(url) for url in home_urls(con)}
    kinds = page_types(con, config.page_types)
    shape = ListShape(con, ctx.roles)
    topics = _Topics(con)
    rows = _entity_rows(con)
    moved: dict[int, int] = {}

    def current(entity_id: int | None) -> int | None:
        while entity_id in moved:
            entity_id = moved[entity_id]
        return entity_id

    for members in groups:
        if any(page_url(m.url) in homes for m in members):
            continue
        usable = [m for m in members if shape.has_body(m.page_id)
                  and not shape.list_page(m, kinds.get(m.page_id))]
        for first, second in combinations(usable, 2):
            if first.lang == second.lang:
                continue
            keep, other = current(topics.own(first.page_id)), current(topics.own(second.page_id))
            if keep is None or other is None or keep == other \
                    or not (_exists(con, keep) and _exists(con, other)):
                continue
            kept, gone = topics.entities[keep], topics.entities[other]
            if kept[2] not in PAIR_TYPES or (kept[2], kept[3]) != (gone[2], gone[3]) \
                    or not _mergeable(rows[keep], rows[other]) \
                    or (rows[other][1] is not None and rows[keep][1] is None):
                continue
            merger.merge(keep, other, RULE, {
                "pages": [first.url, second.url], "langs": [first.lang, second.lang],
                "names": [kept[1], gone[1]], "type": kept[2], "subtype": kept[3]})
            con.execute("INSERT INTO entity_aliases (entity_id, alias, lang, source) "
                        "VALUES (?, ?, ?, 'hreflang') ON CONFLICT DO NOTHING",
                        [keep, gone[1], second.lang])
            moved[other] = keep
