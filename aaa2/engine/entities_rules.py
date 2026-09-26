"""Determinisztikus entitás-kör, LLM nélkül: a crawl adatbázisából olvas, az `entities` és a
`page_entities` táblát tölti, és egy `entity_runs` sort ír.

Csak a sikeres (2xx, hiba nélküli, renderelt DOM-mal bíró) oldalakból dolgozik.

- schema: a JSON-LD blokkok minden `@type`-os csomópontja, a beágyazottak is, ha a típusa a
  `config/schema_types.toml` leképezésében szerepel és van szöveges `name`-je → entitás a
  leképezett típussal; position = schema, evidence = a `name` értéke, source = schema.
- brand: a site nevei → brand entitás, source = rule. A nevek: a leggyakoribb org-típusú
  schema-név, a leggyakoribb `og:site_name`, és a title-ök ismétlődő végződései (legalább
  `MIN_TITLE_PAGES` oldalon és a title-ös oldalak `MIN_TITLE_SHARE` részén; ami egy elfogadott
  név végszelete, vagy egy elfogadott névre végződik, kimarad). Sor ott, ahol a név a title-ben
  vagy az első H1-ben áll: position = title / h1, evidence = a szó szerinti részlet.
- anchor: a belső linkek anchorja, ha legalább `MIN_ANCHOR_PAGES` különböző oldalon azonos
  (kulcs szerint). Ha a kulcs egy már talált entitásé, ahhoz kerül, különben concept-jelölt;
  position = anchor, source = rule. Kimarad a betű nélküli anchor, az oldalra önmagára mutató
  link és a más nyelvű oldalra mutató link (nyelvváltó).
- alias: a név kulcsa (`alias_key`) kisbetűs, ékezet és kötőjel nélküli; egy kulcs és típus egy
  entitás, a többi írásmód az `aliases`-ben.
- context: anchornál az oldalon az első ilyen anchor legközelebbi blokk-ősének szövege (ha üres,
  mint a képes linknél, az anchor maga), schemánál a csomópont JSON-ja, title-nél és H1-nél a
  teljes szöveg. A kanonikus név a brandnél az elsőbbségi sor első neve, máshol a létrehozó
  forrás (schema, anchor) leggyakoribb alakja. section_ordinal: az elem előtti utolsó heading `headings.ordinal`-ja;
  0, ha nincs előtte heading (a `<head>` is ez); a H1 sora a saját ordinalja.
- lang: az entitás sorainak oldalain a leggyakoribb elsődleges nyelvi címke; ha nincs, a site
  első nyelve.

Újrafuttatható: a futás a korábbi schema- és rule-sorokat cseréli; a (kulcs, típus) szerint
azonos entitás az azonosítóját megtartja, a más forrású sorok és entitásaik megmaradnak.
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
from aaa2.llm.schemas import ENTITY_TYPES

SCHEMA_TYPES_FILE = Path(__file__).parent / "config" / "schema_types.toml"
MIN_ANCHOR_PAGES = 3
MIN_TITLE_PAGES = 3
MIN_TITLE_SHARE = 0.25
CONTEXT_CHARS = 500
TITLE_SEPARATORS = (" | ", " - ", " – ", " — ", " · ", " :: ", " » ", " • ")
# Egy anchor ehhez a típushoz kerül elsőnek, ha a kulcsa több talált entitásé is.
ATTACH_ORDER = ("brand", "org", "person", "product", "service", "event", "work", "place",
                "tech", "concept")

_SKIPPED_ANCESTORS = frozenset({"noscript", "template"})
_BLOCK_TAGS = frozenset({
    "p", "li", "td", "th", "dd", "dt", "figcaption", "blockquote", "address", "caption",
    "h1", "h2", "h3", "h4", "h5", "h6",
})
_DASHES = frozenset("-‐‑‒–—―−")
_TRAILING_SEPARATORS = re.compile(r"[\s|\-–—·:»•]+$")
_WHITESPACE = re.compile(r"\s+")
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
    names: list[str] = field(default_factory=list)      # kanonikus-jelöltek elsőbbségi sorban
    primary: Counter[str] = field(default_factory=Counter)   # a létrehozó forrás alakjai
    forms: Counter[str] = field(default_factory=Counter)     # minden talált alak
    mentions: list[Mention] = field(default_factory=list)

    def canonical(self) -> str:
        if self.names:
            return self.names[0]
        return (self.primary or self.forms).most_common(1)[0][0]


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
    _brands(con, pages, dom, candidates, skipped)
    _anchors(con, page_ids, dom, candidates, skipped)

    site_languages = (con.execute("SELECT languages FROM site").fetchone() or [None])[0] or []
    fallback_lang = _primary(site_languages[0]) if site_languages else None
    con.begin()
    try:
        con.execute("DELETE FROM page_entities WHERE source IN ('schema', 'rule')")
        existing = _existing_entities(con)
        rows = 0
        by_position: Counter[str] = Counter()
        entity_ids: set[int] = set()
        pages_with: set[int] = set()
        for (key, kind), candidate in candidates.items():
            if not candidate.mentions:
                continue
            name = candidate.canonical()
            aliases = sorted({form for form in candidate.forms if form != name})
            langs = Counter(page_lang[m.page_id] for m in candidate.mentions
                            if page_lang.get(m.page_id))
            lang = langs.most_common(1)[0][0] if langs else fallback_lang
            entity_id = existing.get((key, kind))
            if entity_id is None:
                (entity_id,) = con.execute(
                    "INSERT INTO entities (name, lang, type, aliases, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) RETURNING entity_id",
                    [name, lang, kind, aliases, candidate.source, started],
                ).fetchone()
            else:
                con.execute(
                    "UPDATE entities SET aliases = list_distinct(list_concat(coalesce(aliases, "
                    "[]), ?)), lang = coalesce(lang, ?) WHERE entity_id = ?",
                    [aliases, lang, entity_id],
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
            form = html_lib.unescape(name).strip()
            candidate.primary[form] += 1
            candidate.forms[form] += 1
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


def _brands(con, pages, dom, candidates, skipped) -> None:
    org_names: Counter[str] = Counter()
    for (key, kind), candidate in candidates.items():
        if kind == "org":
            org_names[candidate.forms.most_common(1)[0][0]] = len(
                {m.page_id for m in candidate.mentions})
    site_names = Counter(name for page in dom.values() for name in set(page.site_names))
    titles = [title for _, _, title, _, _, _ in pages if title]
    names = ([org_names.most_common(1)[0][0]] if org_names else []) \
        + ([site_names.most_common(1)[0][0]] if site_names else []) \
        + site_title_names(titles)
    h1_ordinals = dict(con.execute(
        "SELECT page_id, min(ordinal) FROM headings WHERE level = 1 GROUP BY page_id"
    ).fetchall())
    without_rows = []
    done: set[str] = set()
    for name in names:
        key = alias_key(name)
        if key in done:
            continue
        done.add(key)
        candidate = candidates.get((key, "brand")) or Candidate("brand", "rule")
        candidate.names.append(name)
        candidate.forms[name] += 1
        found_any = False
        for page_id, _, title, h1, _, _ in pages:
            for position, text, section in (("title", title, 0),
                                            ("h1", h1, h1_ordinals.get(page_id, 0))):
                found = find_name(text or "", key)
                if found:
                    found_any = True
                    evidence = text[found[0]:found[1]]
                    candidate.forms[evidence] += 1
                    candidate.mentions.append(
                        Mention(page_id, position, evidence, text, section, "rule"))
        if found_any or (key, "brand") in candidates:
            candidates[(key, "brand")] = candidate
        if not found_any:
            without_rows.append(name)
    if without_rows:
        skipped["brand_not_in_title_or_h1"] = without_rows


def _anchors(con, page_ids, dom, candidates, skipped) -> None:
    reasons: Counter[str] = Counter()
    occurrences: dict[str, dict[int, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    for from_id, anchor, to_url, to_id, from_url, from_lang, to_lang in con.execute(
        "SELECT l.from_page_id, l.anchor, l.to_url, l.to_page_id, p.url, p.lang, t.lang "
        "FROM links l JOIN pages p ON p.page_id = l.from_page_id "
        "LEFT JOIN pages t ON t.page_id = l.to_page_id "
        "WHERE l.anchor IS NOT NULL AND list_contains(?, l.from_page_id) "
        "ORDER BY l.from_page_id, l.ordinal", [page_ids],
    ).fetchall():
        key = alias_key(anchor)
        if not any(char.isalpha() for char in key):
            reasons["anchor_without_letter"] += 1
        elif to_id == from_id or to_url == from_url:
            reasons["anchor_self_link"] += 1
        elif to_lang and from_lang and _primary(to_lang) != _primary(from_lang):
            reasons["anchor_language_switch"] += 1
        else:
            occurrences[key][from_id][anchor] += 1
    rare = 0
    for key, by_page in occurrences.items():
        if len(by_page) < MIN_ANCHOR_PAGES:
            rare += 1
            continue
        target = next((candidates[(key, kind)] for kind in ATTACH_ORDER
                       if (key, kind) in candidates and kind != "concept"), None)
        if target is None:
            target = candidates.setdefault((key, "concept"), Candidate("concept", "rule"))
        for page_id, forms in by_page.items():
            evidence = forms.most_common(1)[0][0]
            context, section = dom[page_id].anchors.get(evidence, (evidence, 0))
            target.forms.update(forms)
            if target.type == "concept":
                target.primary.update(forms)
            target.mentions.append(Mention(page_id, "anchor", evidence, context, section,
                                           "rule", sum(forms.values())))
    if rare:
        reasons["anchor_texts_under_min_pages"] = rare
    skipped.update(dict(reasons))


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
    found: dict[tuple[str, str], int] = {}
    for entity_id, name, kind, aliases in con.execute(
        "SELECT entity_id, name, type, aliases FROM entities ORDER BY entity_id"
    ).fetchall():
        for form in [name, *(aliases or [])]:
            found.setdefault((alias_key(form), kind), entity_id)
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
