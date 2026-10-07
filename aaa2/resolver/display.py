"""A megjelenített név (`DisplayNames`): csak megjelenítés, az entitások és a gráf nem változik.

- Oldalszintű kimenetben az entitás neve az oldal nyelvén áll, ha az oldal nyelve nem a site
  elsődleges nyelve, és az entitásnak van ilyen nyelvű neve a site-on. Két forrás, ebben a
  sorrendben: az entitás saját oldalának strukturált adata (az ilyen nyelvű oldalon álló,
  az oldalra mutató schema.org-csomópont neve, `own_schema_names`), és a nyelvi összevonás
  (`language_pair`: a beolvadt entitás neve, `hreflang` forrású alias). Minden más esetben a
  megtartott név. Az oldalhoz kötött entitások többi nyelvű aliasa (menüpont, horgonyszöveg,
  H1, title, más oldal rövidített hivatkozása) nem megjelenített név: rövidített címke vagy
  szlogen is lehet.
- Ha az oldal nyelvén nincs neve az entitásnak, a kimenet ezt jelöli (`note`,
  `MISSING_NAME`), a megtartott név mellett. Nem hiány: a nyelvtől független név (szervezet,
  személy, márka, technológia, hely: `NEUTRAL_TYPES`; a csupa nagybetűs rövidítés), és az az
  oldalhoz nem kötött entitás, amelyet a kinyerés ezen a nyelven látott először.
- Site-szintű kimenetben a megtartott név marad; a többi nyelvű név (minden nyelvvel jelölt
  alias, amelynek a nyelve nem a site elsődleges nyelve) külön oszlopban áll, nyelvvel és
  forrással."""
from __future__ import annotations

import html as html_lib
from collections import defaultdict

import duckdb

from aaa2.engine import queries as crawl
from aaa2.entities import store
from aaa2.resolver import queries as resolver_queries
from aaa2.resolver.overrides import canonical_language, load_site_config, site_domain
from aaa2.resolver.pages import primary_lang, same_page, schema_nodes

OTHER_NAMES_COLUMN = "más nyelvű nevek"
NAME_NOTE_COLUMN = "a név az oldal nyelvén"
MISSING_NAME = "nincs az oldal nyelvén név"
NEUTRAL_TYPES = ("org", "person", "brand", "tech", "place")
PAGE_NODE_TYPES = ("WebPage", "WebSite", "CollectionPage", "ItemPage", "AboutPage")


def own_schema_names(con: duckdb.DuckDBPyConnection) -> dict[int, dict[str, str]]:
    """Entitás → nyelv → az entitás saját oldalának strukturált adatából jövő név: az adott
    nyelvű oldalon álló, magára az oldalra mutató (`url`, vagy `url` híján `@id`) csomópont
    neve, ha az entitásnak ez ilyen nyelvű, `schema` forrású aliasa. Az oldalt leíró csomópont
    (`PAGE_NODE_TYPES`) neve csak akkor, ha más csomópont nem ad nevet."""
    tagged: dict[tuple[str, str], set[int]] = defaultdict(set)
    for alias in resolver_queries.aliases(con):
        if alias.source == "schema" and primary_lang(alias.lang):
            tagged[(alias.alias, primary_lang(alias.lang))].add(alias.entity_id)
    pages = {page.page_id: (page.url, primary_lang(page.lang)) for page in crawl.pages(con)}
    found: dict[int, dict[str, str]] = {}
    for page_id, nodes in sorted(schema_nodes(con).items()):
        url, lang = pages.get(page_id, (None, None))
        if not url or not lang:
            continue
        own = []
        for node in nodes:
            target = node.get("url") if isinstance(node.get("url"), str) else node.get("@id")
            name = node.get("name")
            if isinstance(target, str) and isinstance(name, str) and same_page(target, url):
                types = node.get("@type")
                types = types if isinstance(types, list) else [types]
                generic = any(str(t).rsplit("/", 1)[-1] in PAGE_NODE_TYPES for t in types)
                own.append((generic, html_lib.unescape(name).strip()))
        for _, name in sorted(own, key=lambda item: item[0]):
            for entity_id in sorted(tagged.get((name, lang), ())):
                found.setdefault(entity_id, {}).setdefault(lang, name)
    return found


class DisplayNames:
    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.site_lang = canonical_language(con, load_site_config(site_domain(con)))
        self.pair = resolver_queries.language_pair_names(con)
        self.own = own_schema_names(con)
        self.entities = {row[0]: row[1:] for row in store.entities_for_display_names(con)}
        self.other: dict[int, list[tuple[str, str, str]]] = defaultdict(list)
        for alias in resolver_queries.aliases(con):
            if alias.lang and primary_lang(alias.lang) != self.site_lang:
                self.other[alias.entity_id].append((alias.lang, alias.alias, alias.source))
        for names in self.other.values():
            names.sort()

    def on_page(self, entity_id: int | None, name: str, page_lang: str | None) -> str:
        """Az entitás neve egy `page_lang` nyelvű oldal kimenetében."""
        lang = primary_lang(page_lang)
        if entity_id is None or lang is None or self.site_lang is None \
                or lang == self.site_lang:
            return name
        return self.own.get(entity_id, {}).get(lang) or self.pair.get(entity_id, {}).get(
            lang, name)

    def note(self, entity_id: int | None, page_lang: str | None) -> str:
        """`MISSING_NAME`, ha az entitásnak nincs neve az oldal nyelvén (lásd a modul leírását);
        különben üres."""
        lang = primary_lang(page_lang)
        if entity_id is None or lang is None or self.site_lang is None \
                or lang == self.site_lang or entity_id not in self.entities:
            return ""
        if lang in self.own.get(entity_id, {}) or lang in self.pair.get(entity_id, {}):
            return ""
        name, kind, entity_lang, anchor = self.entities[entity_id]
        letters = [c for c in name or "" if c.isalpha()]
        if kind in NEUTRAL_TYPES or (letters and all(c.isupper() for c in letters)):
            return ""
        if anchor is None and primary_lang(entity_lang) == lang:
            return ""
        return MISSING_NAME

    def other_names(self, entity_id: int) -> list[dict]:
        """A többi nyelvű név: nyelv, név, forrás; a nyelv, a név és a forrás szerint."""
        return [{"lang": lang, "name": name, "source": source}
                for lang, name, source in self.other.get(entity_id, [])]

    def other_text(self, entity_id: int) -> str:
        """A többi nyelvű név egy cellában: `hu: Oldalsebesség (hreflang); …`."""
        return "; ".join(f"{lang}: {name} ({source})"
                         for lang, name, source in self.other.get(entity_id, []))
