"""SEO-megállapítások és nézetek az entitásgráfból (M3 spec, „M3 — Entitásgráf”, 4. és 7. pont),
LLM nélkül, a gráf tábláiból (`graph.build_graph` után). Minden futás újraépíti a `findings`
táblát.

Megállapítások (`build_findings`), mindegyik bizonyítékkal és súlyossággal (high / medium / low);
a canonical-duplikátum oldal egyikben sem szerepel:

- `h1_title_mismatch`: az erős megbízhatóságú fő entitással bíró oldalon a fő entitás neve vagy
  aliasa nincs a H1-ben, illetve a title-ben (`names_in`: szókezdettől, a kötőszótól, az
  írásjelektől és az egybe- vagy különírástól függetlenül is, pl. „&” = „and”, „E-Privacy” =
  „Eprivacy”; a név rövid alakja is egyezés, ha a H1 vagy a title egy szelete legalább két
  szó, és minden szava a név szava; a title-ben a hosszkorlát miatt levágott név eleje is
  egyezés). Névnek nem számít az az alias, amely az oldal saját szövege: maga az oldal (vagy a
  hreflang-párja) H1-e, vagy csak H1- és title-forrása van (`entity_aliases.source`); így az
  összevetés nem körkörös (az M2 az oldalhoz kötött entitás aliasai közé az oldal H1-ét és
  title-szeleteit is felveszi). Személynél a kéttagú név mindkét sorrendje név. Az oldalhoz
  nem kötött fő entitásnál a gráf H1- és title-bizonyítéka (az M2 említése) is egyezés. Ha a
  H1-ben a fő entitás nincs meg, és más entitás említése sincs benne, a H1 általános. A
  kezdőoldalon, és ahol a fő entitás a site saját entitása (rólunk, karrier), csak a title
  számít (a H1-be nem kell a márkanév). Ha a H1 megnevezi a fő entitást, és a title a H1
  szövegét tartalmazza, a title is megnevezi. A fülek (pl. `?tab=api`) nem ismétlik. Az
  azonos okú (ugyanazok a hibák) és azonos szerepű oldalak egy megállapítást adnak, az
  érintett oldalak listájával (pl. a H1 nélküli esettanulmányok). Súlyosság: high, ha a
  title-ből hiányzik, vagy nincs H1; medium, ha csak a H1-ből.
- `cannibalization` és `shared_topic`: ugyanaz az erős megbízhatóságú fő entitás legalább két
  oldalcsoportban, egy nyelven belül. Egy csoport a hreflang-pár és a fül nélküli URL
  (`page_nodes.group_key`). Nem számít: a canonical-duplikátum, a lapozó oldal (`PAGINATION`),
  a nem indexelhető oldal (`noindex`), a szülő–gyermek viszony (az egyik oldal útvonala a
  másiké alatt áll), és a site saját entitása (a cégről több oldal is szólhat). Kannibalizáció
  csak akkor, ha a fő entitáson túl két oldal másodlagos entitásai is átfednek, vagy a
  title-jük nagyon hasonló (`title_similarity` ≥ `TITLE_SIMILAR`: a title szavainak
  Jaccard-hasonlósága a site-nevet hordozó utolsó szelet nélkül); súlyosság: high, ha
  legalább `HIGH_GROUPS` oldal érintett, különben medium. Egyébként közös téma
  (`shared_topic`, low): az oldalak ugyanarról az entitásról szólnak, más-más szögből.
- `uncovered_topic` és `missing_page`: az entitás súlya a site felső tizedében van
  (`TOP_SHARE`), legalább `MIN_PAGES` oldalcsoport említi (a fülek, pl. `?tab=api`, és a
  hreflang-pár egy csoport), és egyik oldalnak sem fő entitása. További feltételek: a site
  maga kiemeli (legalább `MIN_STRUCTURAL`, `SMALL_SITE_GROUPS`-nál kevesebb oldalcsoportú
  site-on `SMALL_SITE_STRUCTURAL` oldalcsoportban áll title-ben, H1-ben vagy headingben, és
  összesen legalább `MIN_MENTIONS` említése van), és nincs lefedve: nem
  másodlagos entitása egy oldalnak sem, nem egy saját oldalú ajánlat fogalma (`offers` él), a
  neve nem egy oldal H1-e vagy URL-szakasza, és a nevének szavai nem egy fő entitás nevének
  szavai közül valók („Organic Growth” az „Organic Growth System” mellett; a szülő, pl. a
  termékcsalád a termékei mellett, ettől még nincs lefedve). Kimarad: a szervezet, a személy
  és a hely (`NO_PAGE_TYPES`), az API-szimbólum, a site saját entitása, az oldalhoz kötött
  entitás, és a kontextus-entitás (`is_context`): az említései legalább `CONTEXT_SHARE`
  részben sablon- vagy chrome-helyen állnak (title-sablon, menü, lábléc, oldalsáv), vagy a
  neve a site nevében szerepel (az Angular az „Angular Bootstrap” komponenstárban); a sok
  oldalon tárgyalt téma (pl. a GA4 egy mérési tanácsadó site-ján) nem kontextus. Ha egy
  hiányzó oldalú termékcsaládnak az alcsaládja is hiányzó oldal volna, az alcsalád a szülő
  megállapításában áll (`subfamilies`), nem külön. Nem lefedetlen téma az sem (`exclusion`),
  amit egy indexelhető oldal H1-e és title-je is megnevez (a szülő, pl. a termékcsalád
  kivételével; a rövid, más entitás nevében álló alias, pl. „AI”, nem megnevezés), és az
  egyszavas, kisbetűs köznévi fogalom („stratégia”, „reporting”). A termékcsalád (product / line, vagy termék, amelynek
  részei vannak) hiányzó oldal (`missing_page`: családoldal kell; high, ha legalább
  `HIGH_PAGES` oldalon szerepel, különben medium). Minden más lefedetlen téma
  (`uncovered_topic`): a teendő cikk, how-to vagy szakasz egy meglévő oldalon, nem
  feltétlenül új oldal; medium, ha legalább `HIGH_PAGES` oldalon szerepel, különben low. A
  további feltételek nélküli (szó szerinti) jelöltek száma: `FindingsRun.missing_literal`, a
  kontextus-entitások: `FindingsRun.context`.
- `unclear_topic`: az oldal fő entitása gyenge megbízhatóságú (medium), kivéve, ha a H1 és a
  title is megnevezi (ott a téma egyértelmű, csak más bizonyíték nincs); vagy az oldalnak
  egyetlen jelöltje sincs (high).

Nézetek (`export_views`), ellenőrzéshez: site-áttekintő (a legfontosabb entitások típus szerint,
a szülővel, a kategóriával és a fő oldalaikkal), entitás környezete (az entitás élei egy
lépésnyire, a fő és a csak említő oldalai), oldalnézet (a fő entitás a bizonyítékaival, a H1 és a
title összevetése, a további említett entitások, az oldal megállapításai); CSV-ben és egy
lenyitható HTML-oldalon (`<név>-views.html`).
"""
from __future__ import annotations

import csv
import html
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from urllib.parse import urlsplit

import duckdb

from aaa2.db.stable_json import dumps
from aaa2.entities.gate import occurs
from aaa2.entities.rules import alias_key
from aaa2.functions.graph import evidence_text
from aaa2.resolver.names import normal_key

TYPES = ("h1_title_mismatch", "cannibalization", "shared_topic", "missing_page",
         "uncovered_topic", "unclear_topic")
TYPE_LABELS = {"h1_title_mismatch": "H1/title-eltérés", "cannibalization": "Kannibalizáció",
               "shared_topic": "Közös téma", "missing_page": "Hiányzó oldal",
               "uncovered_topic": "Lefedetlen téma", "unclear_topic": "Nem egyértelmű téma"}
ROLE_LABELS = {"offer": "ajánlatoldal", "article": "cikkoldal", "product": "termékoldal",
               "component": "komponensoldal", "category": "kategóriaoldal",
               "profile": "profiloldal", "home": "kezdőoldal", "support": "egyéb oldal"}
ACTIONS = {"missing_page": "családoldal a termékcsaládnak",
           "uncovered_topic": "cikk, how-to vagy szakasz egy meglévő oldalon (topical "
                              "authority); nem feltétlenül új oldal"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
OWN_TEXT_SOURCES = frozenset({"h1", "title"})
PAGINATION = re.compile(r"(?:[?&](?:page|paged|p|infinite_page|oldal)=\d+)|(?:/page/\d+/?$)",
                        re.IGNORECASE)
NO_PAGE_TYPES = frozenset({"org", "person", "place"})
NO_PAGE_SUBTYPES = frozenset({"api_symbol"})
TOP_SHARE = 0.1
MIN_PAGES = 3
MIN_STRUCTURAL = 3
SMALL_SITE_GROUPS = 50                  # ennél kevesebb oldalcsoportú site-on …
SMALL_SITE_STRUCTURAL = 2               # … ennyi csoportban elég a kiemelés
# ilyen szerepű oldalon a H1 vagy a title önmagában is megnevezés (az oldal egy entitásé)
NAMING_ALONE_ROLES = ("offer", "product", "component", "category")
SHORT_ALIAS_CHARS = 3                   # ennél nem hosszabb alias gyenge megnevezés
MIN_MENTIONS = 6
HIGH_PAGES = 10
HIGH_GROUPS = 3
CONTEXT_SHARE = 0.5                     # az említések ekkora része sablon vagy chrome
TITLE_SIMILAR = 0.6
TITLE_SPLIT = re.compile(r"\s+[-–—|·:]\s+")
CONJUNCTIONS = frozenset({"and", "es"})
CUT_MIN_CHARS = 20                      # a levágott title-ben a névnek legalább ennyi jele áll
CUT_MIN_SHARE = 0.6
OVERVIEW_TOP = 100                      # a site-áttekintő ennyi legnagyobb súlyú entitást mutat
PAGE_MENTIONS = 10                      # az oldalnézet ennyi további említett entitást sorol
EVIDENCE_PAGES = 5


@dataclass
class FindingsRun:
    counts: Counter = field(default_factory=Counter)          # (típus, súlyosság) → darab
    missing_literal: int = 0             # lefedetlen-jelöltek a kiemelés és lefedés nélkül
    context: list[str] = field(default_factory=list)          # kizárt kontextus-entitások

    def by_type(self) -> dict[str, int]:
        found: Counter = Counter()
        for (kind, _), count in self.counts.items():
            found[kind] += count
        return dict(found)


# ---------------------------------------------------------------------------
# megállapítások
# ---------------------------------------------------------------------------


def build_findings(con: duckdb.DuckDBPyConnection) -> FindingsRun:
    """A `findings` tábla újraépítése a gráf tábláiból (lásd a modul leírását)."""
    con.execute("DELETE FROM findings")
    site = _Site(con)
    run = FindingsRun()
    rows = [*_mismatches(site), *_shared_topics(site), *_uncovered(site, run),
            *_unclear_topics(site)]
    rows.sort(key=lambda r: (TYPES.index(r[0]), SEVERITY_ORDER[r[1]], r[4]))
    for number, (kind, severity, page_id, entity_id, summary, evidence) in enumerate(rows, 1):
        con.execute("INSERT INTO findings (finding_id, type, severity, page_id, entity_id, "
                    "summary, evidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [number, kind, severity, page_id, entity_id, summary,
                     dumps(evidence, ensure_ascii=False)])
        run.counts[(kind, severity)] += 1
    return run


class _Site:
    """A gráf táblái a megállapításokhoz és a nézetekhez."""

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con
        self.pages = {row[0]: dict(zip(
            ("page_id", "url", "role", "support", "title", "h1", "lang", "group", "status",
             "canonical", "issue", "noindex"), row, strict=True)) for row in con.execute(
            "SELECT n.page_id, n.url, n.role, n.support_kind, n.title, n.h1, n.lang, "
            "n.group_key, n.main_status, n.canonical_page, n.canonical_issue, "
            "coalesce(p.noindex, false) FROM page_nodes n JOIN pages p USING (page_id) "
            "ORDER BY n.url").fetchall()}
        self.entities = {row[0]: dict(zip(
            ("entity_id", "name", "type", "subtype", "aliases", "anchor", "role", "source"),
            row, strict=True)) for row in con.execute(
            "SELECT entity_id, name, type, subtype, aliases, anchor_page_id, role, source "
            "FROM entities ORDER BY ALL").fetchall()}
        self.alias_sources: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for entity_id, alias, source in con.execute(
                "SELECT entity_id, alias, source FROM entity_aliases ORDER BY ALL").fetchall():
            self.alias_sources[entity_id][alias].add(source)
        self.chosen: dict[int, list[tuple]] = defaultdict(list)     # oldal → (entitás, szerep, …)
        for page_id, entity_id, role, confidence, evidence in con.execute(
                "SELECT page_id, entity_id, role, confidence, evidence FROM page_main_entity "
                "ORDER BY page_id, rank").fetchall():
            self.chosen[page_id].append((entity_id, role, confidence, json.loads(evidence)))
        self.weights = {row[0]: dict(zip(
            ("entity_id", "pages", "mentions", "structural", "main_pages", "secondary_pages",
             "content_anchors", "nav_anchors", "weight"), row, strict=True))
            for row in con.execute(
                "SELECT entity_id, pages, mentions, structural, main_pages, secondary_pages, "
                "content_anchors, nav_anchors, weight FROM entity_weights ORDER BY ALL").fetchall()}
        ranked = sorted(self.weights.values(), key=lambda w: (-w["weight"], w["entity_id"]))
        self.rank = {w["entity_id"]: i for i, w in enumerate(ranked, start=1)}
        self.site_entities = {entity_id for (entity_id,) in con.execute(
            "SELECT entity_id FROM entities e WHERE role = 'brand' OR (type = 'brand' AND "
            "source IN ('rule', 'schema') AND NOT EXISTS (SELECT 1 FROM entity_relations r "
            "WHERE r.from_id = e.entity_id AND r.type = 'brand_of')) ORDER BY ALL").fetchall()}
        self.mention_edges: dict[int, list[tuple[int, float, dict]]] = defaultdict(list)
        for page_id, entity_id, weight, evidence in con.execute(
                "SELECT from_id, to_id, weight, evidence FROM edges WHERE type = 'mentions' "
                "ORDER BY from_id, weight DESC, to_id").fetchall():
            self.mention_edges[page_id].append((entity_id, weight or 0.0, json.loads(evidence)))

    def name(self, entity_id: int) -> str:
        return self.entities[entity_id]["name"]

    def kind(self, entity_id: int) -> str:
        entity = self.entities[entity_id]
        return "/".join(filter(None, (entity["type"], entity["subtype"])))

    def main(self, page_id: int) -> tuple | None:
        return next((c for c in self.chosen.get(page_id, []) if c[1] == "main"), None)

    def nodes(self) -> list[dict]:
        """Az oldalak a canonical-duplikátumok nélkül."""
        return [p for p in self.pages.values() if p["canonical"] is None]

    def group_h1(self, page: dict) -> set[str]:
        return {alias_key(p["h1"]) for p in self.pages.values()
                if p["group"] == page["group"] and p["h1"]} - {""}

    def named(self, page: dict, main: tuple) -> tuple[bool, bool, list[str]]:
        """(a H1 megnevezi-e a fő entitást, a title megnevezi-e, az elfogadott megnevezések);
        lásd a modul leírását."""
        forms = self.naming_forms(main[0], page)
        mentioned = self.entities[main[0]]["anchor"] is None
        in_h1 = names_in(forms, page["h1"]) or (mentioned and "h1" in main[3])
        in_title = names_in(forms, page["title"], cut=True) \
            or (mentioned and "title" in main[3]) \
            or (in_h1 and bool(page["h1"]) and names_in([page["h1"]], page["title"], cut=True))
        return in_h1, in_title, forms

    def naming_forms(self, entity_id: int, page: dict) -> list[str]:
        """Az entitás megnevezései az oldal H1- és title-összevetéséhez: a név és az aliasok,
        az oldal saját szövegeiből lett aliasok nélkül (lásd a modul leírását)."""
        entity = self.entities[entity_id]
        own = self.group_h1(page)
        sources = self.alias_sources.get(entity_id, {})
        forms = [entity["name"]]
        for alias in dict.fromkeys([*(entity["aliases"] or []), *sources]):
            own_text = alias_key(alias) in own \
                or bool(sources.get(alias)) and sources[alias] <= OWN_TEXT_SOURCES
            if own_text and alias_key(alias) != alias_key(entity["name"]):
                continue
            forms.append(alias)
        if entity["type"] == "person":
            forms += [" ".join(reversed(f.split())) for f in list(forms) if len(f.split()) == 2]
        return [f for f in dict.fromkeys(forms) if f and alias_key(f)]


def names_in(forms: list[str], text: str | None, cut: bool = False) -> bool:
    """Valamelyik megnevezés áll-e a szövegben: szókezdettől (`gate.occurs`); a betűi egymás
    után, szóhatártól szóhatárig, az írásjelektől, a kötőszótól és az egybeírástól függetlenül
    (`_tokens`: „Tracking and Measurement” a „Tracking & Measurement”-ben, „Eprivacy” az
    „E-Privacy”-ben, de a „Mérés” nem a „Kimérés”-ben); a szöveg egy szelete (a title a
    `TITLE_SPLIT` mentén) a név rövid alakja: legalább két szó, mind a név szava; vagy `cut`
    esetén (title) a szöveg a megnevezés legalább `CUT_MIN_CHARS` jeles, a hosszának
    `CUT_MIN_SHARE` részét kitevő elejével kezdődik (a hosszkorlát miatt levágott név)."""
    if not text:
        return False
    words, plain = _tokens(text), _squash(text)
    joined = "".join(words)
    starts, ends, at = set(), set(), 0
    for word in words:
        starts.add(at)
        at += len(word)
        ends.add(at)
    pieces = [set(_tokens(piece)) for piece in TITLE_SPLIT.split(text)]
    for form in forms:
        name_words = _tokens(form)
        name = "".join(name_words)
        if occurs(form, text) or (name and any(
                joined.startswith(name, start) and start + len(name) in ends
                for start in starts)):
            return True
        if any(len(piece) >= 2 and piece <= set(name_words) for piece in pieces):
            return True
        if cut:
            name, shared = _squash(form), 0
            while shared < min(len(name), len(plain)) and name[shared] == plain[shared]:
                shared += 1
            if shared >= CUT_MIN_CHARS and shared >= CUT_MIN_SHARE * len(name):
                return True
    return False


def _tokens(text: str) -> list[str]:
    """A kulcs szavai az írásjelek és a kötőszó (és, and, &) nélkül."""
    return [w for w in re.split(r"[\W_]+", alias_key(text)) if w and w not in CONJUNCTIONS]


def _squash(text: str) -> str:
    """A kulcs betűi és számjegyei, elválasztók és írásjelek nélkül."""
    return re.sub(r"[\W_]+", "", alias_key(text))


def _mismatches(site: _Site) -> list[tuple]:
    records = []
    seen: set[tuple] = set()
    for page in site.nodes():
        main = site.main(page["page_id"])
        if page["status"] != "main" or main is None or main[2] != "strong":
            continue
        entity_id = main[0]
        key = (page["group"], entity_id, page["h1"], page["title"])
        if key in seen:
            continue
        seen.add(key)
        in_h1, in_title, forms = site.named(page, main)
        home = page["role"] == "home" or entity_id in site.site_entities
        h1_missing = not in_h1 and not home
        if not h1_missing and in_title:
            continue
        others = sorted({site.name(e) for e, _, counts in site.mention_edges[page["page_id"]]
                         if e != entity_id and counts.get("h1")})
        problems = [text for flag, text in (
            (not page["h1"] and not home, "nincs H1"),
            (bool(page["h1"]) and h1_missing and not others,
             "a H1 általános: nincs benne entitás"),
            (bool(page["h1"]) and h1_missing and bool(others), "a fő entitás nincs a H1-ben"),
            (not in_title, "a fő entitás nincs a title-ben")) if flag]
        severity = "high" if not in_title or (not page["h1"] and not home) else "medium"
        records.append((severity, page, entity_id,
                        {"url": page["url"], "main_entity": site.name(entity_id),
                         "confidence": main[2], "h1": page["h1"], "title": page["title"],
                         "in_h1": in_h1, "in_title": in_title, "h1_entities": others,
                         "names": forms[:8], "problems": problems}))
    groups: dict[tuple, list[tuple]] = defaultdict(list)
    for record in records:
        groups[(tuple(record[3]["problems"]), record[1]["role"])].append(record)
    found = []
    for (problems, role), members in sorted(groups.items()):
        severity, page, entity_id, evidence = members[0]
        if len(members) == 1:
            found.append(("h1_title_mismatch", severity, page["page_id"], entity_id,
                          f"{site.name(entity_id)}: {'; '.join(problems)}", evidence))
            continue
        label = f"{'; '.join(problems)} | {role}"
        found.append(("h1_title_mismatch", severity, None, None,
                      (f"{'; '.join(problems)}: {len(members)} "
                      f"{ROLE_LABELS.get(role, role)}"),
                      {"group": label, "role": role, "problems": list(problems),
                       "pages": [m[3] for m in sorted(members, key=lambda m: m[1]["url"])]}))
    return found


def _parent_child(a: str, b: str) -> bool:
    """Az egyik URL útvonala a másiké alatt áll (szülő–gyermek)."""
    first, second = (urlsplit(u).path.rstrip("/") + "/" for u in (a, b))
    return first != second and (first.startswith(second) or second.startswith(first))


def title_similarity(first: str | None, second: str | None) -> float:
    """A két title szavainak Jaccard-hasonlósága, az utolsó (a site nevét hordozó) szelet
    nélkül, ha a title több szeletből áll (`TITLE_SPLIT`)."""
    sets = []
    for title in (first, second):
        pieces = TITLE_SPLIT.split(title or "")
        sets.append(set(_tokens(" ".join(pieces[:-1] if len(pieces) > 1 else pieces))))
    union = sets[0] | sets[1]
    return len(sets[0] & sets[1]) / len(union) if union else 0.0


def _shared_topics(site: _Site) -> list[tuple]:
    by_entity: dict[tuple[int, str | None], dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list))
    for page in site.nodes():
        main = site.main(page["page_id"])
        if page["status"] != "main" or main is None or main[2] != "strong" \
                or page["noindex"] or PAGINATION.search(page["url"]) \
                or main[0] in site.site_entities:
            continue
        by_entity[(main[0], page["lang"])][page["group"]].append({**page, "confidence": main[2]})
    found = []
    for (entity_id, lang), groups in sorted(by_entity.items(),
                                            key=lambda item: (item[0][0], item[0][1] or "")):
        reps = [min(members, key=lambda p: p["url"]) for _, members in sorted(groups.items())]
        kept = [p for p in reps if not any(
            _parent_child(p["url"], other["url"]) and len(urlsplit(other["url"]).path)
            < len(urlsplit(p["url"]).path) for other in reps)]
        if len(kept) < 2:
            continue
        kept.sort(key=lambda p: p["url"])
        secondary = {p["page_id"]: {c[0] for c in site.chosen.get(p["page_id"], [])
                                    if c[1] == "secondary"} for p in kept}
        overlaps = []
        for first, second in combinations(kept, 2):
            common = sorted(site.name(e) for e in
                            secondary[first["page_id"]] & secondary[second["page_id"]])
            similarity = round(title_similarity(first["title"], second["title"]), 2)
            if common or similarity >= TITLE_SIMILAR:
                overlaps.append({"pages": [first["url"], second["url"]], "secondary": common,
                                 "title_similarity": similarity})
        involved = {url for overlap in overlaps for url in overlap["pages"]}
        tail = f" ({lang})" if lang else ""
        evidence = {"main_entity": site.name(entity_id), "lang": lang, "overlaps": overlaps,
                    "pages": [{"url": p["url"], "role": p["role"], "h1": p["h1"],
                               "title": p["title"], "confidence": p["confidence"],
                               "secondary": sorted(site.name(e)
                                                   for e in secondary[p["page_id"]])}
                              for p in kept]}
        if overlaps:
            found.append(("cannibalization",
                          "high" if len(involved) >= HIGH_GROUPS else "medium", None, entity_id,
                          (f"{site.name(entity_id)}: {len(involved)} oldal fő entitása, átfedő "
                          f"másodlagos entitással vagy hasonló title-lel{tail}"), evidence))
        else:
            found.append(("shared_topic", "low", None, entity_id,
                          (f"{site.name(entity_id)}: {len(kept)} oldal közös témája, átfedés "
                          f"nélkül{tail}"), evidence))
    return found


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^\w]+", alias_key(text)) if w}


def uncovered_candidates(site: _Site, run: FindingsRun | None = None,
                         min_structural: int | None = None) -> list[dict]:
    """A lefedetlen téma jelöltjei a kizárások (`exclusion`) előtt: a súly, az oldalszám, a
    típus, a kiemelés (`min_structural` oldalcsoportban title, H1 vagy heading; alapból
    `MIN_STRUCTURAL`, `SMALL_SITE_GROUPS`-nál kevesebb oldalcsoportú site-on
    `SMALL_SITE_STRUCTURAL`) és a lefedettség feltételein átment entitások, a mérőszámaikkal
    (`template_share`: az említések hányad része áll sablon- vagy chrome-helyen;
    `in_site_name`: a név szavai a site nevének szavai; `headline`: az első indexelhető oldal,
    amelynek a H1-e és a title-je is megnevezi, `NAMING_ALONE_ROLES` szerepű oldalon elég az
    egyik; `common_word`: egyszavas, kisbetűs köznévi
    fogalom; `page_share`: az indexelhető oldalak hányad részén szerepel)."""
    if not site.weights:
        return []
    if min_structural is None:
        small = len({p["group"] for p in site.nodes()}) < SMALL_SITE_GROUPS
        min_structural = SMALL_SITE_STRUCTURAL if small else MIN_STRUCTURAL
    indexable = {p["page_id"] for p in site.nodes() if not p["noindex"]}
    headlines = [(p["url"], p["h1"], p["title"], p["role"] in NAMING_ALONE_ROLES)
                 for p in site.nodes() if not p["noindex"]]
    name_words: Counter = Counter()                  # szó → hány entitás nevében áll
    for other in site.entities.values():
        name_words.update(_words(other["name"]))
    on_pages: Counter = Counter()
    groups: dict[int, set[str]] = defaultdict(set)          # entitás → említő oldalcsoportok
    headed: dict[int, set[str]] = defaultdict(set)          # … ahol title, H1 vagy heading
    for page_id, edges in site.mention_edges.items():
        page = site.pages.get(page_id)
        if page is None or page["canonical"] is not None:
            continue
        for mentioned, edge_weight, counts in edges:
            if edge_weight <= 0:
                continue
            on_pages[mentioned] += page_id in indexable
            groups[mentioned].add(page["group"])
            if any(counts.get(position) for position in ("title", "h1", "heading")):
                headed[mentioned].add(page["group"])
    placed = {entity_id: (total, fixed) for entity_id, total, fixed in site.con.execute(
        "SELECT pe.entity_id, count(*), count(*) FILTER (WHERE b.region = 'chrome' "
        "OR list_contains(coalesce(pe.flags, []), 'template')) FROM page_entities pe "
        "LEFT JOIN blocks b USING (block_id) JOIN page_nodes n ON n.page_id = pe.page_id "
        "WHERE n.canonical_page IS NULL GROUP BY 1 ORDER BY ALL").fetchall()}
    site_words = [_words(form) for e in site.site_entities if e in site.entities
                  for form in [site.name(e), *(site.entities[e]["aliases"] or [])]]
    top = max(1, math.ceil(len(site.weights) * TOP_SHARE))
    page_keys = {normal_key(p["h1"]) for p in site.pages.values() if p["h1"]}
    page_keys |= {normal_key(segment) for p in site.pages.values()
                  for segment in urlsplit(p["url"]).path.split("/") if segment}
    main_words = [_words(site.name(main[0])) for page in site.nodes()
                  if (main := site.main(page["page_id"])) is not None
                  and main[0] not in site.site_entities]
    offered = {to_id for (to_id,) in site.con.execute(
        "SELECT e.to_id FROM edges e JOIN entities s ON s.entity_id = e.from_id "
        "WHERE e.type = 'offers' AND s.anchor_page_id IS NOT NULL ORDER BY ALL").fetchall()}
    secondary_of = {c[0] for chosen in site.chosen.values() for c in chosen
                    if c[1] == "secondary"}
    parents = {to_id for (to_id,) in site.con.execute(
        "SELECT to_id FROM edges WHERE type = 'part_of' ORDER BY ALL").fetchall()}
    found = []
    for entity_id, weight in site.weights.items():
        entity = site.entities[entity_id]
        if site.rank[entity_id] > top or len(groups[entity_id]) < MIN_PAGES \
                or weight["main_pages"] \
                or entity["type"] in NO_PAGE_TYPES or entity["subtype"] in NO_PAGE_SUBTYPES \
                or entity_id in site.site_entities or entity["anchor"] is not None:
            continue
        if run is not None:
            run.missing_literal += 1
        forms = [entity["name"], *(entity["aliases"] or [])]
        if len(headed[entity_id]) < min_structural or weight["mentions"] < MIN_MENTIONS \
                or entity_id in secondary_of \
                or entity_id in offered or {normal_key(f) for f in forms} & page_keys \
                or (entity_id not in parents
                    and any(_words(entity["name"]) <= words for words in main_words)):
            continue
        total, fixed = placed.get(entity_id, (0, 0))
        name = entity["name"].strip()
        own_words = _words(name)
        naming = [f for f in forms if alias_key(f) == alias_key(name) or not (
            len(_squash(f)) <= SHORT_ALIAS_CHARS
            and any(name_words[w] > (w in own_words) for w in _words(f)))]
        found.append({
            "entity_id": entity_id, "weight": weight, "parent": entity_id in parents,
            "headline": next((url for url, h1, title, alone in headlines
                              if (any if alone else all)((
                                  names_in(naming, h1), names_in(naming, title, cut=True)))),
                             None),
            "common_word": entity["type"] == "concept" and name.isalpha()
            and name == name.lower(),
            "family": entity["type"] == "product" and (entity["subtype"] == "line"
                                                       or entity_id in parents),
            "page_share": on_pages[entity_id] / len(indexable) if indexable else 0.0,
            "template_share": fixed / total if total else 0.0,
            "in_site_name": any(_words(entity["name"]) <= words for words in site_words),
            "page_groups": len(groups[entity_id]), "structural": len(headed[entity_id])})
    return found


def is_context(candidate: dict) -> bool:
    """Kontextus-entitás: az említései legalább `CONTEXT_SHARE` részben sablon- vagy
    chrome-helyen állnak, vagy a neve a site nevében szerepel."""
    return candidate["template_share"] >= CONTEXT_SHARE or candidate["in_site_name"]


def exclusion(candidate: dict) -> str | None:
    """Miért nem lefedetlen téma a jelölt (None: az): `context` (`is_context`); `headline`: egy
    indexelhető oldal H1-e és title-je is megnevezi (van róla szóló oldal; a csak az egyikben
    álló említés mellékes, kivéve a `NAMING_ALONE_ROLES` szerepű oldalt, pl. az ajánlatoldalt,
    ahol a title vagy a H1 önmagában is megnevezés; a legfeljebb `SHORT_ALIAS_CHARS` jelű alias, amely egy másik entitás
    nevének szava, pl. az „AI” az „AI Search” mellett, nem megnevezés; a szülőre, pl. a
    termékcsaládra nem vonatkozik, mert a termékei címében mindig ott áll); `common_word`:
    egyszavas, kisbetűs köznévi fogalom (nem rövidítés, nem tulajdonnév)."""
    if is_context(candidate):
        return "context"
    if candidate["headline"] is not None and not candidate["parent"]:
        return "headline"
    if candidate["common_word"]:
        return "common_word"
    return None


def _uncovered(site: _Site, run: FindingsRun) -> list[tuple]:
    kept = []
    for candidate in uncovered_candidates(site, run):
        reason = exclusion(candidate)
        if reason == "context":
            run.context.append(site.name(candidate["entity_id"]))
        elif reason is None:
            kept.append(candidate)
    parent_of = {from_id: to_id for from_id, to_id in site.con.execute(
        "SELECT from_id, to_id FROM edges WHERE type = 'part_of' AND from_kind = 'entity' "
        "AND to_kind = 'entity' ORDER BY ALL").fetchall()}
    families = {c["entity_id"] for c in kept if c["family"]}
    under: dict[int, list[int]] = defaultdict(list)          # hiányzó szülő → alcsaládjai
    for entity_id in sorted(families):
        seen, parent = {entity_id}, parent_of.get(entity_id)
        top = None
        while parent is not None and parent not in seen:
            if parent in families:
                top = parent
            seen.add(parent)
            parent = parent_of.get(parent)
        if top is not None:
            under[top].append(entity_id)
    nested = {child for children in under.values() for child in children}
    found = []
    for candidate in kept:
        entity_id, weight = candidate["entity_id"], candidate["weight"]
        if entity_id in nested:
            continue
        entity = site.entities[entity_id]
        family = candidate["family"]
        kind = "missing_page" if family else "uncovered_topic"
        pages = [(site.pages[p]["url"], w, counts) for p, edges in site.mention_edges.items()
                 if p in site.pages and site.pages[p]["canonical"] is None
                 for e, w, counts in edges if e == entity_id and w > 0]
        pages.sort(key=lambda item: (-item[1], item[0]))
        subfamilies = [{"entity": site.name(child), "page_count": site.weights[child]["pages"]}
                       for child in sorted(under.get(entity_id, []), key=site.name)]
        many = weight["pages"] >= HIGH_PAGES
        severity = ("high" if many else "medium") if family else ("medium" if many else "low")
        tail = f"; alcsaládjai: {', '.join(c['entity'] for c in subfamilies)}" \
            if subfamilies else ""
        found.append((kind, severity, None, entity_id,
                      (f"{entity['name']} ({site.kind(entity_id)}): {weight['pages']} oldalon "
                       f"szerepel, egyiknek sem fő entitása{tail}"),
                      {"entity": entity["name"], "type": site.kind(entity_id),
                       "action": ACTIONS[kind], "page_share": round(candidate["page_share"], 2),
                       "template_share": round(candidate["template_share"], 2),
                       "weight": weight["weight"], "rank": site.rank[entity_id],
                       "ranked": len(site.weights), "page_count": weight["pages"],
                       "mentions": weight["mentions"], "structural": candidate["structural"],
                       "page_groups": candidate["page_groups"], "subfamilies": subfamilies,
                       "top_pages": [{"url": url, "mention_weight": w, "positions": counts}
                                     for url, w, counts in pages[:EVIDENCE_PAGES]]}))
    return found


def _unclear_topics(site: _Site) -> list[tuple]:
    found = []
    for page in site.nodes():
        main = site.main(page["page_id"])
        if page["status"] == "none":
            found.append(("unclear_topic", "high", page["page_id"], None,
                          "az oldalnak nincs fő entitás-jelöltje",
                          {"url": page["url"], "h1": page["h1"], "title": page["title"]}))
        elif page["status"] == "main" and main is not None and main[2] == "weak" \
                and not {"h1", "title"} <= set(main[3]):
            found.append(("unclear_topic", "medium", page["page_id"], main[0],
                          (f"gyenge fő entitás: {site.name(main[0])} "
                           f"({evidence_text(main[3])})"),
                          {"url": page["url"], "h1": page["h1"], "title": page["title"],
                           "main_entity": site.name(main[0]), "evidence": main[3]}))
    return found


# ---------------------------------------------------------------------------
# kivonatok és nézetek
# ---------------------------------------------------------------------------


def _findings(con: duckdb.DuckDBPyConnection) -> list[dict]:
    return [dict(zip(("id", "type", "severity", "page_id", "entity_id", "summary", "evidence"),
                     (*row[:6], json.loads(row[6])), strict=True)) for row in con.execute(
        "SELECT finding_id, type, severity, page_id, entity_id, summary, evidence FROM findings "
        "ORDER BY finding_id").fetchall()]


def _affected(finding: dict) -> list[str]:
    """A megállapítás érintett oldalai (a lefedetlen témánál a legtöbbet említők)."""
    evidence = finding["evidence"]
    if "url" in evidence:
        return [evidence["url"]]
    return [p["url"] for p in evidence.get("pages") or evidence.get("top_pages") or []]


def export_findings(con: duckdb.DuckDBPyConnection, out: Path, name: str) -> Path:
    """A megállapítások: `<név>-findings.csv` (típus, súlyosság, entitás, érintett oldalak,
    összefoglaló, bizonyíték)."""
    site = _Site(con)
    rows = [{"azonosító": f["id"], "típus": TYPE_LABELS[f["type"]], "súlyosság": f["severity"],
             "entitás": site.name(f["entity_id"]) if f["entity_id"] is not None else "",
             "oldalak": " | ".join(_affected(f)), "összefoglaló": f["summary"],
             "bizonyíték": dumps(f["evidence"], ensure_ascii=False)}
            for f in _findings(con)]
    return _write(out / f"{name}-findings.csv", rows,
                  ["azonosító", "típus", "súlyosság", "entitás", "oldalak", "összefoglaló",
                   "bizonyíték"])


def _relations(site: _Site) -> dict[int, list[tuple[str, str, int]]]:
    """entitás → (éltípus, irány, a másik entitás) az entitás–entitás élekből."""
    found: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
    for from_id, to_id, kind in site.con.execute(
            "SELECT from_id, to_id, type FROM edges WHERE from_kind = 'entity' "
            "AND to_kind = 'entity' ORDER BY type, from_id, to_id").fetchall():
        if from_id in site.entities and to_id in site.entities:
            found[from_id].append((kind, "→", to_id))
            found[to_id].append((kind, "←", from_id))
    return found


def _overview_ids(site: _Site) -> list[int]:
    """A site-áttekintő entitásai: a legnagyobb súlyúak és mindegyik, amelyik fő entitás."""
    ranked = sorted(site.weights, key=lambda e: site.rank[e])
    return [e for e in ranked if site.rank[e] <= OVERVIEW_TOP or site.weights[e]["main_pages"]]


def _views(site: _Site) -> tuple[list[dict], list[dict], list[dict]]:
    relations = _relations(site)
    main_pages: dict[int, list[str]] = defaultdict(list)
    for page in site.nodes():
        main = site.main(page["page_id"])
        if main is not None:
            main_pages[main[0]].append(page["url"])
    mention_pages: dict[int, list[str]] = defaultdict(list)
    for page_id, edges in site.mention_edges.items():
        if page_id in site.pages and site.pages[page_id]["canonical"] is None:
            for entity_id, weight, _ in edges:
                if weight > 0:
                    mention_pages[entity_id].append(site.pages[page_id]["url"])
    findings = _findings(site.con)
    by_url: dict[str, list[str]] = defaultdict(list)
    for finding in findings:
        label = f"{TYPE_LABELS[finding['type']]} ({finding['severity']}): {finding['summary']}"
        if finding["type"] not in ("missing_page", "uncovered_topic"):
            for url in _affected(finding):
                by_url[url].append(label)

    def related(entity_id: int, kind: str, direction: str) -> str:
        return "; ".join(site.name(o) for k, d, o in relations.get(entity_id, [])
                         if k == kind and d == direction)

    overview, neighbourhood = [], []
    for entity_id in _overview_ids(site):
        weight = site.weights[entity_id]
        entity = site.entities[entity_id]
        overview.append({
            "típus": entity["type"], "altípus": entity["subtype"] or "",
            "entitás": entity["name"], "rang": site.rank[entity_id], "súly": weight["weight"],
            "szülő": related(entity_id, "part_of", "→"),
            "kategória": related(entity_id, "is_a", "→"),
            "márka": related(entity_id, "brand_of", "←"),
            "oldalak": weight["pages"], "említések": weight["mentions"],
            "fő oldalak": " | ".join(sorted(main_pages[entity_id]))})
        only = sorted(set(mention_pages[entity_id]) - set(main_pages[entity_id]))
        neighbourhood.append({
            "entitás": entity["name"], "típus": site.kind(entity_id),
            "rang": site.rank[entity_id], "súly": weight["weight"],
            "élek": "; ".join(f"{k} {d} {site.name(o)}"
                              for k, d, o in relations.get(entity_id, [])),
            "fő oldalak": " | ".join(sorted(main_pages[entity_id])),
            "csak említő oldalak száma": len(only), "csak említő oldalak": " | ".join(only)})
    overview.sort(key=lambda r: (r["típus"], r["rang"]))
    pages = []
    for page in sorted(site.pages.values(), key=lambda p: p["url"]):
        chosen = site.chosen.get(page["page_id"], [])
        main = next((c for c in chosen if c[1] == "main"), None)
        duplicate = page["canonical"] is not None
        in_h1, in_title, _ = site.named(page, main) if main else (False, False, [])
        picked = {c[0] for c in chosen}
        others = [f"{site.name(e)} ({w:g})" for e, w, _ in site.mention_edges[page["page_id"]]
                  if e not in picked and w > 0][:PAGE_MENTIONS]
        pages.append({
            "url": page["url"], "szerep": page["role"], "segédoldal": page["support"] or "",
            "nyelv": page["lang"] or "",
            "canonical": f"duplikátum: {site.pages[page['canonical']]['url']}" if duplicate
            else page["issue"] or "",
            "fő entitás": site.name(main[0]) if main else "",
            "típus": site.kind(main[0]) if main else "",
            "megbízhatóság": main[2] if main else "",
            "bizonyítékok": evidence_text(main[3]) if main else "",
            "másodlagos": "; ".join(site.name(c[0]) for c in chosen if c[1] == "secondary"),
            "H1": page["h1"] or "", "a H1-ben": _yes(main, in_h1),
            "title": page["title"] or "", "a title-ben": _yes(main, in_title),
            "további említett entitások": "; ".join(others),
            "megállapítások": "" if duplicate else " || ".join(by_url[page["url"]])})
    return overview, neighbourhood, pages


def _yes(main: tuple | None, named: bool) -> str:
    return "" if main is None else "igen" if named else "nem"


def export_views(con: duckdb.DuckDBPyConnection, out: Path, name: str) -> dict[str, Path]:
    """A három nézet CSV-ben (`<név>-view-site.csv`, `-view-entities.csv`, `-view-pages.csv`) és
    egy lenyitható HTML-oldalon (`<név>-views.html`) a megállapításokkal együtt."""
    out.mkdir(parents=True, exist_ok=True)
    site = _Site(con)
    overview, neighbourhood, pages = _views(site)
    paths = {
        "site": _write(out / f"{name}-view-site.csv", overview, [
            "típus", "altípus", "entitás", "rang", "súly", "szülő", "kategória", "márka",
            "oldalak", "említések", "fő oldalak"]),
        "entities": _write(out / f"{name}-view-entities.csv", neighbourhood, [
            "entitás", "típus", "rang", "súly", "élek", "fő oldalak",
            "csak említő oldalak száma", "csak említő oldalak"]),
        "pages": _write(out / f"{name}-view-pages.csv", pages, [
            "url", "szerep", "segédoldal", "nyelv", "canonical", "fő entitás", "típus",
            "megbízhatóság", "bizonyítékok", "másodlagos", "H1", "a H1-ben", "title",
            "a title-ben", "további említett entitások", "megállapítások"])}
    paths["html"] = out / f"{name}-views.html"
    paths["html"].write_text(_html(name, _findings(con), site, overview, neighbourhood, pages),
                             encoding="utf-8")
    return paths


def _write(path: Path, rows: list[dict], columns: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


STYLE = """
body{font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px;color:#1c1c1c;
background:#fff}
h1{font-size:1.4em}h2{font-size:1.15em;margin-top:2em;border-bottom:1px solid #ccc}
details{border:1px solid #ddd;border-radius:6px;margin:6px 0;padding:6px 10px}
details details{margin-left:8px}summary{cursor:pointer;font-weight:600}
summary span{font-weight:400;color:#555}
table{border-collapse:collapse;width:100%;margin:6px 0}
td,th{border-top:1px solid #eee;padding:3px 6px;text-align:left;vertical-align:top}
th{width:14em;color:#555;font-weight:400}
.wrap{overflow-x:auto}.high{color:#a40000}.medium{color:#8a5a00}.low{color:#555}
a{color:#0b57d0;word-break:break-all}
@media (prefers-color-scheme:dark){body{background:#151515;color:#e6e6e6}
details{border-color:#444}td,th{border-color:#333}th,summary span,.low{color:#aaa}
h2{border-color:#555}a{color:#8ab4f8}.high{color:#ff8a80}.medium{color:#ffcc80}}
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _link(url: str) -> str:
    return f'<a href="{_e(url)}">{_e(url)}</a>'


def _links(text: str) -> str:
    return "<br>".join(_link(u) for u in text.split(" | ") if u)


def _table(pairs: list[tuple[str, str]]) -> str:
    rows = "".join(f"<tr><th>{_e(k)}</th><td>{v}</td></tr>" for k, v in pairs if v)
    return f'<div class="wrap"><table>{rows}</table></div>'


def _finding_html(finding: dict) -> str:
    evidence = finding["evidence"]
    pairs: list[tuple[str, str]] = []
    if finding["type"] == "h1_title_mismatch" and "pages" in evidence:
        pairs = [(p["main_entity"], (f"{_link(p['url'])}<br>H1: {_e(p['h1']) or '(nincs)'}<br>"
                                    f"title: {_e(p['title'])}")) for p in evidence["pages"]]
    elif finding["type"] == "h1_title_mismatch":
        pairs = [("oldal", _link(evidence["url"])), ("fő entitás", _e(evidence["main_entity"])),
                 ("H1", _e(evidence["h1"]) or "(nincs)"), ("title", _e(evidence["title"])),
                 ("a H1 más entitásai", _e("; ".join(evidence["h1_entities"]))),
                 ("elfogadott megnevezések", _e("; ".join(evidence["names"])))]
    elif finding["type"] in ("cannibalization", "shared_topic"):
        pairs = [("oldalak", "<br>".join(
            f"{_link(p['url'])} — {_e(p['role'])}; title: {_e(p['title'])}; másodlagos: "
            f"{_e('; '.join(p['secondary']) or '—')}" for p in evidence["pages"])),
            ("átfedés", "<br>".join(
                f"{_e(' ↔ '.join(o['pages']))}: közös másodlagos: "
                f"{_e('; '.join(o['secondary']) or '—')}, title-hasonlóság "
                f"{o['title_similarity']}" for o in evidence["overlaps"]))]
    elif finding["type"] in ("missing_page", "uncovered_topic"):
        pairs = [("teendő", _e(evidence["action"])),
                 ("alcsaládjai", _e("; ".join(f"{c['entity']} ({c['page_count']} oldal)"
                                              for c in evidence["subfamilies"]))),
                 ("súly és rang", _e(f"{evidence['weight']} ({evidence['rank']}. a "
                                     f"{evidence['ranked']}-ból)")),
                 ("említés", _e(f"{evidence['page_count']} oldal, {evidence['mentions']} említés, "
                                f"{evidence['structural']} oldalon szerkezeti helyen")),
                 ("a legtöbbet említő oldalak", "<br>".join(
                     f"{_link(p['url'])} ({p['mention_weight']:g})"
                     for p in evidence["top_pages"]))]
    else:
        pairs = [("oldal", _link(evidence["url"])), ("H1", _e(evidence["h1"])),
                 ("title", _e(evidence["title"])),
                 ("bizonyítékok", _e(evidence_text(evidence["evidence"]))
                  if "evidence" in evidence else "")]
    return (f'<details><summary><span class="{finding["severity"]}">[{finding["severity"]}]'
            f'</span> {_e(finding["summary"])}</summary>{_table(pairs)}</details>')


def _html(name: str, findings: list[dict], site: _Site, overview: list[dict],
          neighbourhood: list[dict], pages: list[dict]) -> str:
    parts = [(f'<!doctype html><html lang="hu"><head><meta charset="utf-8">'
              f'<meta name="viewport" content="width=device-width,initial-scale=1">'
              f"<title>{_e(name)} — entitásgráf-nézetek</title><style>{STYLE}</style></head>"
              f"<body><h1>{_e(name)} — megállapítások és nézetek</h1>"
              f"<p>{len(site.pages)} oldal, {len(site.weights)} súlyozott entitás, "
              f"{len(findings)} megállapítás. Ellenőrző nézet, nem ügyfélriport.</p>")]
    parts.append("<h2>Megállapítások</h2>")
    for kind in TYPES:
        group = [f for f in findings if f["type"] == kind]
        counts = Counter(f["severity"] for f in group)
        detail = ", ".join(f"{counts[s]} {s}" for s in SEVERITY_ORDER if counts[s])
        parts.append(f"<details><summary>{TYPE_LABELS[kind]} <span>({len(group)}"
                     f"{': ' + detail if detail else ''})</span></summary>"
                     + "".join(_finding_html(f) for f in group) + "</details>")
    parts.append("<h2>Site-áttekintő</h2>")
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in overview:
        by_type[row["típus"]].append(row)
    for kind, rows in sorted(by_type.items()):
        body = "".join(
            f"<tr><td>{r['rang']}.</td><td>{_e(r['entitás'])}"
            f"{' <span>(' + _e(r['altípus']) + ')</span>' if r['altípus'] else ''}</td>"
            f"<td>{r['súly']}</td><td>{_e(r['szülő'] or r['kategória'] or r['márka'])}</td>"
            f"<td>{_links(r['fő oldalak'])}</td></tr>" for r in rows)
        parts.append(f"<details><summary>{_e(kind)} <span>({len(rows)})</span></summary>"
                     f'<div class="wrap"><table><tr><td>rang</td><td>entitás</td><td>súly</td>'
                     f"<td>szülő / kategória / márka</td><td>fő oldalak</td></tr>{body}"
                     f"</table></div></details>")
    parts.append("<h2>Entitás környezete</h2>")
    for row in neighbourhood:
        parts.append(
            f"<details><summary>{_e(row['entitás'])} <span>({_e(row['típus'])}; "
            f"{row['rang']}., súly {row['súly']})</span></summary>" + _table([
                ("élek", _e(row["élek"]).replace("; ", "<br>")),
                ("fő oldalak", _links(row["fő oldalak"])),
                (f"csak említi ({row['csak említő oldalak száma']})",
                 _links(row["csak említő oldalak"]))]) + "</details>")
    parts.append("<h2>Oldalnézet</h2>")
    for row in pages:
        label = row["fő entitás"] or row["segédoldal"] or "nincs fő entitás"
        parts.append(
            f"<details><summary>{_e(urlsplit(row['url']).path or '/')}"
            f"{'?' + _e(urlsplit(row['url']).query) if urlsplit(row['url']).query else ''} "
            f"<span>— {_e(label)}</span></summary>" + _table([
                ("URL", _link(row["url"])),
                ("szerep", _e(" / ".join(filter(None, (row["szerep"], row["segédoldal"]))))),
                ("canonical", _e(row["canonical"])),
                ("fő entitás", _e(f"{row['fő entitás']} ({row['típus']}; "
                                  f"{row['megbízhatóság']})") if row["fő entitás"] else ""),
                ("bizonyítékok", _e(row["bizonyítékok"])),
                ("másodlagos", _e(row["másodlagos"])),
                ("H1", f"{_e(row['H1'])} <span>— benne a fő entitás: {row['a H1-ben']}</span>"
                 if row["fő entitás"] else _e(row["H1"])),
                ("title", f"{_e(row['title'])} <span>— benne a fő entitás: "
                          f"{row['a title-ben']}</span>" if row["fő entitás"]
                 else _e(row["title"])),
                ("amit még említ", _e(row["további említett entitások"])),
                ("megállapítások", _e(row["megállapítások"]).replace(" || ", "<br>"))])
            + "</details>")
    parts.append("</body></html>")
    return "".join(parts)
