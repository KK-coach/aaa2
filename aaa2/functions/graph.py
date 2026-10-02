"""Entitásgráf (M3 spec, „M3 — Entitásgráf”, 2–3. és 5–6. pont), LLM nélkül, az M2 tárolt
kimenetéből. Minden futás újraépíti a `page_nodes`, `edges`, `page_main_entity` és
`entity_weights` táblát.

- Oldal-csomópont (`page_nodes`): az M2/6 oldalszerepe (`pages.page_roles`), a webshopon a
  kategória- és a márka × kategória oldal (`category`, `listing`), a kezdőoldal (`home`: maga a
  kezdőoldal, vagy a saját hreflang-listájában álló nyelvi változata), a rólam- és szerzői oldal
  (`profile`: `PROFILE_TYPES`, vagy segédoldal-szerepnél a `PROFILE_URL_WORDS` szó; nem
  segédoldal, a fő entitása a személy); a segédoldal fajtája: `legal` (jogi, köszönő,
  hibaoldal: teljes útvonalszegmens vagy lekérdezés-érték, `pages.support_url`), `contact`
  (`ContactPage`, vagy entitásoldal-szerep nélkül a `CONTACT_URL_WORDS` szó), `list`
  (`CollectionPage`, `SearchResultsPage`, márka × kategória lista, a kategóriaoldal oldalhoz
  kötött entitás nélkül, pl. a gyökérkategória, és a segéd- vagy cikkoldal, amelynek
  content-blokkjai `LIST_MIN_SHARE`-nél nagyobb részben kivonatok, `teaser_share`),
  `checkout`, `placeholder` (kitöltőszöveg-oldal). Segédoldalnak nincs fő entitása. A segéd- vagy
  cikkszerepű oldal az URL szerint is kapcsolat (`CONTACT_URL_WORDS`) vagy lista (a
  `CATEGORY_SEGMENTS` teljes útvonalszegmens).
- Canonical: ha az oldal canonicalja (a láncot követve) egy másik oldal-csomópontra mutat, az
  oldal duplikátum (`canonical_page`): nem kap saját döntést, az eredeti szerepét, fő és
  másodlagos entitásait örökli, a csoportja az eredetié, és `duplicate_of` él köti hozzá; a
  súlyba nem számít. Ha a cél nincs a készletben, nem sikeres válasz, nem oldal-csomópont vagy
  a lánc körbeér, a canonical nem számít, és a `canonical_issue` jelöli.
- Fő entitás (`page_main_entity`), a bizonyítékok erőssége szerint (`EVIDENCE_RANK`):
  1. az oldalhoz kötött entitás (az oldalcsoport bármely tagjához, `anchored`); a kezdőoldalon a
     site entitása (`home`: a `role = brand` entitás, és a márkakapcsolat nélküli, szabályból
     vagy schemából jött `brand`, mint a `site._site_name_keys`-ben); a site bármely oldalához
     kötött cikk (work/article) nem jelölt: a cikkoldal fő entitása a téma;
  2. a JSON-LD `about` / `mainEntity` az oldalcsoport bármely tagjáról (a név, vagy az `@id` a
     site csomópontjai vagy oldalai szerint; a kérdés-válasz csomópontok nem számítanak;
     `schema_about`); cikkoldalon a site másik oldalához kötött saját ajánlatra mutató `about`
     / `mainEntity` nem bizonyíték (a cikk fő entitása a témája marad), helyette `supports` él; a profiloldalon a legtöbbet említett személy (`profile`; a profiloldal
     fő entitása ő, ha van);
  3. a kinyerés `primary_entities` listája (a sorszámmal; `primary`);
  4. a title, a H1 és a headingek nem sablon-említései (`title`, `h1`, `heading`); a már
     bizonyítékkal bíró jelöltnél a neve vagy aliasa a H1 és a title szövegében is (`occurs`);
  5. az oldal legtöbbet említett entitása, ha legalább `TOP_MIN_MENTIONS` említése van és
     egyértelműen az első (`top_mentions`; a sablon-említések nélkül);
  6. a más oldalcsoportokról érkező anchorok szövege (`inbound_anchor`) és az URL utolsó
     szakasza (`url`).
  A jelöltek sorrendje: a legerősebb bizonyíték, a `primary` sorszáma, a bizonyítékfajták
  száma, az említésszám. Jelölt nem lehet a demó- és a navigációs jelölésű entitás; a site
  entitása csak a kezdőoldalon, ha oldalhoz kötött vagy JSON-LD bizonyítéka van, vagy ha a
  `primary_entities` első eleme. Megbízhatóság (`confidence`): erős, ha oldalhoz kötött,
  kezdőoldali vagy JSON-LD, vagy `primary` és title / H1; közepes, ha a `primary` első eleme,
  vagy `primary` és heading / említéseloszlás, vagy title / H1 és egy további bizonyíték;
  különben gyenge. Másodlagos (legfeljebb `MAX_SECONDARY`): a többi jelölt, ha oldalhoz kötött
  vagy JSON-LD, vagy a H1 megnevezi és a `primary_entities` között van; nem a fő entitás
  névrokona, és a site entitása csak JSON-LD-vel. A döntés az összes jelölttel a
  `page_nodes.decision`-ben.
- Élek (`edges`): `mentions` (oldal → entitás, a nem sablon-említések pozíció szerinti
  súlyával, `config/graph.toml`), `main_entity` (oldal → entitás; fő 1, másodlagos 0,5), az M2/6
  kapcsolatai (`part_of`, `brand_of`, `offers`), `about` (cikk → a cikkoldal fő entitása),
  `duplicate_of` (oldal → a canonical szerinti eredeti), `supports` (a cikk entitása, vagy ha
  nincs, a cikkoldal → a saját ajánlat, amelyre a cikk JSON-LD `about`-ja mutat),
  `is_a`: termék → kategória a kategóriaoldalból (`category_page`, az M2/6 `in_category`-ja), és
  entitás → entitás a biztos Wikidata-osztályból (`wikidata`: az entitás P31 vagy P279 osztálya
  egy másik, biztos QID-jű site-entitás; `is_a_reason` szerint kimarad: az általános osztály,
  `GENERIC_CLASSES`; a P31 nem példány-típusú entitáson, `INSTANCE_TYPES`; tech és concept csak
  azonos típusú osztályhoz; az elvetettek `export_rejected`).
- Súly (`entity_weights`): az oldalszám, az említésszám, a szerkezeti helyű oldalak (title, H1,
  heading, navigáció), a fő és a másodlagos oldalak száma, a más oldalcsoportokról az entitás
  fő oldalaira mutató belső anchorok: a tartalmiak (`content_anchors`, a `links.position` =
  body) darabra, a navigációsak (`nav_anchors`: menü, lábléc, oldalsáv) forrás-oldalcsoportonként
  egyszer és külön, kisebb súllyal; a sablon-említés, az attribútumcímke (a pontosan kétcellás,
  tulajdonság–érték `table_row` első cellájában álló említés, pl. a műszaki táblázat „SCOP”
  sora; a több oszlopos, pl. árazási sor első cellája nem az; a fő entitás említésszámába sem
  számít), a demó és a canonical-duplikátum nem számít, és kimarad az az entitás, amelynek nincs tartalmi említése, fő vagy másodlagos
  oldala. A képlet: Σ súly × log2(1 + összetevő), egyetlen említésnél × `single_mention`
  (`config/graph.toml`).
"""
from __future__ import annotations

import csv
import json
import math
import re
import tomllib
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import duckdb

from aaa2.db.stable_json import dumps
from aaa2.engine import queries as crawl
from aaa2.entities import queries as extract_queries
from aaa2.entities import store
from aaa2.entities.gate import occurs
from aaa2.entities.placeholder import placeholder_pages
from aaa2.entities.rules import alias_key
from aaa2.resolver import queries as resolver_queries
from aaa2.resolver.names import normal_key
from aaa2.resolver.pages import PageInfo, home_urls, page_roles, page_types, page_url, support_url
from aaa2.resolver.pages import schema_nodes as page_schema_nodes

CONFIG_FILE = Path(__file__).parent / "config" / "graph.toml"
EVIDENCE_RANK = {"anchored": 1, "home": 1, "schema_about": 2, "profile": 2, "primary": 3,
                 "h1": 4, "title": 4, "heading": 4, "top_mentions": 5, "inbound_anchor": 6,
                 "url": 6}
STRONG = frozenset({"anchored", "home", "schema_about", "profile"})
EXCLUDED_FLAGS = ("demo", "navigational")
SUPPORT_SCHEMA = {"ContactPage": "contact", "CollectionPage": "list",
                  "SearchResultsPage": "list", "CheckoutPage": "checkout"}
CONTACT_URL_WORDS = ("contact", "kapcsolat", "kontakt", "contatti")
PROFILE_TYPES = frozenset({"AboutPage", "ProfilePage"})
PROFILE_URL_WORDS = ("about", "rolam", "rolunk", "author", "szerzo")
CATEGORY_SEGMENTS = frozenset({"category", "kategoria"})
CANONICAL_ISSUES = {"not_crawled": "a cél nincs a készletben", "error_status": "a cél hibás",
                    "not_a_node": "a cél nem oldal-csomópont", "loop": "körbeérő lánc"}
LIST_MIN_SHARE = 0.5                     # a teaser-blokkok aránya ennél nagyobb: lista
EXCERPT_CHARS = 40                       # a kivonat-teaser legalább ennyi jelnyi eleje
INSTANCE_TYPES = frozenset({"tech", "org", "product", "place", "person"})
SAME_TYPE_CLASSES = frozenset({"tech", "concept"})
_SEPARATORS = re.compile(r"[/\-_.?=&]+")
QUESTION_TYPES = frozenset({"Question", "Answer", "FAQPage"})
CONTENT_POSITIONS = ("title", "h1", "heading", "body")
STRUCTURAL_POSITIONS = ("title", "h1", "heading")
TOP_MIN_MENTIONS = 2
MAX_SECONDARY = 2
MAIN_WEIGHT = {"main": 1.0, "secondary": 0.5}
# entity, concept, method, process, activity, technology, field of study
GENERIC_CLASSES = frozenset({"Q35120", "Q151885", "Q1799072", "Q3249551", "Q1914636",
                             "Q11016", "Q2267705"})


# ---------------------------------------------------------------------------
# beállítás
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphConfig:
    mention_weights: Mapping[str, float]
    weights: Mapping[str, float]
    single_mention: float


def load_graph_config(path: Path = CONFIG_FILE) -> GraphConfig:
    """A súlyok; minden összetevő és pozíció megadva, nem negatív számmal."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    mention = raw.get("mention_weights") or {}
    weight = dict(raw.get("weight") or {})
    single = weight.pop("single_mention", None)
    needed = {"pages", "mentions", "structural", "main_pages", "content_anchors",
              "nav_anchors"}
    if set(mention) != {"title", "h1", "heading", "other"} or set(weight) != needed \
            or not isinstance(single, int | float) \
            or not all(isinstance(v, int | float) and v >= 0
                       for v in [*mention.values(), *weight.values(), single]):
        raise ValueError(f"{path.name}: [mention_weights] title, h1, heading, other; [weight] "
                         f"{', '.join(sorted(needed))}, single_mention; nem negatív számok")
    return GraphConfig(mention, weight, float(single))


# ---------------------------------------------------------------------------
# a futás
# ---------------------------------------------------------------------------


@dataclass
class GraphRun:
    pages: int = 0
    main: int = 0
    support: int = 0
    none: int = 0
    duplicates: int = 0                                   # canonical szerint (a fentiekben is)
    canonical_issues: Counter = field(default_factory=Counter)
    confidence: Counter = field(default_factory=Counter)
    edges: Counter = field(default_factory=Counter)
    weights: int = 0
    class_lookups: int = 0
    class_failures: int = 0
    is_a_rejected: list = field(default_factory=list)    # (honnan, hová, tulajdonság, ok)


@dataclass
class Candidate:
    entity_id: int
    evidence: dict = field(default_factory=dict)          # fajta → részlet
    mentions: int = 0
    primary_index: int | None = None

    def key(self) -> tuple:
        best = min(EVIDENCE_RANK[kind] for kind in self.evidence)
        return (best, 99 if self.primary_index is None else self.primary_index,
                -len(self.evidence), -self.mentions, self.entity_id)

    def confidence(self) -> str:
        kinds = set(self.evidence)
        headline = kinds & {"title", "h1"}
        if kinds & STRONG or ("primary" in kinds and headline):
            return "strong"
        if self.primary_index == 0 or ("primary" in kinds and kinds & {"heading", "top_mentions"}) \
                or (headline and len(kinds - headline) >= 1):
            return "medium"
        return "weak"

    def secondary(self) -> bool:
        """Másodlagos entitás lehet: oldalhoz kötött vagy JSON-LD, vagy a H1 megnevezi és a
        kinyerés fő entitásai között van."""
        kinds = set(self.evidence)
        return bool(kinds & STRONG) or {"h1", "primary"} <= kinds

    def as_dict(self) -> dict:
        return {"entity_id": self.entity_id, "evidence": self.evidence,
                "mentions": self.mentions, "confidence": self.confidence()}


def build_graph(con: duckdb.DuckDBPyConnection, config: GraphConfig | None = None, *,
                page_type_patterns: Mapping[str, Sequence[str]] | None = None,
                superclasses: Callable[[str], list[tuple[str, str]] | None] | None = None
                ) -> GraphRun:
    """A gráf újraépítése (lásd a modul leírását). `page_type_patterns`: a site-fájl
    `[page_types]` mintái (a webshop kategória- és márka × kategória oldalaihoz);
    `superclasses`: QID → (tulajdonság, osztály-QID) párok (`gate.KnowledgeBase.superclasses`;
    None: nincs Wikidata-él)."""
    config = config or load_graph_config()
    run = GraphRun()
    for table in ("page_nodes", "edges", "page_main_entity", "entity_weights"):
        con.execute(f"DELETE FROM {table}")
    graph = _Graph(con, page_type_patterns or {})
    decisions = {page_id: graph.decide(info) for page_id, info in sorted(graph.roles.items())
                 if page_id not in graph.canonical}
    for page_id, original in sorted(graph.canonical.items()):
        decisions[page_id] = graph.duplicate(decisions[original], original)
    for page_id, info in sorted(graph.roles.items()):
        decision = decisions[page_id]
        run.pages += 1
        run.duplicates += decision["canonical"] is not None
        if decision["canonical_issue"]:
            run.canonical_issues[decision["canonical_issue"]["issue"]] += 1
        if decision["status"] == "main":
            run.main += 1
            run.confidence[decision["main"].confidence()] += 1
        else:
            setattr(run, decision["status"], getattr(run, decision["status"]) + 1)
        _store_page(con, graph, info, decision)
    _edges(con, graph, config, decisions, superclasses, run)
    run.weights = _weights(con, graph, config)
    run.edges = Counter(dict(con.execute("SELECT type, count(*) FROM edges GROUP BY 1 ORDER BY ALL")
                             .fetchall()))
    return run


class _Graph:
    """Az M2 tárolt kimenete a döntéshez: oldalak, entitások, névindex, említések, JSON-LD,
    a kinyerés `primary_entities`-e, linkek."""

    def __init__(self, con: duckdb.DuckDBPyConnection,
                 page_type_patterns: Mapping[str, Sequence[str]]):
        self.con = con
        self.roles: dict[int, PageInfo] = page_roles(con)
        self.types = page_types(con, page_type_patterns) if page_type_patterns else {}
        self.homes = {page_url(u) for u in home_urls(con)}
        self.placeholder = placeholder_pages(con)
        self.groups: dict[str, list[int]] = defaultdict(list)
        for page_id, info in self.roles.items():
            self.groups[info.group].append(page_id)
        self.urls = {info.page_id: info.url for info in self.roles.values()}
        self.alternates = {meta.page_id: {page_url(entry.split("|", 1)[-1])
                                          for entry in meta.hreflang}
                           for meta in crawl.page_metas(con)}
        self.page_of_url = {page_url(info.url): info.page_id for info in self.roles.values()}
        rows = store.entities_for_graph___init__(con)
        self.entities = {row[0]: row for row in rows}
        self.excluded = {row[0] for row in rows if set(row[5] or []) & set(EXCLUDED_FLAGS)}
        self.site_entities = {entity_id for (entity_id,) in con.execute(
            "SELECT entity_id FROM entities e WHERE role = 'brand' OR (type = 'brand' AND "
            "source IN ('rule', 'schema') AND NOT EXISTS (SELECT 1 FROM entity_relations r "
            "WHERE r.from_id = e.entity_id AND r.type = 'brand_of')) ORDER BY ALL").fetchall()}
        self.index: dict[str, set[int]] = defaultdict(set)
        self.normal: dict[str, set[int]] = defaultdict(set)
        for entity_id, name, *_, aliases in [(r[0], r[1], r[4]) for r in rows]:
            for form in [name, *(aliases or [])]:
                self._index(entity_id, form)
        for entity_id, alias in sorted({(a.entity_id, a.alias)
                                        for a in resolver_queries.aliases(con)}):
            if entity_id in self.entities:
                self._index(entity_id, alias)
        self.mentions: dict[int, list[tuple]] = defaultdict(list)
        for page_id, entity_id, position, flags, region, kind, cells, end in store.page_entities_for_graph___init__(con):
            skip = "template" if "template" in (flags or []) \
                else "label" if kind == "table_row" and row_label(cells, end) else None
            self.mentions[page_id].append((entity_id, position, skip, region))
        self.counts = Counter(m[0] for rows in self.mentions.values() for m in rows)
        self.nodes = page_schema_nodes(con)
        self.id_names = self._schema_ids()
        self.primary = self._primary()
        self.inbound: dict[int, list[tuple[int, str, bool]]] = defaultdict(list)
        self.outbound: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for from_id, to_id, anchor, position in sorted(
                ((link.from_page_id, link.to_page_id, link.anchor, link.position)
                 for link in crawl.links(con) if link.to_page_id is not None),
                key=lambda row: (row[0], row[1], row[2] is None, row[2] or "", row[3])):
            if from_id in self.roles and to_id in self.roles \
                    and self.roles[from_id].group != self.roles[to_id].group:
                self.inbound[to_id].append((from_id, anchor or "", position == "body"))
                self.outbound[from_id].append((to_id, alias_key(anchor or "")))
        self.texts: dict[int, list[str]] = defaultdict(list)
        for page_id, text in [(b.page_id, b.text) for b in extract_queries.blocks(con)
                              if b.region == "content" and b.kind in ("heading", "paragraph")]:
            if alias_key(text or ""):
                self.texts[page_id].append(alias_key(text))
        self.articles = {e for e, row in self.entities.items() if row[7] is not None
                         and row[2] == "work" and row[3] == "article"}
        self.supports: dict[int, list[tuple[int, str]]] = {}   # cikkoldal → (ajánlat, tulajd.)
        self.canonical: dict[int, int] = {}          # duplikátum → az eredeti oldal
        self.canonical_issue: dict[int, dict] = {}
        self._canonicals()

    def _canonicals(self) -> None:
        """A canonical szerinti duplikátumok (a láncot az eredetiig követve) és a canonical
        nélküli döntés okai (`CANONICAL_ISSUES`)."""
        node = {canonical_key(info.url): page_id for page_id, info in self.roles.items()}
        status = {canonical_key(url): code for url, code in sorted(
            ((page.url, page.status) for page in crawl.pages(self.con)),
            key=lambda row: (row[0], row[1] is None, row[1] or 0))}
        declared: dict[int, int] = {}
        for page_id, canonical in [(meta.page_id, meta.canonical)
                                   for meta in crawl.page_metas(self.con)
                                   if meta.canonical is not None]:
            if page_id not in self.roles:
                continue
            target = canonical_key(urljoin(self.roles[page_id].url, canonical))
            if target == canonical_key(self.roles[page_id].url):
                continue
            if target in node:
                declared[page_id] = node[target]
                continue
            code = status.get(target)
            issue = "not_crawled" if target not in status \
                else "not_a_node" if code is not None and 200 <= code < 300 else "error_status"
            self.canonical_issue[page_id] = {"issue": issue, "canonical": canonical,
                                             "status": code}
        for page_id, target in declared.items():
            seen = {page_id}
            while target in declared and target not in seen:
                seen.add(target)
                target = declared[target]
            if target in seen:
                self.canonical_issue[page_id] = {"issue": "loop",
                                                 "canonical": self.urls[declared[page_id]],
                                                 "status": None}
            else:
                self.canonical[page_id] = target

    def _index(self, entity_id: int, form: str | None) -> None:
        if form and alias_key(form):
            self.index[alias_key(form)].add(entity_id)
            self.normal[normal_key(form)].add(entity_id)

    def _schema_ids(self) -> dict[str, str]:
        """A site JSON-LD csomópontjainak `@id` → név térképe (az összes oldalról)."""
        found: dict[str, str] = {}
        for nodes in self.nodes.values():
            for node in nodes:
                for item in _walk(node):
                    if isinstance(item.get("@id"), str) and isinstance(item.get("name"), str):
                        found.setdefault(item["@id"], item["name"])
        return found

    def _primary(self) -> dict[int, list[str]]:
        """Oldalanként a kinyerő modell legutóbbi kész rekordjának `primary_entities`-e."""
        found: dict[int, list[str]] = {}
        for page_id, value in store.entity_runs_for_graph__primary(self.con):
            if page_id not in found and value is not None:
                names = json.loads(value)
                found[page_id] = [n for n in names if isinstance(n, str)]
        return found

    def resolve(self, name: str, page_id: int | None = None, normal: bool = False) -> int | None:
        """A név entitása (kulcs szerint); több közül a nem kizárt, az, amelyiknek ez a neve
        (nem csak aliasa: a „Google Ads” nevű entitás megelőzi azt, amelynek „Google Ads” az
        aliasa), az oldalon említett, a több említésű."""
        key_of = normal_key if normal else alias_key
        key = key_of(name or "")
        pool = {e for e in (self.normal if normal else self.index).get(key, set())
                if e not in self.excluded}
        if not pool:
            return None
        on_page = {m[0] for m in self.mentions.get(page_id, [])}
        return min(pool, key=lambda e: (key_of(self.entities[e][1] or "") != key,
                                        e not in on_page, -self.counts[e], e))

    # -- oldalszerep és segédoldal ---------------------------------------------

    def role_of(self, info: PageInfo) -> tuple[str, str | None]:
        """(szerep, a segédoldal fajtája vagy None)."""
        kind = self.types.get(info.page_id)
        members = self.groups[info.group]
        own = page_url(info.url)
        if own in self.homes or (own in self.alternates.get(info.page_id, set()) and any(
                page_url(self.urls[p]) in self.homes for p in members)):
            return "home", None
        if info.page_id in self.placeholder:
            return info.role, "placeholder"
        if support_url(info.url):
            return "support", "legal"
        if info.role in ("support", "article") and url_has_word(info.url, CONTACT_URL_WORDS):
            return "support", "contact"
        if info.role in ("support", "article") and CATEGORY_SEGMENTS & set(
                urlsplit(info.url).path.lower().split("/")):
            return "listing", "list"
        types = {_short(t) for node in self.nodes.get(info.page_id, [])
                 for t in _as_list(node.get("@type"))}
        if info.role == "support" and (types & PROFILE_TYPES
                                       or url_has_word(info.url, PROFILE_URL_WORDS)):
            return "profile", None
        for schema_type, support in SUPPORT_SCHEMA.items():
            if schema_type in types:
                return info.role, support
        if kind == "brand_category":
            return "listing", "list"
        if kind == "category":
            anchored = any(row[7] in members for e, row in self.entities.items()
                           if row[7] is not None and e not in self.excluded)
            return ("category", None) if anchored else ("listing", "list")
        if info.role in ("support", "article") and self.teaser_share(info) > LIST_MIN_SHARE:
            return "listing", "list"
        return info.role, None

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

    # -- a fő entitás ----------------------------------------------------------

    def decide(self, info: PageInfo) -> dict:
        role, support = self.role_of(info)
        group = set(self.groups[info.group])
        articles = sorted(e for e in self.articles if self.entities[e][7] in group)
        candidates = self.candidates(info, home=role == "home", profile=role == "profile")
        ranked = sorted(candidates.values(), key=Candidate.key)
        if role == "profile":
            ranked.sort(key=lambda item: "profile" not in item.evidence)
        decision = {"role": role, "support": support, "candidates": ranked, "main": None,
                    "secondary": [], "articles": articles, "canonical": None,
                    "supports": self.supports.get(info.page_id, []),
                    "canonical_issue": self.canonical_issue.get(info.page_id),
                    "group": info.group}
        if support is not None:
            decision["status"] = "support"
            return decision
        if not ranked:
            decision["status"] = "none"
            return decision
        decision["status"] = "main"
        main = decision["main"] = ranked[0]
        names = {alias_key(self.entities[main.entity_id][1] or "")}
        for item in ranked[1:]:
            name = alias_key(self.entities[item.entity_id][1] or "")
            if len(decision["secondary"]) < MAX_SECONDARY and item.secondary() \
                    and name not in names and (item.entity_id not in self.site_entities
                                               or item.evidence.keys() & STRONG):
                decision["secondary"].append(item)
                names.add(name)
        return decision

    def duplicate(self, original: dict, original_id: int) -> dict:
        """A canonical-duplikátum döntése: az eredetié (szerep, fő és másodlagos entitások,
        csoport); a cikk `about`-éle az eredetiről jön."""
        return {**original, "articles": [], "supports": [], "canonical": original_id,
                "canonical_issue": None}

    def candidates(self, info: PageInfo, home: bool = False,
                   profile: bool = False) -> dict[int, Candidate]:
        """Az oldal jelöltjei a bizonyítékaikkal. A site oldalaihoz kötött cikk-entitások nem
        jelöltek (a cikkoldal fő entitása a téma). `profile`: rólam- vagy szerzői oldal; a
        legtöbbet említett személy `profile` bizonyítékot kap."""
        page_id = info.page_id
        found: dict[int, Candidate] = {}
        articles = self.articles

        def add(entity_id: int | None, kind: str, detail: object) -> None:
            if entity_id is None or entity_id in self.excluded or entity_id in articles:
                return
            item = found.setdefault(entity_id, Candidate(entity_id))
            item.evidence.setdefault(kind, detail)

        group = set(self.groups[info.group])
        for entity_id, row in self.entities.items():
            if row[7] is not None and row[7] in group:
                add(entity_id, "anchored", self.urls.get(row[7]))
        if home:
            for entity_id in sorted(self.site_entities):
                add(entity_id, "home", self.urls.get(page_id))
        supports: list[tuple[int, str]] = []
        for member in sorted(group, key=lambda p: p != page_id):     # a hreflang-pár is
            for node in self.nodes.get(member, []):
                for key in ("about", "mainEntity"):
                    for value in _as_list(node.get(key)):
                        name, target = self._about(value)
                        about = self.resolve(name, page_id) if name else target
                        if info.role == "article" and self._own_offer(about, group):
                            if (about, key) not in supports:
                                supports.append((about, key))
                            continue
                        add(about, "schema_about",
                            {"property": key, "name": name, "source": self.urls.get(member),
                             "page": self.urls.get(target) if target in self.urls else None}
                            if name or target else None)
        self.supports[page_id] = supports
        for index, name in enumerate(self.primary.get(page_id, [])):
            entity_id = self.resolve(name, page_id)
            add(entity_id, "primary", {"index": index, "name": name})
            if entity_id in found and found[entity_id].primary_index is None:
                found[entity_id].primary_index = index
        content = Counter()
        for entity_id, position, skip, _ in self.mentions.get(page_id, []):
            if skip:
                continue
            if position in STRUCTURAL_POSITIONS:
                add(entity_id, position, True)
            if position in CONTENT_POSITIONS:
                content[entity_id] += 1
        ranked = [(e, n) for e, n in content.most_common()
                  if e not in self.excluded and e not in articles]
        if ranked and ranked[0][1] >= TOP_MIN_MENTIONS \
                and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
            add(ranked[0][0], "top_mentions", ranked[0][1])
        if profile:
            people = [(e, n) for e, n in ranked if self.entities[e][2] == "person"]
            people += [(e, 0) for e, item in found.items() if self.entities[e][2] == "person"
                       and "schema_about" in item.evidence]
            if people:
                add(people[0][0], "profile", {"mentions": people[0][1]})
        anchors = Counter()
        for _, anchor, _ in self.inbound.get(page_id, []):
            entity_id = self.resolve(anchor, page_id)
            if entity_id is not None:
                anchors[entity_id] += 1
        for entity_id, count in anchors.items():
            add(entity_id, "inbound_anchor", count)
        slug = urlsplit(info.url).path.rstrip("/").rsplit("/", 1)[-1]
        if slug:
            add(self.resolve(slug.replace("-", " ").replace("_", " "), page_id, normal=True),
                "url", slug)
        for item in found.values():
            item.mentions = content.get(item.entity_id, 0)
            forms = [self.entities[item.entity_id][1], *(self.entities[item.entity_id][4] or [])]
            for kind, text in (("h1", info.h1), ("title", info.title)):
                if text and kind not in item.evidence and any(
                        form and occurs(form, text) for form in forms):
                    item.evidence[kind] = "text"
        for entity_id in list(found):
            if entity_id in self.site_entities and not home \
                    and not set(found[entity_id].evidence) & {"anchored", "schema_about"} \
                    and found[entity_id].primary_index != 0:
                del found[entity_id]
        return found

    def _own_offer(self, entity_id: int | None, group: set[int]) -> bool:
        """A site egy másik oldalához kötött saját ajánlat-e az entitás."""
        row = self.entities.get(entity_id) if entity_id is not None else None
        return row is not None and row[2] == "service" and row[7] is not None \
            and row[7] not in group

    def _about(self, value: object) -> tuple[str | None, int | None]:
        """Az `about` / `mainEntity` értéke: (név, vagy None; a célzott oldalhoz kötött
        entitás, vagy None). A kérdés-válasz csomópont nem számít."""
        if isinstance(value, str):
            return value, None
        if not isinstance(value, dict):
            return None, None
        if {_short(t) for t in _as_list(value.get("@type"))} & QUESTION_TYPES:
            return None, None
        name = value.get("name") if isinstance(value.get("name"), str) else None
        ref = value.get("@id") if isinstance(value.get("@id"), str) else None
        if name is None and ref is not None:
            name = self.id_names.get(ref)
        if name is None and ref is not None:
            page = self.page_of_url.get(page_url(ref))
            if page is not None:
                anchored = [e for e, row in self.entities.items() if row[7] is not None
                            and row[7] in self.groups[self.roles[page].group]
                            and e not in self.excluded]
                return None, min(anchored) if anchored else None
        return name, None


# ---------------------------------------------------------------------------
# tárolás
# ---------------------------------------------------------------------------


def _store_page(con: duckdb.DuckDBPyConnection, graph: _Graph, info: PageInfo,
                decision: dict) -> None:
    members = [graph.urls[p] for p in sorted(graph.groups[info.group]) if p != info.page_id]
    record = {"status": decision["status"], "support": decision["support"],
              "articles": decision["articles"],
              "supports": [offer for offer, _ in decision["supports"]],
              "canonical": graph.urls[decision["canonical"]] if decision["canonical"] else None,
              "canonical_issue": decision["canonical_issue"],
              "candidates": [c.as_dict() for c in decision["candidates"]]}
    issue = decision["canonical_issue"]
    con.execute(
        "INSERT INTO page_nodes (page_id, url, role, support_kind, title, h1, lang, group_key, "
        "hreflang_pages, main_status, canonical_page, canonical_issue, decision) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [info.page_id, info.url, decision["role"], decision["support"], info.title, info.h1,
         info.lang, decision["group"], members, decision["status"], decision["canonical"],
         issue["issue"] if issue else None, dumps(record, ensure_ascii=False)])
    chosen = ([("main", decision["main"])] if decision["main"] else []) \
        + [("secondary", c) for c in decision["secondary"]]
    for rank, (role, item) in enumerate(chosen, start=1):
        con.execute(
            "INSERT INTO page_main_entity (page_id, entity_id, role, rank, confidence, evidence) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [info.page_id, item.entity_id, role, rank, item.confidence(),
             dumps(item.evidence, ensure_ascii=False)])


def _edges(con: duckdb.DuckDBPyConnection, graph: _Graph, config: GraphConfig,
           decisions: Mapping[int, dict], superclasses, run: GraphRun) -> None:
    rows = []
    about: dict[tuple[int, int], dict] = {}
    supports: dict[tuple[str, int, int], dict] = {}
    weights = config.mention_weights
    for page_id in sorted(graph.roles):
        per: dict[int, Counter] = defaultdict(Counter)
        for entity_id, position, skip, _ in graph.mentions.get(page_id, []):
            if entity_id in graph.excluded or entity_id not in graph.entities:
                continue
            per[entity_id][skip or position] += 1
        for entity_id, counts in sorted(per.items()):
            weight = sum(n * weights.get(pos, weights["other"]) for pos, n in counts.items()
                         if pos not in ("template", "label"))
            rows.append(("page", page_id, "entity", entity_id, "mentions", "m2_mentions",
                         dict(counts), weight))
        decision = decisions[page_id]
        if decision["canonical"] is not None:
            rows.append(("page", page_id, "page", decision["canonical"], "duplicate_of",
                         "canonical", {"canonical": graph.urls[decision["canonical"]]}, None))
            continue
        chosen = ([("main", decision["main"])] if decision["main"] else []) \
            + [("secondary", c) for c in decision["secondary"]]
        for role, item in chosen:
            rows.append(("page", page_id, "entity", item.entity_id, "main_entity",
                         "m3_main_entity", {"role": role, "confidence": item.confidence(),
                                            "evidence": item.evidence}, MAIN_WEIGHT[role]))
        if decision["main"] is not None:
            for article in decision["articles"]:
                about.setdefault((article, decision["main"].entity_id),
                                 {"page": graph.urls[page_id]})
        for offer, key in decision["supports"]:
            source = ("entity", decision["articles"][0]) if decision["articles"] \
                else ("page", page_id)
            supports.setdefault((*source, offer), {"page": graph.urls[page_id],
                                                   "property": key})
    rows += [("entity", article, "entity", topic, "about", "m3_article_topic", evidence, None)
             for (article, topic), evidence in sorted(about.items())]
    rows += [(kind, source, "entity", offer, "supports", "schema_about", evidence, None)
             for (kind, source, offer), evidence in sorted(supports.items())]
    for from_id, to_id, kind, source, evidence in [
            (r.from_id, r.to_id, r.type, r.source, r.evidence)
            for r in resolver_queries.relations(con)]:
        if from_id in graph.excluded or to_id in graph.excluded \
                or from_id not in graph.entities or to_id not in graph.entities:
            continue
        detail = None if evidence in (None, "") else evidence
        if kind == "in_category":
            rows.append(("entity", from_id, "entity", to_id, "is_a", "category_page", detail,
                         None))
        else:
            rows.append(("entity", from_id, "entity", to_id, kind, source, detail, None))
    if superclasses is not None:
        rows += _wikidata_is_a(graph, superclasses, run)
    if rows:
        con.executemany(
            "INSERT INTO edges (from_kind, from_id, to_kind, to_id, type, source, evidence, "
            "weight) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(fk, fi, tk, ti, t, s, dumps(e, ensure_ascii=False) if e is not None
              else None, w) for fk, fi, tk, ti, t, s, e, w in rows])


def _wikidata_is_a(graph: _Graph, superclasses, run: GraphRun) -> list[tuple]:
    """Entitás → entitás `is_a`: az entitás biztos QID-jének osztálya egy lépésben egy másik
    site-entitás biztos QID-je (`is_a_reason`); a kiesők okkal a `run.is_a_rejected`-be."""
    confident = {row[0]: row[8] for row in graph.entities.values()
                 if row[9] == "confident" and row[8] and row[0] not in graph.excluded}
    by_qid: dict[str, list[int]] = defaultdict(list)
    for entity_id, qid in confident.items():
        by_qid[qid].append(entity_id)
    rows: dict[tuple[int, int], tuple] = {}
    rejected: dict[tuple[int, int], tuple] = {}
    for entity_id, qid in sorted(confident.items()):
        run.class_lookups += 1
        found = superclasses(qid)
        if found is None:
            run.class_failures += 1
            continue
        for prop, target_qid in found:
            if target_qid == qid:
                continue
            for target in by_qid.get(target_qid, []):
                if target == entity_id:
                    continue
                reason = is_a_reason(graph.entities[entity_id][2], graph.entities[target][2],
                                     prop, target_qid)
                if reason is None:
                    rows.setdefault((entity_id, target), (
                        "entity", entity_id, "entity", target, "is_a", "wikidata",
                        {"property": prop, "qid": qid, "class": target_qid}, None))
                else:
                    rejected.setdefault((entity_id, target), (entity_id, target, prop, reason))
    run.is_a_rejected = [r for key, r in sorted(rejected.items()) if key not in rows]
    return list(rows.values())


def is_a_reason(kind: str, target_kind: str, prop: str, target_qid: str) -> str | None:
    """Miért nem `is_a` a Wikidata-osztály (None: az): általános osztály (`GENERIC_CLASSES`);
    fogalomnál csak P279 (a P31 csak a példány-típusoknál, `INSTANCE_TYPES`); tech és fogalom
    csak azonos típusú osztályhoz (`SAME_TYPE_CLASSES`)."""
    if target_qid in GENERIC_CLASSES:
        return "általános osztály"
    if prop == "P31" and kind not in INSTANCE_TYPES:
        return f"P31 {kind} típuson"
    if kind in SAME_TYPE_CLASSES and target_kind != kind:
        return f"{kind} → {target_kind}"
    return None


def _weights(con: duckdb.DuckDBPyConnection, graph: _Graph, config: GraphConfig) -> int:
    pages: dict[int, set[int]] = defaultdict(set)
    mentions: Counter = Counter()
    structural: dict[int, set[int]] = defaultdict(set)
    present: set[int] = set()
    for page_id, rows in graph.mentions.items():
        if page_id in graph.canonical:
            continue
        for entity_id, position, skip, region in rows:
            if entity_id in graph.excluded or entity_id not in graph.entities:
                continue
            present.add(entity_id)
            if skip or position not in CONTENT_POSITIONS:
                continue
            pages[entity_id].add(page_id)
            mentions[entity_id] += 1
            if position in STRUCTURAL_POSITIONS or region == "chrome":
                structural[entity_id].add(page_id)
    roles: dict[int, Counter] = defaultdict(Counter)
    main_pages: dict[int, set[int]] = defaultdict(set)
    for page_id, entity_id, role in con.execute(
            "SELECT page_id, entity_id, role FROM page_main_entity ORDER BY ALL").fetchall():
        if page_id in graph.canonical:
            continue
        roles[entity_id][role] += 1
        if role == "main":
            main_pages[entity_id].add(page_id)
    content: Counter = Counter()
    navigation: Counter = Counter()
    for entity_id, targets in main_pages.items():
        links = [link for page_id in targets for link in graph.inbound.get(page_id, [])]
        content[entity_id] = sum(1 for _, _, body in links if body)
        navigation[entity_id] = len({graph.roles[source].group for source, _, body in links
                                     if not body})
    w = config.weights
    rows = []
    for entity_id in sorted(present):
        parts = {"pages": len(pages[entity_id]), "mentions": mentions[entity_id],
                 "structural": len(structural[entity_id]),
                 "main_pages": roles[entity_id]["main"],
                 "content_anchors": content[entity_id],
                 "nav_anchors": navigation[entity_id]}
        if not (parts["pages"] or parts["main_pages"] or roles[entity_id]["secondary"]):
            continue
        single = parts["mentions"] == 1
        weight = sum(w[k] * math.log2(1 + v) for k, v in parts.items())
        if single:
            weight *= config.single_mention
        rows.append((entity_id, parts["pages"], parts["mentions"], parts["structural"],
                     parts["main_pages"], roles[entity_id]["secondary"],
                     parts["content_anchors"], parts["nav_anchors"], single,
                     round(weight, 4)))
    if rows:
        con.executemany(
            "INSERT INTO entity_weights (entity_id, pages, mentions, structural, main_pages, "
            "secondary_pages, content_anchors, nav_anchors, single_mention, weight) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


# ---------------------------------------------------------------------------
# kivonatok
# ---------------------------------------------------------------------------


def export_csv(con: duckdb.DuckDBPyConnection, out: Path, name: str) -> dict[str, Path]:
    """A három táblázat: oldalanként a fő entitás (`<név>-main-entity.csv`), az éltábla
    (`<név>-edges.csv`) és az entitások súlya (`<név>-weights.csv`)."""
    out.mkdir(parents=True, exist_ok=True)
    names = dict(store.entities_for_export_csv(con))
    kinds = {row[0]: (row[1], row[2]) for row in store.entities_for_export_csv_2(con)}
    urls = dict(con.execute("SELECT page_id, url FROM page_nodes ORDER BY ALL").fetchall())
    chosen: dict[int, list[tuple]] = defaultdict(list)
    for page_id, entity_id, role, confidence, evidence in con.execute(
            "SELECT page_id, entity_id, role, confidence, evidence FROM page_main_entity "
            "ORDER BY page_id, rank").fetchall():
        chosen[page_id].append((entity_id, role, confidence, json.loads(evidence)))
    paths = {}
    pages = []
    for page_id, url, role, support, title, h1, lang, status, canonical, issue, decision in \
            con.execute(
                "SELECT page_id, url, role, support_kind, title, h1, lang, main_status, "
                "canonical_page, canonical_issue, decision FROM page_nodes ORDER BY url"
                ).fetchall():
        main = [c for c in chosen[page_id] if c[1] == "main"]
        secondary = [c for c in chosen[page_id] if c[1] == "secondary"]
        if canonical is not None:
            note = f"duplikátum: {urls[canonical]}"
        elif issue:
            note = f"nem számít ({CANONICAL_ISSUES[issue]}): " \
                f"{json.loads(decision)['canonical_issue']['canonical']}"
        else:
            note = ""
        pages.append({
            "url": url, "szerep": role, "segédoldal": support or "", "állapot": status,
            "canonical": note,
            "fő entitás": names.get(main[0][0], "") if main else "",
            "típus": "/".join(filter(None, kinds.get(main[0][0], ("", "")))) if main else "",
            "megbízhatóság": main[0][2] if main else "",
            "bizonyítékok": evidence_text(main[0][3]) if main else "",
            "másodlagos": "; ".join(f"{names.get(e, e)} ({c})" for e, _, c, _ in secondary),
            "title": title or "", "h1": h1 or "", "nyelv": lang or ""})
    paths["main_entity"] = _write(out / f"{name}-main-entity.csv", pages)
    edges = []
    for from_kind, from_id, to_kind, to_id, kind, source, evidence, weight in con.execute(
            "SELECT from_kind, from_id, to_kind, to_id, type, source, evidence, weight "
            "FROM edges ORDER BY type, from_kind, from_id, to_id").fetchall():
        edges.append({
            "honnan": urls.get(from_id, from_id) if from_kind == "page" else names.get(from_id),
            "honnan fajta": from_kind,
            "hová": urls.get(to_id, to_id) if to_kind == "page" else names.get(to_id),
            "hová fajta": to_kind, "él": kind, "forrás": source,
            "súly": "" if weight is None else round(weight, 4),
            "bizonyíték": evidence or ""})
    paths["edges"] = _write(out / f"{name}-edges.csv", edges)
    weights = []
    for rank, row in enumerate(con.execute(
            "SELECT w.entity_id, w.pages, w.mentions, w.structural, w.main_pages, "
            "w.secondary_pages, w.content_anchors, w.nav_anchors, w.single_mention, "
            "w.weight FROM "
            "entity_weights w JOIN entities e USING (entity_id) "
            "ORDER BY w.weight DESC, e.name").fetchall(), start=1):
        entity_id, *parts, weight = row
        weights.append({"rang": rank, "entitás": names.get(entity_id),
                        "típus": "/".join(filter(None, kinds.get(entity_id, ("", "")))),
                        "oldalak": parts[0], "említések": parts[1], "szerkezeti": parts[2],
                        "fő oldalak": parts[3], "másodlagos oldalak": parts[4],
                        "tartalmi anchorok": parts[5], "navigációs anchorok": parts[6],
                        "egy említés": parts[7], "súly": weight})
    paths["weights"] = _write(out / f"{name}-weights.csv", weights)
    return paths


def export_rejected(con: duckdb.DuckDBPyConnection, run: GraphRun, out: Path,
                    name: str) -> Path:
    """A Wikidata-osztályból kiesett `is_a`-élek okkal: `<név>-is-a-rejected.csv`."""
    names = {row[0]: (row[1], row[2], row[3]) for row in store.entities_for_export_rejected(con)}
    rows = [{"honnan": names[a][0], "honnan típus": names[a][1], "QID": names[a][2],
             "hová": names[b][0], "hová típus": names[b][1], "osztály": names[b][2],
             "tulajdonság": prop, "ok": reason} for a, b, prop, reason in run.is_a_rejected]
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}-is-a-rejected.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["honnan", "honnan típus", "QID", "hová",
                                                    "hová típus", "osztály", "tulajdonság",
                                                    "ok"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def evidence_text(evidence: Mapping) -> str:
    """A bizonyítékok rövid szövege az erősség sorrendjében."""
    parts = []
    for kind in sorted(evidence, key=lambda k: (EVIDENCE_RANK[k], k)):
        detail = evidence[kind]
        if kind == "primary":
            parts.append(f"primary#{detail['index']}")
        elif kind in ("top_mentions", "inbound_anchor"):
            parts.append(f"{kind}:{detail}")
        else:
            parts.append(kind)
    return "; ".join(parts)


def _write(path: Path, rows: list[dict]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# segédek
# ---------------------------------------------------------------------------


def row_label(cells: str | None, end: int | None) -> bool:
    """A táblázatsor említése attribútumcímke-e: a pontosan kétcellás (tulajdonság–érték) sor
    első cellájában áll (a blokk szövege a cellák „ | ”-vel összefűzve, az első cella a 0.
    jeltől)."""
    values = json.loads(cells) if cells else []
    return len(values) == 2 and end is not None \
        and end <= len(str(values[0].get("value") or ""))


def canonical_key(url: str) -> str:
    """A canonical-összevetés kulcsa: töredék és záró perjel nélkül, a lekérdezéssel (a
    `?tab=` változatok külön oldalak)."""
    parts = urlsplit(url.split("#", 1)[0])
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/') or '/'}" \
        + (f"?{parts.query}" if parts.query else "")


def url_has_word(url: str, stems: Sequence[str]) -> bool:
    """Az URL útvonalában vagy lekérdezésében szókezdettől áll-e valamelyik minta (az
    elválasztók egységesítve): a „privacy-centre” és a `?tab=privacy_policy` igen, az
    „eprivacy-and-gdpr” nem."""
    parts = urlsplit(url)
    text = _SEPARATORS.sub("-", f"{parts.path}-{parts.query}".lower())
    return any(re.search(rf"(?:^|-){re.escape(_SEPARATORS.sub('-', stem))}", text)
               for stem in stems)


def _walk(node: object):
    """A JSON-LD csomópont és a beágyazott csomópontjai."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _short(value: object) -> str:
    text = str(value).strip()
    return text.replace("#", "/").replace(":", "/").rsplit("/", 1)[-1] if text else text


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value] if value is not None else []
