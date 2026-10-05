"""A listaoldal szerkezeti jelei (`ListShape`): a kivonatok és a táblázatsorok aránya az oldal
tartalmi blokkjai között, és az oldal szövegtörzse (a bekezdések száma). A gráf szerepdöntése
(`functions/graph.py`, `role_of`) és a feloldó nyelvi összevonása (`resolver/language.py`)
ugyanezt a mérést használja."""
from __future__ import annotations

from collections import Counter, defaultdict
from urllib.parse import urlsplit

import duckdb

from aaa2.engine import queries as crawl
from aaa2.entities import queries as extract_queries
from aaa2.entities.rules import alias_key
from aaa2.resolver.pages import PageInfo, canonical_targets

CATEGORY_SEGMENTS = frozenset({"category", "kategoria"})
LIST_MIN_SHARE = 0.5                     # a teaser-blokkok aránya ennél nagyobb: lista
LIST_MIN_ROWS = 3                        # legalább ennyi táblázatsor: táblázatos lista lehet
EXCERPT_CHARS = 40                       # a kivonat-teaser legalább ennyi jelnyi eleje
LIST_TYPES = frozenset({"list", "category", "brand_category"})
LIST_ROLES = ("support", "article")


class ListShape:
    """Oldalanként a tartalmi szövegblokkok (heading és bekezdés, kulcs szerint), a tartalmi
    táblázatsorok és bekezdések száma, és a más oldalcsoportra mutató linkek (céloldal, az
    anchor kulcsa; a canonical-duplikátumra mutató link az eredetié)."""

    def __init__(self, con: duckdb.DuckDBPyConnection, roles: dict[int, PageInfo]):
        originals = canonical_targets(con, {info.page_id: info.url for info in roles.values()})
        self.outbound: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for from_id, to_id, anchor in sorted(
                ((link.from_page_id, link.to_page_id, link.anchor)
                 for link in crawl.links(con) if link.to_page_id is not None),
                key=lambda row: (row[0], row[1], row[2] is None, row[2] or "")):
            to_id = originals.get(to_id, to_id)
            if from_id in roles and to_id in roles and roles[from_id].group != roles[to_id].group:
                self.outbound[from_id].append((to_id, alias_key(anchor or "")))
        self.texts: dict[int, list[str]] = defaultdict(list)
        self.rows: Counter[int] = Counter()
        self.paragraphs: Counter[int] = Counter()
        for page_id, kind, text in [(b.page_id, b.kind, b.text)
                                    for b in extract_queries.blocks(con)
                                    if b.region == "content"
                                    and b.kind in ("heading", "paragraph", "table_row")]:
            if kind == "table_row":
                self.rows[page_id] += 1
                continue
            if kind == "paragraph":
                self.paragraphs[page_id] += 1
            if alias_key(text or ""):
                self.texts[page_id].append(alias_key(text))

    def table_share(self, info: PageInfo) -> float:
        """A tartalmi blokkok (heading és bekezdés a H1 nélkül, táblázatsor) hányad része
        táblázatsor, ha legalább `LIST_MIN_ROWS` sor van: a jórészt táblázatból álló segédoldal
        (letöltések, katalógusok listája) lista, nem egy entitásról szól."""
        rows = self.rows[info.page_id]
        if rows < LIST_MIN_ROWS:
            return 0.0
        texts = [b for b in self.texts.get(info.page_id, []) if b != alias_key(info.h1 or "")]
        return rows / (rows + len(texts))

    def teaser_share(self, info: PageInfo) -> float:
        """A tartalmi heading- és bekezdésblokkok (a H1 nélkül) hányad része teaser: egy más
        oldalcsoportra mutató link szövege (cím), vagy legalább `EXCERPT_CHARS` jelnyi eleje a
        linkelt oldal egy blokkjának eleje (kivonat)."""
        blocks = [b for b in self.texts.get(info.page_id, []) if b != alias_key(info.h1 or "")]
        if not blocks:
            return 0.0
        links = self.outbound.get(info.page_id, [])
        anchors = {anchor for _, anchor in links if anchor}
        target = [text for page in {t for t, _ in links} for text in self.texts.get(page, [])]
        teasers = sum(1 for b in blocks if b in anchors or (
            len(b) >= EXCERPT_CHARS and any(t.startswith(b[:EXCERPT_CHARS]) for t in target)))
        return teasers / len(blocks)

    def list_page(self, info: PageInfo, kind: str | None) -> bool:
        """Listaoldal az oldaltípusa (lista, kategória, márka × kategória), a kategória-
        útvonalszegmense vagy a szerkezete szerint (segéd- vagy cikkszerepű oldal, amelynek
        tartalma döntően kivonat vagy táblázatsor)."""
        if kind in LIST_TYPES:
            return True
        if info.role not in LIST_ROLES:
            return False
        return bool(CATEGORY_SEGMENTS & set(urlsplit(info.url).path.lower().split("/"))) \
            or self.teaser_share(info) > LIST_MIN_SHARE \
            or self.table_share(info) > LIST_MIN_SHARE

    def has_body(self, page_id: int) -> bool:
        """Van-e az oldalnak szövegtörzse: legalább egy tartalmi bekezdés."""
        return self.paragraphs[page_id] > 0
