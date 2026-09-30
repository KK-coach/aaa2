"""Kitöltőszöveg (placeholder) felismerése (M2 spec, M2/6, 6. pont, oldalszinten).

A szókészlet a szabványos kitöltőszövegek: a „Lorem ipsum dolor sit amet…” bekezdés és a Cicero
„De finibus” 1.10.32–33 részlete („Sed ut perspiciatis…”, „At vero eos…”), amelyekből a
kitöltőszöveg-generátorok dolgoznak. Az arány a legalább `MIN_LETTERS` betűs szavakon számít
(a rövid latin szavak, pl. „a”, „in”, „et”, más nyelvben is gyakoriak).

- `placeholder_share(text)`: a szöveg szavainak hányad része a szókészletből való;
- `placeholder_pages(con)`: azok az oldalak, amelyeknek a content-régiós szövege (a title
  nélkül) legalább `PAGE_MIN_WORDS` szó, és legalább `PAGE_SHARE` része kitöltőszöveg. Az ilyen
  oldal nem hoz létre oldalhoz kötött entitást, és a rajta álló említés demó-környezet
  (`site._page_entities`, `site._demo`).
"""
from __future__ import annotations

import re

import duckdb

PLACEHOLDER_TEXT = """
Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut
labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco
laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in
voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat
non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.
Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque
laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto
beatae vitae dicta sunt explicabo. Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut
odit aut fugit, sed quia consequuntur magni dolores eos qui ratione voluptatem sequi nesciunt.
Neque porro quisquam est, qui dolorem ipsum quia dolor sit amet, consectetur, adipisci velit,
sed quia non numquam eius modi tempora incidunt ut labore et dolore magnam aliquam quaerat
voluptatem. Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit
laboriosam, nisi ut aliquid ex ea commodi consequatur? Quis autem vel eum iure reprehenderit qui
in ea voluptate velit esse quam nihil molestiae consequatur, vel illum qui dolorem eum fugiat
quo voluptas nulla pariatur?
At vero eos et accusamus et iusto odio dignissimos ducimus qui blanditiis praesentium voluptatum
deleniti atque corrupti quos dolores et quas molestias excepturi sint occaecati cupiditate non
provident, similique sunt in culpa qui officia deserunt mollitia animi, id est laborum et dolorum
fuga. Et harum quidem rerum facilis est et expedita distinctio. Nam libero tempore, cum soluta
nobis est eligendi optio cumque nihil impedit quo minus id quod maxime placeat facere possimus,
omnis voluptas assumenda est, omnis dolor repellendus. Temporibus autem quibusdam et aut
officiis debitis aut rerum necessitatibus saepe eveniet ut et voluptates repudiandae sint et
molestiae non recusandae. Itaque earum rerum hic tenetur a sapiente delectus, ut aut reiciendis
voluptatibus maiores alias consequatur aut perferendis doloribus asperiores repellat.
"""
MIN_LETTERS = 3
PAGE_MIN_WORDS = 20
PAGE_SHARE = 0.5
WORD = re.compile(r"[^\W\d_]+")
PLACEHOLDER_WORDS = frozenset(w.lower() for w in WORD.findall(PLACEHOLDER_TEXT)
                              if len(w) >= MIN_LETTERS)


def words(text: str) -> list[str]:
    return [w.lower() for w in WORD.findall(text or "") if len(w) >= MIN_LETTERS]


def placeholder_share(text: str) -> float:
    """A legalább `MIN_LETTERS` betűs szavak hányad része a kitöltőszöveg szókészletéből való
    (0, ha nincs ilyen szó)."""
    found = words(text)
    return sum(w in PLACEHOLDER_WORDS for w in found) / len(found) if found else 0.0


def placeholder_pages(con: duckdb.DuckDBPyConnection) -> set[int]:
    """Az oldalak, amelyeknek a content-régiós szövege (a title nélkül) legalább
    `PAGE_MIN_WORDS` szó, és legalább `PAGE_SHARE` része kitöltőszöveg."""
    found = set()
    for page_id, text in con.execute(
            "SELECT page_id, string_agg(text, ' ' ORDER BY ordinal) FROM blocks "
            "WHERE region = 'content' AND kind <> 'title' GROUP BY page_id").fetchall():
        if len(words(text)) >= PAGE_MIN_WORDS and placeholder_share(text) >= PAGE_SHARE:
            found.add(page_id)
    return found
