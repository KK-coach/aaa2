"""Determinisztikus entitás-kör, LLM nélkül: a crawl adatbázisából olvas, az `entities` és a
`page_entities` táblát tölti, és egy `entity_runs` sort ír.

Csak a sikeres (2xx, hiba nélküli, renderelt DOM-mal bíró) oldalakból dolgozik.

- schema: a JSON-LD blokkok minden `@type`-os csomópontja, a beágyazottak is, ha a típusa a
  `config/schema_types.toml` leképezésében szerepel és van szöveges `name`-je → entitás a
  leképezett típussal; position = schema, evidence = a `name` értéke, source = schema.
- személynév: két `person`, ha mindkettő kéttokenes és a token-halmazuk azonos (a kulcs szerint,
  tehát ékezet-érzéketlenül), egy entitás ("Kiss Krisztián" = "Krisztian Kiss").
- a site neve: a leggyakoribb org-típusú schema-név, a leggyakoribb `og:site_name`, és a title-ök
  ismétlődő végződései (legalább `MIN_TITLE_PAGES` oldalon és a title-ös oldalak
  `MIN_TITLE_SHARE` részén; ami egy elfogadott név végszelete, vagy egy elfogadott névre végződik,
  kimarad). Ha a név kulcsa egy talált org-é, az org kapja a sorait; ha legfeljebb
  `SHORT_NAME_CHARS` jelű, és a jelei sorrendben benne vannak egy ilyen org nevében ("KK" a
  "kk.coach"-ban), az org aliasa; különben brand. Sor ott, ahol a név a title-ben vagy az első
  H1-ben áll: position = title / h1, evidence = a szó szerinti részlet, source = rule.
- anchor: a belső linkek anchorja, ha legalább `MIN_ANCHOR_PAGES` különböző oldalon azonos
  (kulcs szerint). Ha a kulcs egy talált entitásé, ahhoz kerül; különben concept-jelölt, ha
  egyetlen célra mutat (két vagy több célra: navigációs, kimarad). position = anchor, source =
  rule. Kimarad a betű nélküli, a legfeljebb 2 jelű és a csupa nagybetűs római szám anchor, az
  oldalra önmagára, a kezdőoldalra (`site_profile.home_urls`), a nem crawlolt oldalra (a cél nincs
  a `pages`-ben) és a más nyelvű oldalra mutató link.
- alias: a név kulcsa (`alias_key`) kisbetűs, ékezet és kötőjel nélküli; egy kulcs és típus egy
  entitás, a többi írásmód az `aliases`-ben.
- kanonikus név: a legerősebb forrás (schema > title / H1 > anchor) alakjai közül az ékezetes,
  azon belül a leggyakoribb; a csak title-ből jött brandnél az elsőbbségi sor első neve.
- context: anchornál az oldalon az első ilyen anchor legközelebbi blokk-ősének szövege (ha üres,
  mint a képes linknél, az anchor maga), schemánál a csomópont JSON-ja, title-nél és H1-nél a
  teljes szöveg. section_ordinal: az elem előtti utolsó heading `headings.ordinal`-ja; 0, ha
  nincs előtte heading (a `<head>` is ez); a H1 sora a saját ordinalja.
- lang: azoknak az oldalaknak a leggyakoribb elsődleges nyelvi címkéje, ahol a kanonikus alak
  előfordul; ha ilyen nincs, az entitás összes oldaláé; ha az sincs, a site első nyelve.

Újrafuttatható: a futás a korábbi schema- és rule-sorokat cseréli; a (kulcs, típus) szerint
azonos entitás az azonosítóját megtartja (a source az erősebb lesz: schema > rule > llm), a más
forrású sorok és entitásaik megmaradnak.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import tomllib
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import zstandard
from selectolax.parser import HTMLParser, Node

from aaa2.engine.parse import HEADING_TAGS, anchor_text, schema_items
from aaa2.engine.site_profile import home_urls
from aaa2.llm.schemas import ENTITY_TYPES

SCHEMA_TYPES_FILE = Path(__file__).parent / "config" / "schema_types.toml"
MIN_ANCHOR_PAGES = 3
MIN_TITLE_PAGES = 3
MIN_TITLE_SHARE = 0.25
SHORT_NAME_CHARS = 4
MAX_TRIVIAL_ANCHOR_CHARS = 2
CONTEXT_CHARS = 500
TITLE_SEPARATORS = (" | ", " - ", " – ", " — ", " · ", " :: ", " » ", " • ")
# A forrás erőssége a kanonikus névhez: kisebb az erősebb.
SOURCE_RANK = {"schema": 0, "title": 1, "h1": 1, "anchor": 2}
# Egy anchor ehhez a típushoz kerül elsőnek, ha a kulcsa több talált entitásé is.
ATTACH_ORDER = ("org", "brand", "person", "product", "service", "event", "work", "place",
                "tech", "concept")

_SKIPPED_ANCESTORS = frozenset({"noscript", "template"})
_NON_BODY_TEXT = frozenset({"script", "style", "noscript", "template", "head"})
# Az entitás forrása: kisebb az erősebb; újrafuttatáskor az erősebb marad.
SOURCE_STRENGTH = {"schema": 0, "rule": 1, "llm": 2}
_BLOCK_TAGS = frozenset({
    "p", "li", "td", "th", "dd", "dt", "figcaption", "blockquote", "address", "caption",
    "h1", "h2", "h3", "h4", "h5", "h6",
})
_DASHES = frozenset("-‐‑‒–—―−")
_TRAILING_SEPARATORS = re.compile(r"[\s|\-–—·:»•]+$")
_WHITESPACE = re.compile(r"\s+")
_ROMAN = re.compile(r"(?=[ivxlcdm]+$)m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})")
# Egy szó belsejében álló írásjel ("kk.coach", "a_b"): a határvizsgálat ezeken nem ugrik át.
_INNER_PUNCTUATION = frozenset("._@/&+'")


# ---------------------------------------------------------------------------
# alias-kulcs és normalizált keresés
# ---------------------------------------------------------------------------


def alias_key(text: str) -> str:
    """Kisbetűs, ékezet nélküli, a kötőjelek és a whitespace egy szóközzé; HTML-entitás
    feloldva."""
    return "".join(piece for piece, _ in _normalized(html_lib.unescape(text))).strip()


def find_name(text: str, key: str) -> tuple[int, int] | None:
    """A `key` első, szóhatáron álló előfordulása a `text` normalizált alakjában; az eredeti
    szöveg (kezdet, vég) indexei, vagy None."""
    if not key:
        return None
    pieces = list(_normalized(text))
    normalized = "".join(piece for piece, _ in pieces)
    origin = [index for piece, index in pieces for _ in piece]
    start = normalized.find(key)
    while start != -1:
        end = start + len(key)
        if _boundary(normalized, start - 1, -1) and _boundary(normalized, end, 1):
            return origin[start], origin[end - 1] + 1
        start = normalized.find(key, start + 1)
    return None


def is_abbreviation(short_key: str, name_key: str) -> bool:
    """Legfeljebb `SHORT_NAME_CHARS` betű vagy szám, és sorrendben benne vannak a névben."""
    if not _short_name(short_key):
        return False
    rest = iter(c for c in name_key if c.isalnum())
    return all(char in rest for char in short_key if char.isalnum())


def _short_name(key: str) -> bool:
    return 0 < sum(char.isalnum() for char in key) <= SHORT_NAME_CHARS


def trivial_anchor(anchor: str) -> str | None:
    """A kimaradás oka, ha az anchor nem lehet entitás-jelölt: betű nélküli, a kulcsa legfeljebb
    `MAX_TRIVIAL_ANCHOR_CHARS` jelű, vagy csupa nagybetűs római szám ("VII"; a "Mix" nem)."""
    key = alias_key(anchor)
    if not any(char.isalpha() for char in key):
        return "anchor_without_letter"
    if len(key) <= MAX_TRIVIAL_ANCHOR_CHARS:
        return "anchor_short"
    if anchor.strip().isupper() and _ROMAN.fullmatch(key):
        return "anchor_roman_numeral"
    return None


def stronger_source(current: str | None, new: str) -> str:
    """A két forrás közül az erősebb (schema > rule > llm); ismeretlen forrás a leggyengébb."""
    return min((current or new, new), key=lambda s: SOURCE_STRENGTH.get(s, len(SOURCE_STRENGTH)))


def _normalized(text: str) -> Iterator[tuple[str, int]]:
    """(normalizált darab, eredeti index) karakterenként; az egymás utáni szóközök egy
    szóközzé, a kezdő szóköz elhagyva."""
    previous_space = True
    for index, char in enumerate(text):
        if char.isspace() or char in _DASHES:
            if not previous_space:
                previous_space = True
                yield " ", index
            continue
        folded = "".join(c for c in unicodedata.normalize("NFKD", char.casefold())
                         if not unicodedata.combining(c))
        if folded:
            previous_space = False
            yield folded, index


def _boundary(text: str, position: int, step: int) -> bool:
    if position < 0 or position >= len(text):
        return True
    char = text[position]
    if char.isalnum():
        return False
    beyond = position + step
    return not (char in _INNER_PUNCTUATION and 0 <= beyond < len(text)
                and text[beyond].isalnum())


def _accented(text: str) -> bool:
    return any(unicodedata.combining(c) for c in unicodedata.normalize("NFKD", text))


# ---------------------------------------------------------------------------
# leképezés és a title-végződések
# ---------------------------------------------------------------------------


def load_schema_types(path: Path = SCHEMA_TYPES_FILE) -> dict[str, str]:
    mapping = tomllib.loads(path.read_text(encoding="utf-8"))
    invalid = {k: v for k, v in mapping.items() if v not in ENTITY_TYPES}
    if invalid:
        raise ValueError(f"{path.name}: ismeretlen entitás-típus: {invalid}")
    return mapping


def title_endings(title: str) -> list[str]:
    """A title és minden elválasztó utáni végszelete; a végén álló elválasztó nélkül."""
    title = _TRAILING_SEPARATORS.sub("", title.strip())
    endings = [title] if title else []
    for separator in TITLE_SEPARATORS:
        start = title.find(separator)
        while start != -1:
            tail = title[start + len(separator):].strip()
            if tail and tail not in endings:
                endings.append(tail)
            start = title.find(separator, start + 1)
    return endings


def site_title_names(titles: list[str]) -> list[str]:
    """A title-ök ismétlődő végződései, oldalszám és hossz szerint csökkenő sorrendben; ami egy
    már elfogadott név végszelete, vagy egy elfogadott névre végződik, kimarad."""
    pages: Counter[str] = Counter()
    forms: dict[str, Counter[str]] = defaultdict(Counter)
    for title in titles:
        seen: set[str] = set()
        for ending in title_endings(title):
            key = alias_key(ending)
            if key and key not in seen:
                seen.add(key)
                pages[key] += 1
                forms[key][ending] += 1
    needed = max(MIN_TITLE_PAGES, MIN_TITLE_SHARE * len(titles))
    accepted: list[str] = []
    accepted_keys: list[str] = []
    for key in sorted((k for k, n in pages.items() if n >= needed),
                      key=lambda k: (-pages[k], -len(k))):
        name = forms[key].most_common(1)[0][0]
        tails = {alias_key(ending) for ending in title_endings(name)}
        if any(key in {alias_key(e) for e in title_endings(a)} for a in accepted) \
                or tails & set(accepted_keys):
            continue
        accepted.append(name)
        accepted_keys.append(key)
    return accepted


# ---------------------------------------------------------------------------
# a futás
# ---------------------------------------------------------------------------


@dataclass
class Mention:
    page_id: int
    position: str
    evidence: str
    context: str
    section: int
    source: str
    count: int = 1


@dataclass
class Candidate:
    type: str
    source: str
    names: list[str] = field(default_factory=list)      # a site-név elsőbbségi sora (brand)
    forms: Counter[str] = field(default_factory=Counter)
    ranks: dict[str, int] = field(default_factory=dict)  # alak → a legerősebb forrása
    mentions: list[Mention] = field(default_factory=list)

    def add_form(self, form: str, position: str, count: int = 1) -> None:
        self.forms[form] += count
        rank = SOURCE_RANK[position]
        self.ranks[form] = min(rank, self.ranks.get(form, rank))

    def absorb(self, other: Candidate) -> None:
        for form, count in other.forms.items():
            self.forms[form] += count
            self.ranks[form] = min(other.ranks[form], self.ranks.get(form, other.ranks[form]))
        self.mentions.extend(other.mentions)

    def canonical(self) -> str:
        if self.type == "brand" and self.names:
            return self.names[0]
        best = min(self.ranks.values())
        order = list(self.ranks)
        return max((form for form, rank in self.ranks.items() if rank == best),
                   key=lambda f: (_accented(f), self.forms[f], -order.index(f)))


@dataclass(frozen=True)
class EntityRun:
    run_id: int
    pages: int
    pages_with_entities: int
    entities: int
    rows: int
    by_position: dict[str, int]
    skipped: dict[str, object]


@dataclass
class _PageDom:
    site_names: list[str]
    anchors: dict[str, tuple[str, int]]       # anchor-szöveg → (context, section) az első helyen
    schema_sections: list[int]                # schema_blocks.ordinal - 1 → section


def run_rules(con: duckdb.DuckDBPyConnection,
              clock: Callable[[], datetime] | None = None) -> EntityRun:
    clock = clock or _now
    started = clock()
    mapping = load_schema_types()
    pages = con.execute(
        "SELECT page_id, url, title, h1, lang, rendered_html FROM pages "
        "WHERE status BETWEEN 200 AND 299 AND error IS NULL AND rendered_html IS NOT NULL "
        "ORDER BY page_id"
    ).fetchall()
    page_ids = [row[0] for row in pages]
    page_lang = {page_id: _primary(lang) for page_id, _, _, _, lang, _ in pages}
    decompressor = zstandard.ZstdDecompressor()
    dom = {page_id: _page_dom(decompressor.decompress(blob).decode("utf-8", "replace"))
           for page_id, _, _, _, _, blob in pages}
    skipped: dict[str, object] = {}
    candidates: dict[tuple[str, str], Candidate] = {}

    _schema(con, page_ids, dom, mapping, candidates, skipped)
    _merge_person_names(candidates)
    _site_names(con, pages, dom, candidates, skipped)
    _anchors(con, page_ids, dom, candidates, skipped)

    site_languages = (con.execute("SELECT languages FROM site").fetchone() or [None])[0] or []
    fallback_lang = _primary(site_languages[0]) if site_languages else None
    keys_of: dict[int, list[tuple[str, str]]] = defaultdict(list)
    unique: dict[int, Candidate] = {}
    for pair, candidate in candidates.items():
        keys_of[id(candidate)].append(pair)
        unique.setdefault(id(candidate), candidate)
    con.begin()
    try:
        con.execute("DELETE FROM page_entities WHERE source IN ('schema', 'rule')")
        existing = _existing_entities(con)
        rows = 0
        by_position: Counter[str] = Counter()
        entity_ids: set[int] = set()
        pages_with: set[int] = set()
        for ident, candidate in unique.items():
            if not candidate.mentions:
                continue
            name = candidate.canonical()
            aliases = sorted({form for form in candidate.forms if form != name})
            lang = _language(candidate, name, page_lang) or fallback_lang
            entity_id = next((existing[pair] for pair in keys_of[ident] if pair in existing),
                             None)
            if entity_id is None:
                (entity_id,) = con.execute(
                    "INSERT INTO entities (name, lang, type, aliases, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) RETURNING entity_id",
                    [name, lang, candidate.type, aliases, candidate.source, started],
                ).fetchone()
            else:
                (current,) = con.execute("SELECT source FROM entities WHERE entity_id = ?",
                                         [entity_id]).fetchone()
                con.execute(
                    "UPDATE entities SET aliases = list_distinct(list_concat(coalesce(aliases, "
                    "[]), ?)), lang = coalesce(lang, ?), source = ? WHERE entity_id = ?",
                    [aliases, lang, stronger_source(current, candidate.source), entity_id],
                )
            entity_ids.add(entity_id)
            for mention in candidate.mentions:
                con.execute(
                    "INSERT INTO page_entities (page_id, entity_id, position, evidence, context, "
                    "section_ordinal, count, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [mention.page_id, entity_id, mention.position, mention.evidence,
                     mention.context, mention.section, mention.count, mention.source],
                )
                rows += 1
                by_position[mention.position] += 1
                pages_with.add(mention.page_id)
        con.execute(
            "DELETE FROM entities WHERE source IN ('schema', 'rule') AND entity_id NOT IN "
            "(SELECT entity_id FROM page_entities)"
        )
        (run_id,) = con.execute(
            "INSERT INTO entity_runs (started_at, finished_at, method, pages, "
            "pages_with_entities, entities, row_count, llm_calls, by_position, skipped) "
            "VALUES (?, ?, 'rules', ?, ?, ?, ?, 0, ?, ?) RETURNING run_id",
            [started, clock(), len(pages), len(pages_with), len(entity_ids), rows,
             json.dumps(dict(sorted(by_position.items()))), json.dumps(skipped,
                                                                     ensure_ascii=False)],
        ).fetchone()
        con.commit()
    except Exception:
        con.rollback()
        raise
    return EntityRun(run_id, len(pages), len(pages_with), len(entity_ids), rows,
                     dict(by_position), skipped)


# ---------------------------------------------------------------------------
# források
# ---------------------------------------------------------------------------


def _schema(con, page_ids, dom, mapping, candidates, skipped) -> None:
    unmapped: Counter[str] = Counter()
    nameless: Counter[str] = Counter()
    per_page: dict[tuple[int, str, str], Mention] = {}
    for page_id, ordinal, raw in con.execute(
        "SELECT page_id, ordinal, json FROM schema_blocks WHERE type IS DISTINCT FROM 'invalid' "
        "AND list_contains(?, page_id) ORDER BY page_id, ordinal", [page_ids],
    ).fetchall():
        try:
            block = json.loads(raw)
        except ValueError:
            continue
        sections = dom[page_id].schema_sections
        section = sections[ordinal - 1] if 0 < ordinal <= len(sections) else 0
        for node in _typed_nodes(block):
            types = [_short_type(t) for t in _as_list(node.get("@type"))]
            kind = next((mapping[t] for t in types if t in mapping), None)
            if kind is None:
                unmapped.update(types)
                continue
            name = _name(node.get("name"))
            if not name:
                nameless.update(types)
                continue
            key = alias_key(name)
            candidate = candidates.setdefault((key, kind), Candidate(kind, "schema"))
            candidate.add_form(html_lib.unescape(name).strip(), "schema")
            mention = per_page.get((page_id, key, kind))
            if mention is None:
                context = json.dumps(node, ensure_ascii=False, separators=(",", ":"))
                mention = Mention(page_id, "schema", name, context[:CONTEXT_CHARS], section,
                                  "schema", 0)
                per_page[(page_id, key, kind)] = mention
                candidate.mentions.append(mention)
            mention.count += 1
    if unmapped:
        skipped["unmapped_schema_types"] = dict(unmapped.most_common())
    if nameless:
        skipped["schema_without_name"] = dict(nameless.most_common())


def _merge_person_names(candidates) -> None:
    """Két kéttokenes person azonos token-halmazzal egy entitás; a második kulcsa is az
    elsőre mutat."""
    groups: dict[frozenset[str], list[tuple[str, Candidate]]] = defaultdict(list)
    for (key, kind), candidate in candidates.items():
        tokens = key.split()
        if kind == "person" and len(tokens) == 2:
            groups[frozenset(tokens)].append((key, candidate))
    for members in groups.values():
        _, keep = members[0]
        for key, other in members[1:]:
            keep.absorb(other)
            candidates[(key, "person")] = keep


def _site_names(con, pages, dom, candidates, skipped) -> None:
    orgs = {key: candidate for (key, kind), candidate in candidates.items() if kind == "org"}
    org_pages = Counter({candidate.canonical(): len({m.page_id for m in candidate.mentions})
                         for candidate in orgs.values()})
    og_names = Counter(name for page in dom.values() for name in set(page.site_names))
    titles = [title for _, _, title, _, _, _ in pages if title]
    names = ([org_pages.most_common(1)[0][0]] if org_pages else []) \
        + ([og_names.most_common(1)[0][0]] if og_names else []) \
        + site_title_names(titles)
    h1_ordinals = dict(con.execute(
        "SELECT page_id, min(ordinal) FROM headings WHERE level = 1 GROUP BY page_id"
    ).fetchall())

    def mentions_of(key: str) -> list[Mention]:
        found = []
        for page_id, _, title, h1, _, _ in pages:
            for position, text, section in (("title", title, 0),
                                            ("h1", h1, h1_ordinals.get(page_id, 0))):
                span = find_name(text or "", key)
                if span:
                    found.append(Mention(page_id, position, text[span[0]:span[1]], text,
                                         section, "rule"))
        return found

    site_orgs: list[tuple[str, Candidate]] = []
    short: list[tuple[str, str]] = []
    without_rows: list[str] = []
    done: set[str] = set()
    for name in names:
        key = alias_key(name)
        if key in done:
            continue
        done.add(key)
        if key in orgs:
            site_orgs.append((key, orgs[key]))
            target = orgs[key]
        elif _short_name(key):
            short.append((key, name))
            continue
        else:
            target = candidates.get((key, "brand")) or Candidate("brand", "rule")
            target.names.append(name)
            target.add_form(name, "title")
        found = mentions_of(key)
        _attach(target, found)
        if found and target.type == "brand":
            candidates[(key, "brand")] = target
        if not found:
            without_rows.append(name)
    for key, name in short:
        org = next((org for org_key, org in site_orgs if is_abbreviation(key, org_key)), None)
        target = org or candidates.get((key, "brand")) or Candidate("brand", "rule", [name])
        found = mentions_of(key)
        _attach(target, found)
        if org is not None:
            candidates[(key, "org")] = org
        elif found:
            target.add_form(name, "title")
            candidates[(key, "brand")] = target
        if not found:
            without_rows.append(name)
    if without_rows:
        skipped["site_name_not_in_title_or_h1"] = without_rows


def _attach(target: Candidate, mentions: list[Mention]) -> None:
    for mention in mentions:
        target.add_form(mention.evidence, mention.position)
        target.mentions.append(mention)


def _anchors(con, page_ids, dom, candidates, skipped) -> None:
    homes = home_urls(con)
    reasons: Counter[str] = Counter()
    occurrences: dict[str, dict[int, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    targets: dict[str, set[str]] = defaultdict(set)
    for from_id, anchor, to_url, to_id, from_url, from_lang, to_lang in con.execute(
        "SELECT l.from_page_id, l.anchor, l.to_url, l.to_page_id, p.url, p.lang, t.lang "
        "FROM links l JOIN pages p ON p.page_id = l.from_page_id "
        "LEFT JOIN pages t ON t.page_id = l.to_page_id "
        "WHERE l.anchor IS NOT NULL AND list_contains(?, l.from_page_id) "
        "ORDER BY l.from_page_id, l.ordinal", [page_ids],
    ).fetchall():
        key = alias_key(anchor)
        reason = trivial_anchor(anchor)
        if reason is None:
            if to_id == from_id or to_url == from_url:
                reason = "anchor_self_link"
            elif to_url in homes:
                reason = "anchor_to_home"
            elif to_id is None:
                reason = "anchor_uncrawled_target"
            elif to_lang and from_lang and _primary(to_lang) != _primary(from_lang):
                reason = "anchor_language_switch"
        if reason:
            reasons[reason] += 1
            continue
        occurrences[key][from_id][anchor] += 1
        targets[key].add(to_url)
    for key, by_page in occurrences.items():
        if len(by_page) < MIN_ANCHOR_PAGES:
            reasons["anchor_texts_under_min_pages"] += 1
            continue
        target = next((candidates[(key, kind)] for kind in ATTACH_ORDER
                       if (key, kind) in candidates and kind != "concept"), None)
        if target is None:
            if len(targets[key]) > 1:
                reasons["anchor_texts_navigational"] += 1
                continue
            target = candidates.setdefault((key, "concept"), Candidate("concept", "rule"))
        for page_id, forms in by_page.items():
            evidence = forms.most_common(1)[0][0]
            context, section = dom[page_id].anchors.get(evidence, (evidence, 0))
            for form, count in forms.items():
                target.add_form(form, "anchor", count)
            target.mentions.append(Mention(page_id, "anchor", evidence, context, section,
                                           "rule", sum(forms.values())))
    skipped.update(dict(reasons))


def _language(candidate: Candidate, name: str, page_lang: dict[int, str | None]) -> str | None:
    canonical_pages = [m.page_id for m in candidate.mentions
                       if html_lib.unescape(m.evidence).strip() == name]
    for page_ids in (canonical_pages, [m.page_id for m in candidate.mentions]):
        langs = Counter(page_lang[p] for p in page_ids if page_lang.get(p))
        if langs:
            return langs.most_common(1)[0][0]
    return None


# ---------------------------------------------------------------------------
# DOM és segédek
# ---------------------------------------------------------------------------


def _page_dom(html: str) -> _PageDom:
    tree = HTMLParser(html or "")
    site_names = [value for meta in tree.css("meta[property]")
                  if (meta.attributes.get("property") or "").strip().lower() == "og:site_name"
                  and (value := (meta.attributes.get("content") or "").strip())]
    anchors: dict[str, tuple[str, int]] = {}
    schema_sections: list[int] = []
    section = 0
    root = tree.root
    for node in root.traverse() if root is not None else ():
        tag = node.tag
        if tag in HEADING_TAGS:
            if not _skipped(node):
                section += 1
        elif tag == "a" and "href" in node.attributes:
            text = anchor_text(node)
            if text and text not in anchors and not _skipped(node):
                anchors[text] = (_block_text(node)[:CONTEXT_CHARS] or text, section)
        elif tag == "script":
            kind = (node.attributes.get("type") or "").split(";")[0].strip().lower()
            raw = (node.text() or "").strip()
            if kind != "application/ld+json" or not raw:
                continue
            try:
                items = len(list(schema_items(json.loads(raw))))
            except ValueError:
                items = 1
            schema_sections.extend([section] * items)
    return _PageDom(site_names, anchors, schema_sections)


def section_texts(html: str) -> list[tuple[int, str]]:
    """A body szövege szakaszonként: (section_ordinal, szöveg). A szakaszhatár a headingek
    sorrendje, a headings táblával azonosan számolva; a script, style, noscript, template és a
    `<head>` szövege kimarad."""
    tree = HTMLParser(html or "")
    parts: dict[int, list[str]] = defaultdict(list)
    section = 0
    root = tree.root
    for node in root.traverse(include_text=True) if root is not None else ():
        if node.tag in HEADING_TAGS:
            if not _skipped(node):
                section += 1
        elif node.tag == "-text" and not any(
                ancestor.tag in _NON_BODY_TEXT for ancestor in _ancestors(node)):
            parts[section].append(node.text(deep=False) or "")
    return [(number, _WHITESPACE.sub(" ", " ".join(texts)).strip())
            for number, texts in sorted(parts.items())]


def _block_text(node: Node) -> str:
    parent = node.parent
    for ancestor in _ancestors(node):
        if ancestor.tag in _BLOCK_TAGS:
            return _text(ancestor)
    return _text(parent) if parent is not None else _text(node)


def _typed_nodes(value: object) -> Iterator[dict]:
    if isinstance(value, dict):
        if "@type" in value:
            yield value
        for child in value.values():
            yield from _typed_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _typed_nodes(child)


def _short_type(value: object) -> str:
    text = str(value).strip()
    return re.split(r"[/#:]", text)[-1] if text else text


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value] if value is not None else []


def _name(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("@value")
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str) and v.strip()), None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _existing_entities(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, str], int]:
    """(kulcs, típus) → entitás; a KG által átállított entitás a régi típusával is."""
    found: dict[tuple[str, str], int] = {}
    for entity_id, name, kind, aliases, previous in con.execute(
        "SELECT entity_id, name, type, aliases, type_changed_from FROM entities "
        "ORDER BY entity_id"
    ).fetchall():
        for form in [name, *(aliases or [])]:
            for each in (kind, previous) if previous else (kind,):
                found.setdefault((alias_key(form), each), entity_id)
    return found


def _skipped(node: Node) -> bool:
    return any(ancestor.tag in _SKIPPED_ANCESTORS for ancestor in _ancestors(node))


def _ancestors(node: Node) -> Iterator[Node]:
    parent = node.parent
    while parent is not None and parent.tag not in (None, "-undef"):
        yield parent
        parent = parent.parent


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return _WHITESPACE.sub(" ", node.text(separator=" ", strip=True) or "").strip()


def _primary(tag: str | None) -> str | None:
    return tag.strip().replace("_", "-").split("-", 1)[0].lower() if tag else None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
