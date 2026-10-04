"""Összevonás (M2 spec, M2/6, 7. pont): a `Merger`, az írásmód és a rövidítés szerinti összevonás, a hreflang-párok azonos helyű headingjei, és az összevont entitás feloldása (`resolve`)."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime

import duckdb

from aaa2.db.stable_json import dumps
from aaa2.entities import queries as extract_queries
from aaa2.entities import store
from aaa2.entities.rules import (
    SOURCE_STRENGTH,
    alias_key,
    stronger_source,
)
from aaa2.resolver.context import _Context
from aaa2.resolver.names import (
    ACRONYM,
    _exists,
    expansions,
    long_form,
    normal_key,
    without_legal_form,
)
from aaa2.resolver.pages import (
    PageInfo,
    representative,
)

SUBTYPE_CLASS = {"package": "distribution", "library": "distribution",
                 "framework": "distribution", "software": "distribution",
                 "platform": "distribution", "language": "distribution",
                 "component": "code", "api_symbol": "code", "feature": "code",
                 "line": "line", "variant": "variant"}


TIER_ORDER = {"core": 0, "package": 1, "work_mode": 1, None: 2, "step": 3}


TIER_GROUP = {"work_mode": "package"}


def clear_resolution(con: duckdb.DuckDBPyConnection) -> None:
    """Az aliasok, a kapcsolatok és az összevonási napló törlése (`entity_aliases`,
    `entity_relations`, `merge_log`); az entitások újraépítése előtt."""
    for table in ("entity_aliases", "entity_relations", "merge_log"):
        con.execute(f"DELETE FROM {table}")


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
        kept = store.entities_for_merger_merge(con, keep)
        gone = store.entities_for_merger_merge(con, remove)
        if kept is None or gone is None:
            return
        for old, new in store.page_entities_for_merger_merge(con, remove, keep):
            store.insert_mention_sources_in_merger_merge(con, new, old)
            store.delete_mention_sources_in_merger_merge(con, old)
            store.delete_page_entities_in_merger_merge(con, old)
        store.update_page_entities_in_merger_merge(con, keep, remove)
        store.update_soft_checks_in_merger_merge(con, keep, remove)
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
        store.update_entities_in_merger_merge(con, forms, stronger_source(kept[2], gone[2]), keep)
        con.execute("INSERT INTO entity_aliases (entity_id, alias, lang, source) "
                    "VALUES (?, ?, NULL, 'merge') ON CONFLICT DO NOTHING", [keep, gone[0]])
        con.execute(
            "INSERT INTO merge_log (run_id, kept_id, removed_id, kept_name, removed_name, rule, "
            "evidence, merged_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [self.run_id, keep, remove, kept[0], gone[0], rule,
             dumps(evidence or {}, ensure_ascii=False), self.clock()])
        store.delete_entities_in_merger_merge(con, remove)
        self.counts[rule] += 1


def _rank(row: tuple) -> tuple:
    """Megtartási sorrend: oldalhoz kötött, core > package > nincs > step, erősebb forrás, több
    említés. `row`: (entity_id, anchor, tier, source, mentions)."""
    entity_id, anchor, tier, source, mentions = row[:5]
    return (anchor is None, TIER_ORDER.get(tier, 2), SOURCE_STRENGTH.get(source, 9), -mentions,
            entity_id)


def _entity_rows(con: duckdb.DuckDBPyConnection) -> dict[int, tuple]:
    """entity_id → (entity_id, anchor, tier, source, említésszám, típus, altípus)."""
    return {row[0]: row for row in store.page_entities_for_entity_rows(con)}


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
    for entity_id, name, kind, aliases in store.entities_for_normalized_merges(con):
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


def _abbreviation_merges(con: duckdb.DuckDBPyConnection, merger: Merger) -> None:
    """A site-on kifejtett rövidítés: ha egy entitás neve vagy aliasa zárójeles kifejtés
    („GEO (Generative Engine Optimization)”), az azonos típusú, oldalhoz nem kötött entitás,
    amelynek a neve maga a rövidítés, a hosszú alak entitásába olvad (`abbreviation`); csak ha
    a rövidítésnek a típuson belül egyetlen entitás adja a kifejtését."""
    rows = _entity_rows(con)
    expanded: dict[tuple[str, str], set[int]] = defaultdict(set)
    named: dict[tuple[str, str], list[int]] = defaultdict(list)
    for entity_id, name, kind, aliases in store.entities_for_normalized_merges(con):
        for form in [name, *(aliases or [])]:
            parts = expansions(form)
            if parts:
                short = parts[0] if ACRONYM.fullmatch(parts[0]) else parts[1]
                expanded[(kind, alias_key(short))].add(entity_id)
        if ACRONYM.fullmatch(name.strip()):
            named[(kind, alias_key(name))].append(entity_id)
    for (kind, key), targets in sorted(expanded.items()):
        if len(targets) != 1:
            continue
        (target,) = targets
        for other in named.get((kind, key), []):
            if other != target and other in rows and target in rows and rows[other][1] is None \
                    and _mergeable(rows[target], rows[other]) and _exists(con, other) \
                    and _exists(con, target):
                merger.merge(target, other, "abbreviation", {"short": key, "type": kind})


def _sections(con: duckdb.DuckDBPyConnection, page_id: int) -> list[tuple[int, list[int]]]:
    """Az oldal H2-szakaszai: (a H2 blokkja, a H3 blokkjai), dokumentum-sorrendben."""
    sections: list[tuple[int, list[int]]] = []
    for block_id, level in [(b.block_id, b.level) for b in extract_queries.page_blocks(con, page_id)
                            if b.region == "content" and b.kind == "heading"
                            and b.level in (2, 3)]:
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
    text = extract_queries.block(con, block_id).text
    found = {entity_id for entity_id, surface in store.page_entities_for_heading_entity(con, block_id) if alias_key(surface) == alias_key(text)}
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
    types = dict(store.entities_for_place_pair(con, [a, b]))
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
    (others,) = store.page_entities_for_only_headings(con, entity_id)
    return others == 0


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
