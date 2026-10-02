"""Navigáció: a más csoportból egy entitásoldalra mutató anchorok és a kártyacímek, és a belőlük lett említések."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from aaa2.engine import queries as crawl
from aaa2.entities import queries as extract_queries
from aaa2.entities.rules import (
    alias_key,
    find_name,
    trivial_anchor,
)
from aaa2.resolver.context import _Context
from aaa2.resolver.names import Name, _title_forms
from aaa2.resolver.pages import (
    PageInfo,
)

ANCHOR_MAX_WORDS = 6


CARD_LOOKBACK = 3


CARD_TITLE_WORDS = 5


def _qualified_anchors(ctx: _Context) -> dict[str, list[tuple[int, int, str, str, str]]]:
    """Csoportonként a más csoportból rá mutató, alias-képes anchorok: nem triviális, a szövege
    az egész site-on csak ide mutat, és legfeljebb `ANCHOR_MAX_WORDS` szó, vagy egyezik egy
    tagoldal H1-ével vagy title-jével (cikkcímek). (forrásoldal, céloldal, szöveg, pozíció, a
    forrásoldal nyelve)."""
    roles = ctx.roles
    rows = [(link.from_page_id, link.to_page_id, link.anchor, link.position)
            for link in crawl.links(ctx.con)
            if link.anchor is not None and link.to_page_id is not None]
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
    for from_id, anchor, to_id in [(link.from_page_id, link.anchor, link.to_page_id)
                                   for link in crawl.links(ctx.con) if link.anchor is not None]:
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
    for ordinal, kind, text in [
            (b.ordinal, b.kind, b.text)
            for b in extract_queries.page_blocks(ctx.con, info.page_id)
            if b.region == "content"
            and (b.kind == "title" or (b.kind == "heading" and b.level == 1))]:
        position = "title" if kind == "title" else "h1"
        candidates = sorted({n.text for n in names}, key=len, reverse=True)
        target = next((c for c in candidates if find_name(text, alias_key(c))), None)
        if target is not None:
            _add_mention(ctx, entity_id, info.page_id, ordinal, target, position)


def _add_mention(ctx: _Context, entity_id: int, page_id: int, ordinal: int, name: str,
                 position: str) -> int:
    """Egy említés a blokkban a név helyén (source = rule, a site-futás); visszaad: 1, ha új
    forrás került be."""
    found = extract_queries.block_at(ctx.con, page_id, ordinal)
    if found is None:
        return 0
    block = (found.block_id, found.text)
    span = find_name(block[1], alias_key(name))
    if span is None:
        return 0
    start, end = span
    found = ctx.con.execute(
        "SELECT mention_id FROM page_entities WHERE page_id = ? AND block_id = ? "
        "AND char_start = ? AND char_end = ? AND entity_id = ? ORDER BY ALL",
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
