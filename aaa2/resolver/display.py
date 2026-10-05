"""A megjelenített név (`DisplayNames`): csak megjelenítés, az entitások és a gráf nem változik.

- Oldalszintű kimenetben az entitás neve az oldal nyelvén áll, ha az oldal nyelve nem a site
  elsődleges nyelve, és az entitásnak van ilyen nyelvű neve a nyelvi összevonásból
  (`language_pair`: a beolvadt entitás neve, `hreflang` forrású alias); minden más esetben a
  megtartott név. Az oldalhoz kötött entitások többi nyelvű aliasa (menüpont, horgonyszöveg,
  H1, title, JSON-LD név) nem megjelenített név: rövidített címke vagy szlogen is lehet.
- Site-szintű kimenetben a megtartott név marad; a többi nyelvű név (minden nyelvvel jelölt
  alias, amelynek a nyelve nem a site elsődleges nyelve) külön oszlopban áll, nyelvvel és
  forrással."""
from __future__ import annotations

from collections import defaultdict

import duckdb

from aaa2.resolver import queries as resolver_queries
from aaa2.resolver.overrides import canonical_language, load_site_config, site_domain
from aaa2.resolver.pages import primary_lang

OTHER_NAMES_COLUMN = "más nyelvű nevek"


class DisplayNames:
    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.site_lang = canonical_language(con, load_site_config(site_domain(con)))
        self.pair = resolver_queries.language_pair_names(con)
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
        return self.pair.get(entity_id, {}).get(lang, name)

    def other_names(self, entity_id: int) -> list[dict]:
        """A többi nyelvű név: nyelv, név, forrás; a nyelv, a név és a forrás szerint."""
        return [{"lang": lang, "name": name, "source": source}
                for lang, name, source in self.other.get(entity_id, [])]

    def other_text(self, entity_id: int) -> str:
        """A többi nyelvű név egy cellában: `hu: Oldalsebesség (hreflang); …`."""
        return "; ".join(f"{lang}: {name} ({source})"
                         for lang, name, source in self.other.get(entity_id, []))
