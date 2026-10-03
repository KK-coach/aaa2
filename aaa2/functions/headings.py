"""Heading-fa oldalanként (H1–H6) a meglévő blokkokból, a headingekben álló entitásokkal, a
kapcsolatukkal az oldal fő entitásához, és a szerkezeti megállapítások (AAAV2-67), LLM nélkül.

- A fa: minden heading egy szakaszt nyit, amely a következő azonos vagy magasabb szintű
  headingig tart, és tartalmazza az alárendelt headingeket és a tartalmat. A szakasz saját
  tartalma (`words`) a következő headingig álló, nem heading content-blokkok szavainak száma; a
  teljes tartalma (`total_words`) az alárendelt szakaszokéval együtt.
- Kimarad a fából a chrome-régió headingje (fejléc, menü, lábléc, oldalsáv; kivéve a fő
  tartalom saját fejlécében álló H1-et, pl. a cikk címsorát: az a blokkmodellben chrome-régiós,
  de a `dom.heading_scan` szerint a tartalom része).
- Sablon-heading (`template`): a content-régió H2–H6 headingje, amelynek szövege (kulcs
  szerint, azonos szinten) a site oldalcsoportjainak legalább `flags.TEMPLATE_MIN_GROUPS`,
  illetve `flags.TEMPLATE_MIN_SHARE` részében ismétlődik (ugyanaz a szabály, mint az említések
  sablonjelölésénél). A sablon-heading nem lehet megállapítás tárgya (nem üres szakasz, nem
  kihagyott szint), de a fában marad, és a szintjét tartja: az alatta álló heading szülője, és
  H2-ként az oldalnak van H2-je (a teljes kihagyása az alatta álló H3-akat kihagyott szintnek,
  az oldalt H2 nélkülinek mutatná). A H1 nem lehet sablon-heading: a több oldalon azonos H1 is
  az oldal H1-e.
- Entitások: a heading-blokkban álló említések entitásai (`page_entities`).
- Kapcsolat az oldal fő entitásához (`relation`), entitásonként és headingenként (a
  legerősebb): `main` (maga a fő entitás), `related` (él a fő entitás és az entitás között,
  bármelyik irányban, vagy közös szülő: mindkettőből él vezet ugyanarra az entitásra),
  `unrelated` (független), `no_entity` (nincs benne entitás); ha az oldalnak nincs fő
  entitása: `no_main`.
- H1 a fő tartalmon kívül (`dom.heading_scan`): a renderelt DOM H1-e cookie- vagy consent-
  elemben, dialógusban (popup, modális ablak) vagy chrome-régióban.
- Szerkezeti megállapítások (`structure_findings`), az azonos okú megállapítás oldalcsoportonként
  (hreflang-pár, fül nélküli URL) egy tétel:
  - `missing_h1` (high): az oldalon sehol nincs H1 (a blokkokban és a DOM-ban sem:
    `dom.heading_scan`; a blokkmodell a linkbe ágyazott headinget nem adja ki);
  - `h1_outside_content` (medium): H1 a fő tartalmon kívül;
  - `multiple_h1` (low): több H1 a fő tartalomban, indokolatlanul. Indokolt, ha minden H1 az
    oldal egy külön fő vagy másodlagos entitását nevezi meg, és mindegyik H1-szakasz alatt
    érdemi tartalom (legalább `SUBSTANTIVE_WORDS` szó) vagy saját alcím áll; különben az ok:
    ugyanarra az entitásra mutatnak, valamelyikben nincs entitás, valamelyik nem az oldal fő
    vagy másodlagos entitása, vagy valamelyik H1-szakasz üres. Az indokolt eset nem
    megállapítás, csak a nézetben látszik (`h1_justified`);
  - `empty_section` (low): a heading után rögtön azonos vagy magasabb szintű heading jön,
    alcím nélkül (H1-nél ez a „H1 után H1”), és a renderelt DOM-ban a kettő között nincs szöveg
    (a rejtett szöveg is tartalom: lenyíló panel, fül) és nincs tartalmi elem sem (kép, videó,
    beágyazás, űrlap, űrlapelem, táblázat, linkbe ágyazott elem; `dom.heading_gaps`). Ha csak
    szöveg nélküli tartalmi elem áll ott, a szakasz nem üres: a nézetben megjegyzést kap
    (`media_only`: „csak kép / űrlap”), megállapítás nincs. Az oldal utolsó headingje nem
    vizsgált. Ha a heading a DOM-ban nem található (a szövege nem illeszthető), a blokkok
    szerinti eredmény marad;
  - `skipped_level` (low): a heading szintje több mint eggyel mélyebb a szülőjénél (H2 után H4;
    egy szakaszban mélyebb szint jön előbb, mint a közvetlen alárendelt);
  Az `empty_section` és a `skipped_level` megállapításai oldalszerep és szintminta szerint egy
  tételbe kerülnek, az oldalak listájával (ahogy a H1/title-eltérésnél). Szintminta: üres
  szakasznál az érintett szintek („H2, H3”), kihagyott szintnél a szülő → gyerek ugrások
  („H2→H4”). A minta oldalcsoportonként (hreflang-pár, fül nélküli URL) az oldalak mintáinak
  uniója, így a pár egy tételben marad; a csoport szerepe az első (URL szerint) érintett
  oldaláé. A többi típus oldalcsoportonként egy tétel.
  - `missing_h2` (low): a content-régió szövege `LONG_CONTENT_WORDS` szó fölött van, és nincs
    H2 (a blokkokban és a DOM fő tartalmában sem).
  A H2–H6 szintű „nincs benne entitás” csak a nézetben látszik.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

import duckdb

from aaa2.engine import queries as crawl
from aaa2.entities import queries as extract_queries
from aaa2.entities import store
from aaa2.entities.dom import heading_gaps, heading_scan
from aaa2.entities.rules import alias_key
from aaa2.functions import graph_queries
from aaa2.resolver.flags import TEMPLATE_MIN_GROUPS, TEMPLATE_MIN_SHARE

LONG_CONTENT_WORDS = 600                # e fölött a H2 hiánya megállapítás
# Egy H1-szakasz érdemi tartalma legalább ennyi szó. Mérés a négy site 346 H1-szakaszán
# (2026-10-03): a kétszer kiírt H1 (címsáv) alatti rész 6–11 szó, a valódi H1-szakaszok
# legrövidebbje site-onként 34–74 szó; a küszöb a kettő között áll.
SUBSTANTIVE_WORDS = 40
RELATION_ORDER = ("main", "related", "unrelated")
PATTERN_TYPES = ("empty_section", "skipped_level")     # szerep és szintminta szerint összevonva
TYPE_SUMMARY = {"empty_section": "üres szakasz", "skipped_level": "kihagyott heading-szint"}
STRUCTURE_TYPES = ("missing_h1", "h1_outside_content", "multiple_h1", "empty_section",
                   "skipped_level", "missing_h2")
SEVERITY = {"missing_h1": "high", "h1_outside_content": "medium", "multiple_h1": "low",
            "empty_section": "low", "skipped_level": "low", "missing_h2": "low"}
OUTSIDE_LABELS = {"cookie": "cookie- vagy consent-elem", "dialog": "popup vagy modális ablak",
                  "chrome": "fejléc, menü, lábléc vagy oldalsáv"}


def page_headings(con: duckdb.DuckDBPyConnection, pages: Mapping[int, Mapping],
                  chosen: Mapping[int, list[tuple]], names: Mapping[int, str]
                  ) -> dict[int, dict]:
    """Oldalanként a heading-fa és a szerkezeti tények. `pages`: oldal → {url, group, …} (a
    gráf oldal-csomópontjai); `chosen`: oldal → (entitás, szerep, …) a fő és a másodlagos
    entitásokra; `names`: entitás → név. Az oldal eredménye: `tree` (a gyökér-headingek, alattuk
    a `children`), `h1` (a fő tartalom H1-ei), `outside_h1` ((hol, szöveg) párok),
    `content_words`, `has_h2`, `template` (a kihagyott sablon-headingek száma), és a több H1
    megítélése (`h1_justified`: True / False, vagy None, ha legfeljebb egy H1 van; `h1_reasons`).
    A fa elemein: `empty` (üres szakasz), `media_only` (szöveg nélküli, de tartalmi elem áll
    benne), `skipped_level` és `parent_level` (a szülő szintje; None a gyökérnél)."""
    groups = {page_id: page["group"] for page_id, page in pages.items()}
    blocks: dict[int, list] = defaultdict(list)
    unit_groups: dict[tuple[int, str], set[str]] = defaultdict(set)
    for block in extract_queries.blocks(con):
        if block.page_id not in groups or block.kind == "title":
            continue
        blocks[block.page_id].append(block)
        if block.kind == "heading" and block.region == "content":
            unit_groups[(block.level or 1, alias_key(block.text))].add(groups[block.page_id])
    needed = max(TEMPLATE_MIN_GROUPS, TEMPLATE_MIN_SHARE * len(set(groups.values())))
    template = {key for key, found in unit_groups.items() if len(found) >= needed}
    mentions: dict[int, list[int]] = defaultdict(list)
    for _, block_id, entity_id in store.heading_mentions(con):
        if entity_id not in mentions[block_id]:
            mentions[block_id].append(entity_id)
    linked: dict[int, set[int]] = defaultdict(set)          # entitás → szomszédok (bármely irány)
    parents: dict[int, set[int]] = defaultdict(set)         # entitás → ahová él vezet belőle
    for edge in graph_queries.edges(con):
        if edge.from_kind == "entity" and edge.to_kind == "entity":
            linked[edge.from_id].add(edge.to_id)
            linked[edge.to_id].add(edge.from_id)
            parents[edge.from_id].add(edge.to_id)
    found: dict[int, dict] = {}
    for page_id in sorted(pages):
        picked = chosen.get(page_id, [])
        main = next((c[0] for c in picked if c[1] == "main"), None)
        own = {c[0] for c in picked}

        def relation(entity_id: int, main: int | None = main) -> str:
            if main is None:
                return "no_main"
            if entity_id == main:
                return "main"
            if entity_id in linked[main] or parents[entity_id] & parents[main]:
                return "related"
            return "unrelated"

        dom_h1: list[tuple[str, str]] = []
        dom_levels: set[int] = set()
        rendered = crawl.rendered_dom(con, page_id)
        if rendered is not None:
            dom_h1, dom_levels = heading_scan(rendered)
        outside = [(where, text) for where, text in dom_h1 if where != "content"]
        outside_keys = {alias_key(text) for _, text in outside}
        roots: list[dict] = []
        stack: list[dict] = []
        sequence: list[dict] = []
        skipped_template = content_words = 0
        for block in sorted(blocks.get(page_id, []), key=lambda b: b.ordinal):
            if block.region != "content":
                own_header = (block.kind == "heading" and block.level == 1
                              and alias_key(block.text) not in outside_keys)
                if not own_header:             # chrome; a tartalom saját fejlécének H1-e marad
                    continue
            if block.kind != "heading":
                words = len(block.text.split())
                content_words += words
                if stack:
                    stack[-1]["words"] += words
                continue
            is_template = (block.level or 1) > 1 \
                and (block.level, alias_key(block.text)) in template
            skipped_template += is_template
            entities = [{"entity_id": e, "entity": names.get(e, ""), "relation": relation(e)}
                        for e in mentions.get(block.block_id, []) if e in names]
            relations = [e["relation"] for e in entities]
            node = {"block_id": block.block_id, "level": block.level or 1, "text": block.text,
                    "entities": entities,
                    "relation": next((r for r in (*RELATION_ORDER, "no_main") if r in relations),
                                     "no_entity"),
                    "words": 0, "total_words": 0, "children": [], "empty": False,
                    "skipped_level": False, "template": is_template, "media_only": False,
                    "parent_level": None,
                    "own": [e["entity_id"] for e in entities if e["entity_id"] in own]}
            while stack and stack[-1]["level"] >= node["level"]:
                stack.pop()
            if stack:
                node["parent_level"] = stack[-1]["level"]
                node["skipped_level"] = (not is_template
                                         and node["level"] > stack[-1]["level"] + 1)
                stack[-1]["children"].append(node)
            else:
                roots.append(node)
            stack.append(node)
            sequence.append(node)
        for index, node in enumerate(sequence[:-1]):
            follower = sequence[index + 1]
            node["empty"] = (not node["template"] and node["words"] == 0
                             and not node["children"] and follower["level"] <= node["level"])
        if rendered is not None and any(node["empty"] for node in sequence):
            _dom_sections(sequence, heading_gaps(rendered))
        for node in reversed(sequence):
            node["total_words"] = node["words"] + sum(c["total_words"] for c in node["children"])
        h1 = [node for node in sequence if node["level"] == 1]
        justified, reasons = _multiple_h1(h1)
        found[page_id] = {"tree": roots, "h1": h1, "outside_h1": outside,
                          "content_words": content_words,
                          "has_h1": bool(h1) or bool(dom_h1),
                          "has_h2": any(node["level"] == 2 for node in sequence)
                          or 2 in dom_levels,
                          "template": skipped_template, "h1_justified": justified,
                          "h1_reasons": reasons, "sequence": sequence}
    return found


def _dom_sections(sequence: list[dict], gaps: list[tuple]) -> None:
    """A blokkok szerint üres szakaszok ellenőrzése a DOM-ból (`dom.heading_gaps`): a fa
    headingjei sorrendben a DOM headingjeihez rendelve (azonos szint, azonos vagy egymást
    tartalmazó szövegkulcs; a fa a DOM headingjeinek részsorozata). Ha a heading és a fában
    utána álló heading között a DOM-ban szöveg vagy további heading áll, a szakasz nem üres;
    ha csak tartalmi elem, `media_only`; ha a heading nem található, marad a blokkok szerinti
    eredmény."""
    keys = [(level, alias_key(text)) for level, text, _, _ in gaps]
    matched: list[int | None] = []
    after = 0
    for node in sequence:
        key = alias_key(node["text"])
        hit = next((index for index in range(after, len(keys))
                    if keys[index][0] == node["level"] and (
                        keys[index][1] == key
                        or (key and keys[index][1] and (key in keys[index][1]
                                                        or keys[index][1] in key)))), None)
        matched.append(hit)
        if hit is not None:
            after = hit + 1
    for index, node in enumerate(sequence[:-1]):
        start = matched[index]
        if not node["empty"] or start is None:
            continue
        stop = matched[index + 1] if matched[index + 1] is not None else start + 1
        between = gaps[start:stop]
        if len(between) > 1 or between[0][2] > 0:      # közbeeső heading vagy szöveg
            node["empty"] = False
        elif between[0][3]:
            node["empty"], node["media_only"] = False, True


def _multiple_h1(h1: list[dict]) -> tuple[bool | None, list[str]]:
    """(indokolt-e a több H1, az indokolatlanság okai); (None, []) legfeljebb egy H1-nél."""
    if len(h1) < 2:
        return None, []
    reasons = []
    if any(not node["entities"] for node in h1):
        reasons.append("valamelyik H1-ben nincs entitás")
    named = [tuple(node["own"]) for node in h1 if node["entities"]]
    if any(not own for own in named):
        reasons.append("valamelyik H1 nem az oldal fő vagy másodlagos entitását nevezi meg")
    firsts = [own[0] for own in named if own]
    if len(set(firsts)) < len(firsts):
        reasons.append("a H1-ek ugyanarra az entitásra mutatnak")
    if any(node["empty"] or (node["total_words"] < SUBSTANTIVE_WORDS and not node["children"])
           for node in h1):
        reasons.append("valamelyik H1-szakasz üres vagy nincs alatta érdemi tartalom")
    return not reasons, reasons


def structure_findings(pages: Mapping[int, Mapping], headings: Mapping[int, Mapping],
                       role_labels: Mapping[str, str] | None = None) -> list[tuple]:
    """A szerkezeti megállapítások (típus, súlyosság, oldal, entitás, összefoglaló, bizonyíték)
    a canonical-duplikátum nélküli oldalakra; az azonos típusú megállapítás oldalcsoportonként
    egy tétel (egy oldalas csoportnál az oldalhoz kötve, különben az oldalak listájával). Az
    üres szakasz és a kihagyott szint oldalszerep és szintminta szerint egy tétel
    (`PATTERN_TYPES`; `role_labels`: a szerepek megnevezése az összefoglalóhoz)."""
    records: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for page_id, page in sorted(pages.items(), key=lambda item: item[1]["url"]):
        if page.get("canonical") is not None or page_id not in headings:
            continue
        data = headings[page_id]
        base = {"url": page["url"], "page_id": page_id}

        def add(kind: str, detail: dict, page: Mapping = page, base: dict = base) -> None:
            records[(kind, page["group"])].append({**base, **detail})

        if not data["has_h1"]:
            add("missing_h1", {"headings": len(data["sequence"])})
        if data["outside_h1"]:
            add("h1_outside_content", {
                "outside": [{"where": where, "text": text} for where, text in data["outside_h1"]],
                "content_h1": [node["text"] for node in data["h1"]]})
        if data["h1_justified"] is False:
            add("multiple_h1", {"h1": [node["text"] for node in data["h1"]],
                                "reasons": data["h1_reasons"]})
        empty = [node for node in data["sequence"] if node["empty"]]
        if empty:
            add("empty_section", {
                "headings": [f"H{node['level']}: {node['text']}" for node in empty],
                "pattern": sorted({f"H{node['level']}" for node in empty}),
                "role": page["role"]})
        skipped = [node for node in data["sequence"] if node["skipped_level"]]
        if skipped:
            add("skipped_level", {
                "headings": [f"H{node['level']}: {node['text']}" for node in skipped],
                "pattern": sorted({f"H{node['parent_level']}→H{node['level']}"
                                   for node in skipped}),
                "role": page["role"]})
        if data["content_words"] > LONG_CONTENT_WORDS and not data["has_h2"]:
            add("missing_h2", {"words": data["content_words"]})
    found = []
    merged: dict[tuple[str, str, tuple[str, ...]], list[dict]] = defaultdict(list)
    for (kind, _), members in sorted(records.items()):
        if kind in PATTERN_TYPES:
            pattern = tuple(sorted({item for member in members for item in member["pattern"]}))
            merged[(kind, members[0]["role"], pattern)] += members
    for (kind, role, pattern), members in sorted(merged.items()):
        shown = [{key: value for key, value in member.items()
                  if key in ("url", "headings")} for member in members]
        levels = ", ".join(pattern)
        if len(members) == 1:
            found.append((kind, SEVERITY[kind], members[0]["page_id"], None,
                          _summary(kind, members), {**shown[0], "pattern": list(pattern)}))
        else:
            label = (role_labels or {}).get(role, role)
            found.append((kind, SEVERITY[kind], None, None,
                          f"{TYPE_SUMMARY[kind]} ({levels}): {len(members)} {label}",
                          {"group": f"{kind} | {role} | {levels}", "role": role,
                           "pattern": list(pattern), "pages": shown}))
    for (kind, group), members in sorted(records.items()):
        if kind in PATTERN_TYPES:
            continue
        first = members[0]
        summary = _summary(kind, members)
        if len(members) == 1:
            evidence = {key: value for key, value in first.items() if key != "page_id"}
            found.append((kind, SEVERITY[kind], first["page_id"], None, summary, evidence))
        else:
            found.append((kind, SEVERITY[kind], None, None, summary, {
                "group": f"{kind} | {group}",
                "pages": [{key: value for key, value in member.items() if key != "page_id"}
                          for member in members]}))
    return found


def _summary(kind: str, members: list[dict]) -> str:
    first = members[0]
    count = f" ({len(members)} oldal)" if len(members) > 1 else ""
    if kind == "missing_h1":
        return f"nincs H1{count}"
    if kind == "h1_outside_content":
        where = sorted({OUTSIDE_LABELS[item["where"]] for member in members
                        for item in member["outside"]})
        return f"H1 a fő tartalmon kívül: {'; '.join(where)}{count}"
    if kind == "multiple_h1":
        return f"{len(first['h1'])} H1 a fő tartalomban: {'; '.join(first['reasons'])}{count}"
    if kind == "empty_section":
        return f"üres szakasz: {'; '.join(first['headings'][:3])}{count}"
    if kind == "skipped_level":
        return f"kihagyott heading-szint: {'; '.join(first['headings'][:3])}{count}"
    return f"{first['words']} szó H2 nélkül{count}"


def tree_rows(node: Mapping, depth: int = 0) -> list[dict]:
    """A fa sorai mélységi bejárásban (a CSV-nézethez)."""
    rows = [{"depth": depth, **{key: node[key] for key in (
        "level", "text", "entities", "relation", "words", "total_words", "empty",
        "media_only", "skipped_level", "template")}}]
    for child in node["children"]:
        rows += tree_rows(child, depth + 1)
    return rows
