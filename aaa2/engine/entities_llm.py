"""LLM-es entitás-kör: oldalanként egy hívás a title-re, a headingekre és a main contentre az
`aaa2/llm/` kliensen át; a sorok a determinisztikus kör entitásaival összevonva.

- Prompt: a `PROMPT` utasítás állandó, a tíz típus definíciójával; a site entitásai és a
  determinisztikus kör találatai sosem kerülnek bele. A bemenet (`page_input`) csak a title, a
  headingek és a main content (`MAX_INPUT_CHARS`-nál levágva).
- Séma: `PageExtraction` (kanonikus név, típus, evidence, context).
- Szűrés kódban: az evidence-nek szó szerint (whitespace-normalizálva, kis-nagybetű-
  érzéketlenül) benne kell lennie a main contentben, egy headingben vagy a title-ben; ha nincs,
  fabrikált: eldobva, és az `llm_calls.fabricated_count`, az `entity_runs.fabricated` számolja.
  A benne lévő, de nem 3–15 szavas evidence is kimarad (`evidence_length`).
- position: title, ha az evidence a title-ben áll; h1 / heading, ha egy headingben; különben body.
  section_ordinal: a heading ordinalja; bodynál a context (ha nincs meg, az evidence) szakasza a
  renderelt DOM-ban (`entities_rules.section_texts`), különben 0.
- Összevonás: azonos kulcsú (`alias_key`) név vagy alias, person-nél a kéttokenes név mindkét
  sorrendje → a meglévő entitás; több közül az azonos típusú, azon belül az erősebb forrású
  (schema > rule > llm). A meglévő entitás típusa és forrása nem változik, az LLM eltérő alakja
  alias lesz. Új név: új entitás, source = llm, az LLM típusával, az oldal nyelvével.
- Típusjavaslat: minden elfogadott sor egy szavazat az LLM típusára (`entities.type_votes`,
  futásról futásra halmozódva); `type_suggested` a legtöbb szavazatot kapott típus, holtversenyben
  a jelenlegi. A típust ez nem írja át.
- Újrafuttatható: a feldolgozott oldal korábbi llm-sorai ugyanattól a modelltől törlődnek, a sor
  nélkül maradt llm-entitás is.
- `entity_runs`: method = llm, a modell, a vizsgált oldalak, a hívások és a költségük, a sorok, a
  fabrikált sorok; az oldalankénti arány ezekből.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb
import zstandard

from aaa2.engine.entities_rules import (
    ATTACH_ORDER,
    SOURCE_STRENGTH,
    alias_key,
    section_texts,
)
from aaa2.llm.client import BudgetExceeded, LLMClient, LLMError, SchemaMismatch
from aaa2.llm.schemas import ENTITY_TYPES, ExtractedEntity, PageExtraction

MAX_INPUT_CHARS = 80_000
MIN_EVIDENCE_WORDS = 3
MAX_EVIDENCE_WORDS = 15
CONTEXT_CHARS = 500

TYPE_DEFINITIONS = {
    "brand": "a brand or trade name under which products or services are offered",
    "product": "a specific product or product line",
    "service": "a service offered to customers",
    "work": "a creative work: article, book, report, course, case study, publication",
    "event": "an event held at a given time: conference, festival, workshop, webinar",
    "person": "a named individual",
    "org": "a company, institution, association or team",
    "place": "a geographic place or venue: country, city, district, street, building",
    "tech": "software, a platform, a programming tool or a technical standard",
    "concept": "a named idea, method or discipline the text is about",
}
PROMPT = (
    "Extract the named entities mentioned in the web page below. Use only the page text.\n"
    "For each entity return:\n"
    "- name: the canonical name as written on the page, in its full form, not translated;\n"
    "- type: one of the types defined below;\n"
    "- evidence: a verbatim quote of 3 to 15 consecutive words, copied exactly from the page, "
    "that mentions the entity;\n"
    "- context: the paragraph or list item that contains the evidence, copied from the page.\n"
    "A keyword or search phrase is not an entity. A generic noun mentioned once is not a "
    "concept. Return nothing that is not on the page; return an empty list if the page names "
    "no entity.\n\n"
    "Types:\n" + "\n".join(f"- {kind}: {text}" for kind, text in TYPE_DEFINITIONS.items())
)


@dataclass(frozen=True)
class LLMRun:
    run_id: int
    model: str
    pages: int
    pages_with_entities: int
    entities: int
    rows: int
    llm_calls: int
    cost_usd: float
    fabricated: int
    by_position: dict[str, int]
    skipped: dict[str, int]


def page_input(title: str | None, headings: list[tuple[int, str]],
               main_content: str | None) -> tuple[str, bool]:
    """A hívás bemenete: title, headingek (szintjükkel), main content; és hogy le kellett-e
    vágni a main contentet."""
    text = main_content or ""
    lines = [f"TITLE: {title or ''}", "HEADINGS:"]
    lines += [f"H{level}: {heading}" for level, heading in headings if heading]
    lines += ["TEXT:", text[:MAX_INPUT_CHARS]]
    return "\n".join(lines), len(text) > MAX_INPUT_CHARS


def normalize_text(text: str | None) -> str:
    """Whitespace egy szóközzé, kisbetűsítve."""
    return " ".join((text or "").split()).casefold()


def check_evidence(entity: ExtractedEntity, sources: list[str]) -> str | None:
    """A kimaradás oka, vagy None: `fabricated`, ha az evidence nincs benne egyik (már
    normalizált) forrásban sem; `evidence_length`, ha nem 3–15 szavas."""
    evidence = normalize_text(entity.evidence)
    if not evidence or not any(evidence in source for source in sources):
        return "fabricated"
    if not MIN_EVIDENCE_WORDS <= len(evidence.split()) <= MAX_EVIDENCE_WORDS:
        return "evidence_length"
    return None


def run_llm(con: duckdb.DuckDBPyConnection, client: LLMClient, *, limit: int | None = None,
            clock: Callable[[], datetime] | None = None) -> LLMRun:
    clock = clock or _now
    started = clock()
    pages = con.execute(
        "SELECT page_id, title, lang, main_content, rendered_html FROM pages "
        "WHERE status BETWEEN 200 AND 299 AND error IS NULL AND rendered_html IS NOT NULL "
        "AND trim(coalesce(main_content, '')) <> '' ORDER BY page_id"
        + (" LIMIT ?" if limit else ""), [limit] if limit else [],
    ).fetchall()
    headings: dict[int, list[tuple[int, str, int]]] = defaultdict(list)
    for page_id, level, text, ordinal in con.execute(
        "SELECT page_id, level, text, ordinal FROM headings ORDER BY page_id, ordinal"
    ).fetchall():
        headings[page_id].append((level, text or "", ordinal))
    index = _EntityIndex(con)
    decompressor = zstandard.ZstdDecompressor()
    skipped: Counter[str] = Counter()
    by_position: Counter[str] = Counter()
    call_ids: list[int] = []
    entity_ids: set[int] = set()
    pages_with: set[int] = set()
    rows = fabricated = done = 0
    for number, (page_id, title, lang, main_content, blob) in enumerate(pages):
        page_headings = headings.get(page_id, [])
        text, truncated = page_input(title, [(level, h) for level, h, _ in page_headings],
                                     main_content)
        skipped["truncated_input"] += truncated
        done += 1
        try:
            result = client.extract(PageExtraction, PROMPT, text, domain="entity",
                                    page_id=page_id)
        except BudgetExceeded:
            done -= 1
            skipped["budget_stopped_pages"] = len(pages) - number
            break
        except SchemaMismatch as exc:
            call_ids.append(exc.call_id)
            skipped["schema_mismatch"] += 1
            continue
        except LLMError:
            skipped["call_error"] += 1
            continue
        call_ids.append(result.call_id)
        sources = [normalize_text(main_content), normalize_text(title),
                   *(normalize_text(h) for _, h, _ in page_headings)]
        sections = section_texts(decompressor.decompress(blob).decode("utf-8", "replace"))
        page_rows: dict[tuple[int, str], list] = {}
        page_fabricated = 0
        con.begin()
        try:
            con.execute(
                "DELETE FROM page_entities WHERE page_id = ? AND source = 'llm' AND llm_call_id "
                "IN (SELECT call_id FROM llm_calls WHERE model = ?)", [page_id, client.model])
            for entity in result.parsed.entities:
                reason = check_evidence(entity, sources)
                if reason == "fabricated":
                    page_fabricated += 1
                    continue
                if reason:
                    skipped[reason] += 1
                    continue
                position, section = _locate(entity, title, page_headings, sections)
                entity_id = index.resolve(con, entity, _primary(lang), started)
                index.vote(entity_id, entity.type)
                row = page_rows.get((entity_id, position))
                if row is None:
                    page_rows[(entity_id, position)] = [
                        page_id, entity_id, position, entity.evidence.strip(),
                        entity.context.strip()[:CONTEXT_CHARS] or entity.evidence.strip(),
                        section, 1, "llm", result.call_id]
                else:
                    row[6] += 1
            for row in page_rows.values():
                con.execute(
                    "INSERT INTO page_entities (page_id, entity_id, position, evidence, context, "
                    "section_ordinal, count, source, llm_call_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
                by_position[row[2]] += 1
                entity_ids.add(row[1])
            con.execute("UPDATE llm_calls SET fabricated_count = ? WHERE call_id = ?",
                        [page_fabricated, result.call_id])
            index.write_votes(con)
            con.commit()
        except Exception:
            con.rollback()
            raise
        rows += len(page_rows)
        fabricated += page_fabricated
        if page_rows:
            pages_with.add(page_id)
    con.execute("DELETE FROM entities WHERE source = 'llm' AND entity_id NOT IN "
                "(SELECT entity_id FROM page_entities)")
    (cost,) = con.execute("SELECT coalesce(sum(cost_usd), 0) FROM llm_calls "
                          "WHERE list_contains(?, call_id)", [call_ids]).fetchone()
    positions = dict(sorted(by_position.items()))
    reasons = {k: v for k, v in skipped.items() if v}
    (run_id,) = con.execute(
        "INSERT INTO entity_runs (started_at, finished_at, method, model, pages, "
        "pages_with_entities, entities, row_count, llm_calls, cost_usd, fabricated, by_position, "
        "skipped) VALUES (?, ?, 'llm', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING run_id",
        [started, clock(), client.model, done, len(pages_with), len(entity_ids), rows,
         len(call_ids), cost, fabricated, json.dumps(positions), json.dumps(reasons)],
    ).fetchone()
    return LLMRun(run_id, client.model, done, len(pages_with), len(entity_ids), rows,
                  len(call_ids), cost, fabricated, positions, reasons)


def _locate(entity: ExtractedEntity, title: str | None,
            headings: list[tuple[int, str, int]], sections: list[tuple[int, str]]
            ) -> tuple[str, int]:
    evidence = normalize_text(entity.evidence)
    if evidence in normalize_text(title):
        return "title", 0
    for level, text, ordinal in headings:
        if evidence in normalize_text(text):
            return ("h1" if level == 1 else "heading"), ordinal
    for probe in (entity.context, entity.evidence):
        compact = _compact(probe)
        if compact:
            for number, text in sections:
                if compact in _compact(text):
                    return "body", number
    return "body", 0


class _EntityIndex:
    """A meglévő entitások kulcs (név és aliasok) szerint, person-nél a kéttokenes név
    token-halmaza szerint is; és a típus-szavazataik."""

    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.by_key: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
        self.by_tokens: dict[frozenset[str], list[tuple[int, str, str]]] = defaultdict(list)
        self.types: dict[int, str] = {}
        self.votes: dict[int, Counter[str]] = defaultdict(Counter)
        self.voted: set[int] = set()
        for entity_id, name, kind, source, aliases, votes in con.execute(
            "SELECT entity_id, name, type, source, aliases, type_votes FROM entities "
            "ORDER BY entity_id"
        ).fetchall():
            self._add(entity_id, kind, source, [name, *(aliases or [])])
            self.votes[entity_id].update(json.loads(votes) if votes else {})

    def vote(self, entity_id: int, kind: str) -> None:
        self.votes[entity_id][kind] += 1
        self.voted.add(entity_id)

    def write_votes(self, con: duckdb.DuckDBPyConnection) -> None:
        """A szavazott entitások `type_votes`-a és `type_suggested`-je; a típus marad."""
        for entity_id in sorted(self.voted):
            votes = self.votes[entity_id]
            current = self.types[entity_id]
            suggested = max(votes, key=lambda t: (votes[t], t == current,
                                                  -ENTITY_TYPES.index(t)))
            con.execute("UPDATE entities SET type_votes = ?, type_suggested = ? "
                        "WHERE entity_id = ?",
                        [json.dumps(dict(sorted(votes.items()))), suggested, entity_id])
        self.voted.clear()

    def resolve(self, con: duckdb.DuckDBPyConnection, entity: ExtractedEntity,
                lang: str | None, created_at: datetime) -> int:
        form = entity.name.strip()
        match = self._find(form, entity.type)
        if match is None:
            (entity_id,) = con.execute(
                "INSERT INTO entities (name, lang, type, aliases, source, created_at) "
                "VALUES (?, ?, ?, [], 'llm', ?) RETURNING entity_id",
                [form, lang, entity.type, created_at],
            ).fetchone()
            self._add(entity_id, entity.type, "llm", [form])
            return entity_id
        entity_id, kind, source = match
        con.execute(
            "UPDATE entities SET aliases = list_append(coalesce(aliases, []), ?) "
            "WHERE entity_id = ? AND name <> ? AND NOT list_contains(coalesce(aliases, []), ?)",
            [form, entity_id, form, form])
        self._add(entity_id, kind, source, [form])
        return entity_id

    def _find(self, name: str, kind: str) -> tuple[int, str, str] | None:
        key = alias_key(name)
        matches = list(self.by_key.get(key, []))
        tokens = key.split()
        if kind == "person" and len(tokens) == 2:
            matches += [m for m in self.by_tokens.get(frozenset(tokens), []) if m not in matches]
        if not matches:
            return None
        pool = [m for m in matches if m[1] == kind] or matches
        return min(pool, key=lambda m: (SOURCE_STRENGTH.get(m[2], len(SOURCE_STRENGTH)),
                                        ATTACH_ORDER.index(m[1])))

    def _add(self, entity_id: int, kind: str, source: str, forms: list[str]) -> None:
        self.types[entity_id] = kind
        entry = (entity_id, kind, source)
        for form in forms:
            key = alias_key(form)
            if entry not in self.by_key[key]:
                self.by_key[key].append(entry)
            tokens = key.split()
            if kind == "person" and len(tokens) == 2 \
                    and entry not in self.by_tokens[frozenset(tokens)]:
                self.by_tokens[frozenset(tokens)].append(entry)


def _compact(text: str | None) -> str:
    return "".join((text or "").split()).casefold()


def _primary(tag: str | None) -> str | None:
    return tag.strip().replace("_", "-").split("-", 1)[0].lower() if tag else None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
