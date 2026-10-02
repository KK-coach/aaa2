"""Jelölések: demótartalom és sablonismétlés."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

import duckdb

from aaa2.entities import queries as extract_queries
from aaa2.entities.rules import (
    alias_key,
)
from aaa2.resolver.context import SiteRun, _Context
from aaa2.resolver.names import _line

DEMO_TYPES = ("person", "place", "org", "brand", "work", "product", "event")


DEMO_SHARE = 0.9


TEMPLATE_MIN_GROUPS = 3


TEMPLATE_MIN_SHARE = 0.15


LOREM = re.compile(r"lorem ipsum", re.IGNORECASE)


def _demo(con: duckdb.DuckDBPyConnection, run: SiteRun,
          placeholder: set[int] = frozenset()) -> None:
    rows = con.execute(
        "SELECT pe.entity_id, pe.page_id, b.kind, b.text FROM page_entities pe "
        "JOIN entities e USING (entity_id) LEFT JOIN blocks b USING (block_id) "
        "WHERE list_contains(?, e.type) ORDER BY ALL", [list(DEMO_TYPES)]).fetchall()
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
    for page_id, kind, text in [(b.page_id, b.kind, b.text) for b in extract_queries.blocks(con)
                                if b.region == "content" and b.kind != "title"]:
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
            "WHERE b.region = 'content' AND b.kind <> 'title' ORDER BY ALL").fetchall():
        unit = (kind, alias_key(_line(text, start) if kind == "code" else text))
        template = len(unit_groups[unit]) >= needed
        by_entity[entity_id].append(template)
        if template:
            flagged.append(mention_id)
    if flagged:
        con.execute("UPDATE page_entities SET flags = list_sort(list_distinct(list_append(coalesce(flags, "
                    "[]), 'template'))) WHERE list_contains(?, mention_id)", [flagged])
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
