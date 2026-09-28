"""LLM-es entitás-kör, blokkos bemenettel: oldalanként egy kinyerő hívás a content-régió
blokkjaira (`blocks.BLOCK_PROMPT`, `BlockExtraction`), opcionálisan egy célzott elnevezési
hívással (`naming.name_record`); az említések a `page_entities`-be, a determinisztikus kör
entitásaival összevonva.

- Bemenet: a site-ról szóló mondat (`llm.site_line`), utána a content-régió blokkjai sorszám
  szerint, `[b<sorszám>]` azonosítóval (`blocks.block_input`); a chrome-régió nem kerül bele. A
  site entitásai és a determinisztikus kör találatai sosem kerülnek bele. Hosszú oldalon
  darabonként egy hívás (`blocks.chunk_blocks`: a felső szintű headingek mentén, legfeljebb 80
  blokk, mindegyik a title-lel és a site-leíró mondattal); a darabok említései és főtémái
  összefésülve (`extract_page`).
- Szakszó-státusz: a `descriptive` concept (csak leíró kifejezés) kiesik
  (`blocks.split_descriptive`), számolva (`descriptive_concept`).
- Ellenőrzés: a `surface_form` a megadott blokk szövegében áll-e, szóhatárral, kis-nagybetű- és
  whitespace-érzéketlenül (`surface_offsets`); ha nem, kitalált: eldobva, és az
  `llm_calls.fabricated_count`, az `entity_runs.fabricated` számolja. Az első előfordulás adja a
  pozíciót.
- position: title (a title-blokk), h1 / heading (heading-blokk, a szintje szerint), különben body.
- Említés: oldal, blokk, pozíció és entitás szerint egyszer; a forrása a `mention_sources`-ban
  (source = llm, a futás, a kinyerő hívás). A `description` az LLM rövid leírása.
- Összevonás: azonos kulcsú (`alias_key`) név vagy alias, person-nél a kéttokenes név mindkét
  sorrendje → a meglévő entitás; több közül az azonos típusú, azon belül az erősebb forrású
  (schema > rule > llm). A meglévő entitás típusa és forrása nem változik, az LLM eltérő alakja
  alias lesz. Új név: új entitás, source = llm, az LLM típusával és altípusával, az oldal
  nyelvével.
- Típusjavaslat: minden elfogadott említés egy szavazat az LLM típusára (`entities.type_votes`,
  futásról futásra halmozódva); `type_suggested` a legtöbb szavazatot kapott típus, holtversenyben
  a jelenlegi. A típust ez nem írja át.
- Újrafuttatható: a feldolgozott oldal korábbi llm-forrásai ugyanattól a kinyerő modelltől
  törlődnek, a forrás nélkül maradt említés és a említés nélkül maradt llm-entitás is.
- `entity_runs`: method = llm, a kinyerő modell, a vizsgált oldalak, a hívások (kinyerés és
  elnevezés) és a költségük, az említések, a kitaláltak.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb

from aaa2.entities.blocks import BLOCK_PROMPT, block_input, chunk_blocks, split_descriptive
from aaa2.entities.dom import build_blocks, page_blocks
from aaa2.entities.llm import site_line
from aaa2.entities.naming import name_record
from aaa2.entities.rules import ATTACH_ORDER, SOURCE_STRENGTH, alias_key
from aaa2.llm.client import BudgetExceeded, LLMClient, LLMError, SchemaMismatch
from aaa2.llm.schemas import ENTITY_TYPES, BlockExtraction


@dataclass(frozen=True)
class LLMRun:
    run_id: int
    model: str
    naming_model: str | None
    pages: int
    pages_with_entities: int
    entities: int
    rows: int
    llm_calls: int
    cost_usd: float
    fabricated: int
    by_position: dict[str, int]
    skipped: dict[str, int]


def surface_offsets(surface: str, text: str) -> list[tuple[int, int]]:
    """A `surface` előfordulásai a `text`-ben (kezdet, vég), az eredeti szöveg indexeivel:
    kis-nagybetű-érzéketlenül, a szavak között bármennyi whitespace-szel, szóhatárral (előtte
    és utána nem betű)."""
    words = (surface or "").split()
    if not words:
        return []
    pattern = re.compile(r"(?<![^\W\d_])" + r"\s+".join(re.escape(w) for w in words)
                         + r"(?![^\W\d_])", re.IGNORECASE)
    return [(m.start(), m.end()) for m in pattern.finditer(text or "")]


@dataclass
class PageExtraction:
    """Egy oldal kinyerése, a darabok összefésülve: az említések dokumentum-sorrendben, a
    főtémák kulcs szerint egyszer, a hívások, és a sikertelen darabok (ok, hívás)."""

    entities: list[dict]
    primary_entities: list[str]
    call_ids: list[int]
    chunks: int
    failures: list[tuple[str, int | None]]


def extract_page(client: LLMClient, site: str, blocks: Sequence[dict],
                 page_id: int | None = None) -> PageExtraction:
    """A blokkos kinyerés egy oldalra, darabonként egy hívással; a `BudgetExceeded` továbbmegy,
    a többi hiba a sikertelen darabok közé kerül."""
    result = PageExtraction([], [], [], 0, [])
    seen: set[str] = set()
    for chunk in chunk_blocks(blocks):
        result.chunks += 1
        try:
            reply = client.extract(BlockExtraction, BLOCK_PROMPT, block_input(site, chunk),
                                   domain="entity", page_id=page_id)
        except SchemaMismatch as exc:
            result.call_ids.append(exc.call_id)
            result.failures.append(("schema_mismatch", exc.call_id))
            continue
        except BudgetExceeded:
            raise
        except LLMError:
            result.failures.append(("call_error", None))
            continue
        result.call_ids.append(reply.call_id)
        result.entities += [entity.model_dump() for entity in reply.parsed.entities]
        for name in reply.parsed.primary_entities:
            if alias_key(name) not in seen:
                seen.add(alias_key(name))
                result.primary_entities.append(name)
    return result


def run_llm(con: duckdb.DuckDBPyConnection, client: LLMClient, *,
            naming_client: LLMClient | None = None, limit: int | None = None,
            page_ids: Sequence[int] | None = None,
            clock: Callable[[], datetime] | None = None) -> LLMRun:
    """`page_ids`: csak ezek közül az alkalmas oldalak; `limit`: legfeljebb ennyi oldal;
    `naming_client`: az elnevezési hívás kliense (None: nincs elnevezés)."""
    clock = clock or _now
    started = clock()
    params: list = []
    only = ""
    if page_ids is not None:
        only = " AND list_contains(?, page_id)"
        params.append(list(page_ids))
    if limit:
        params.append(limit)
    pages = con.execute(
        "SELECT page_id, lang FROM pages "
        "WHERE status BETWEEN 200 AND 299 AND error IS NULL AND rendered_html IS NOT NULL"
        + only + " ORDER BY page_id" + (" LIMIT ?" if limit else ""), params,
    ).fetchall()
    build_blocks(con, [page_id for page_id, _ in pages])
    (run_id,) = con.execute(
        "INSERT INTO entity_runs (started_at, method, model, llm_calls) VALUES (?, 'llm', ?, 0) "
        "RETURNING run_id", [started, client.model]).fetchone()
    index = _EntityIndex(con)
    site = site_line(con) or ""
    skipped: Counter[str] = Counter()
    by_position: Counter[str] = Counter()
    call_ids: list[int] = []
    entity_ids: set[int] = set()
    pages_with: set[int] = set()
    rows = fabricated = done = 0
    for number, (page_id, lang) in enumerate(pages):
        blocks = page_blocks(con, page_id, region="content")
        if not blocks:
            skipped["no_content_blocks"] += 1
            continue
        done += 1
        try:
            page = extract_page(client, site, blocks, page_id)
        except BudgetExceeded:
            done -= 1
            skipped["budget_stopped_pages"] = len(pages) - number
            break
        call_ids += page.call_ids
        if len(page.failures) == page.chunks:
            skipped[page.failures[0][0]] += 1
            continue
        for reason, _ in page.failures:
            skipped[f"chunk_{reason}"] += 1
        extract_call = next(i for i in page.call_ids
                            if i not in {c for _, c in page.failures})
        by_id = {block["id"]: block for block in blocks}
        record = {"call_id": extract_call, "call_ids": list(page.call_ids),
                  "primary_entities": page.primary_entities, "entities": page.entities}
        if naming_client is not None:
            try:
                record = name_record(naming_client, record, by_id, page_id=page_id)
            except BudgetExceeded:
                skipped["naming_budget_stopped"] += 1
            else:
                call_ids += [i for i in record["call_ids"] if i not in page.call_ids]
                if record.get("naming_error"):
                    skipped["naming_error"] += 1
        page_rows = page_fabricated = 0
        con.begin()
        try:
            con.execute(
                "DELETE FROM mention_sources WHERE source = 'llm' AND mention_id IN "
                "(SELECT mention_id FROM page_entities WHERE page_id = ?) AND llm_call_id IN "
                "(SELECT call_id FROM llm_calls WHERE model = ?)", [page_id, client.model])
            con.execute(
                "DELETE FROM page_entities WHERE page_id = ? AND mention_id NOT IN "
                "(SELECT mention_id FROM mention_sources)", [page_id])
            written: set[int] = set()
            kept, dropped = split_descriptive(record["entities"] or [])
            skipped["descriptive_concept"] += len(dropped)
            for raw in kept:
                block = by_id.get(raw.get("block_id"))
                offsets = surface_offsets(raw.get("surface_form", ""), block["text"]) \
                    if block else []
                if not offsets:
                    page_fabricated += 1
                    continue
                start, end = offsets[0]
                entity_id = index.resolve(con, raw["canonical_name"], raw["type"],
                                          raw.get("subtype"), _primary(lang), started)
                index.vote(entity_id, raw["type"])
                position = _position(block)
                mention_id = _store_mention(con, page_id, entity_id, block, start, end,
                                            position, raw.get("description"))
                if mention_id in written:
                    continue
                written.add(mention_id)
                con.execute(
                    "INSERT INTO mention_sources (mention_id, source, run_id, llm_call_id) "
                    "VALUES (?, 'llm', ?, ?)", [mention_id, run_id, extract_call])
                by_position[position] += 1
                entity_ids.add(entity_id)
                page_rows += 1
            con.execute("UPDATE llm_calls SET fabricated_count = ? WHERE call_id = ?",
                        [page_fabricated, extract_call])
            index.write_votes(con)
            con.commit()
        except Exception:
            con.rollback()
            raise
        rows += page_rows
        fabricated += page_fabricated
        if page_rows:
            pages_with.add(page_id)
    con.execute("DELETE FROM entities WHERE source = 'llm' AND entity_id NOT IN "
                "(SELECT entity_id FROM page_entities)")
    (cost,) = con.execute("SELECT coalesce(sum(cost_usd), 0) FROM llm_calls "
                          "WHERE list_contains(?, call_id)", [call_ids]).fetchone()
    positions = dict(sorted(by_position.items()))
    reasons = {k: v for k, v in skipped.items() if v}
    con.execute(
        "UPDATE entity_runs SET finished_at = ?, pages = ?, pages_with_entities = ?, "
        "entities = ?, row_count = ?, llm_calls = ?, cost_usd = ?, fabricated = ?, "
        "by_position = ?, skipped = ? WHERE run_id = ?",
        [clock(), done, len(pages_with), len(entity_ids), rows, len(call_ids), cost, fabricated,
         json.dumps(positions), json.dumps(reasons), run_id])
    return LLMRun(run_id, client.model, naming_client.model if naming_client else None, done,
                  len(pages_with), len(entity_ids), rows, len(call_ids), cost, fabricated,
                  positions, reasons)


def _position(block: dict) -> str:
    if block["kind"] == "title":
        return "title"
    if block["kind"] == "heading":
        return "h1" if block["level"] == 1 else "heading"
    return "body"


def _store_mention(con: duckdb.DuckDBPyConnection, page_id: int, entity_id: int, block: dict,
                   start: int, end: int, position: str, description: str | None) -> int:
    """A meglévő említés (azonos oldal, blokk, pozíció, entitás) azonosítója, a leírással
    kiegészítve, ha még nincs; vagy egy új sor."""
    found = con.execute(
        "SELECT mention_id FROM page_entities WHERE page_id = ? AND block_id = ? "
        "AND char_start = ? AND char_end = ? AND entity_id = ?",
        [page_id, block["block_id"], start, end, entity_id]).fetchone()
    if found:
        con.execute("UPDATE page_entities SET description = coalesce(description, ?) "
                    "WHERE mention_id = ?", [description, found[0]])
        return found[0]
    (mention_id,) = con.execute(
        "INSERT INTO page_entities (page_id, entity_id, block_id, char_start, char_end, "
        "surface_form, position, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "RETURNING mention_id",
        [page_id, entity_id, block["block_id"], start, end, block["text"][start:end], position,
         description]).fetchone()
    return mention_id


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

    def resolve(self, con: duckdb.DuckDBPyConnection, name: str, kind: str,
                subtype: str | None, lang: str | None, created_at: datetime) -> int:
        form = name.strip()
        match = self._find(form, kind)
        if match is None:
            (entity_id,) = con.execute(
                "INSERT INTO entities (name, lang, type, subtype, aliases, source, created_at) "
                "VALUES (?, ?, ?, ?, [], 'llm', ?) RETURNING entity_id",
                [form, lang, kind, subtype, created_at],
            ).fetchone()
            self._add(entity_id, kind, "llm", [form])
            return entity_id
        entity_id, found_kind, source = match
        con.execute(
            "UPDATE entities SET aliases = list_append(coalesce(aliases, []), ?) "
            "WHERE entity_id = ? AND name <> ? AND NOT list_contains(coalesce(aliases, []), ?)",
            [form, entity_id, form, form])
        self._add(entity_id, found_kind, source, [form])
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


def _primary(tag: str | None) -> str | None:
    return tag.strip().replace("_", "-").split("-", 1)[0].lower() if tag else None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
