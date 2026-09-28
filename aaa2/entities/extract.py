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
- Kinyerés utáni lépés (`refine`, a pipeline-ban `v3.V3Step`): a rekord a mentés előtt; a
  bizonyítékai (`v3` mező) a `soft_checks`-be. Ha az ellenőrző hívás hibára fut, az oldal nem
  mentődik (`verify_error`).
- Újrafuttatható: a feldolgozott oldal korábbi llm-forrásai ugyanattól a kinyerő modelltől
  törlődnek, a forrás nélkül maradt említés is; az említés nélküli llm-entitás a futás előtt és
  után törlődik (az összevonás nem köt új említést korábbi, említés nélküli entitáshoz).
- Oldalnapló (`entity_run_pages`): állapot, okok, hívások, időtartam, a kinyerés rekordja és a
  lépés utáni rekord. Folytatás (`resume`): a modell legutóbbi futása megy tovább, a kész
  oldal kimarad, a meglévő rekord nem hív újra. A keret-őr leállítása és a költséghatár
  (`max_usd`, a futás hívásainak összege) a hátralévő oldalakat `stopped` állapotba teszi.
- `entity_runs`: method = llm, a kinyerő modell, a vizsgált oldalak, a hívások (kinyerés,
  elnevezés, ellenőrzés) és a költségük, az említések, a kitaláltak, az időtartam; az
  oldalnaplóból és az említésekből, folytatásnál az egész futásra.
- Költségbecslés a futás előtt: `estimate_llm`.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import duckdb

from aaa2.entities.blocks import BLOCK_PROMPT, block_input, chunk_blocks
from aaa2.entities.dom import build_blocks, page_blocks
from aaa2.entities.llm import site_line
from aaa2.entities.naming import name_record
from aaa2.entities.rules import ATTACH_ORDER, SOURCE_STRENGTH, alias_key
from aaa2.entities.v3 import store_soft_checks
from aaa2.llm.client import BudgetExceeded, LLMClient, LLMError, SchemaMismatch
from aaa2.llm.config import LLMConfig, Usage
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


Refine = Callable[[Mapping, int, Sequence[Mapping], str | None], dict]

FINAL = ("done", "skipped")                              # a folytatás ezeket kihagyja
ATTEMPTED = ("done", "extracted", "failed", "verify_error")


def select_pages(con: duckdb.DuckDBPyConnection, page_ids: Sequence[int] | None = None,
                 limit: int | None = None) -> list[tuple[int, str | None]]:
    """Az alkalmas (2xx, hiba nélküli, renderelt DOM-mal bíró) oldalak és a nyelvük."""
    params: list = []
    only = ""
    if page_ids is not None:
        only = " AND list_contains(?, page_id)"
        params.append(list(page_ids))
    if limit:
        params.append(limit)
    return con.execute(
        "SELECT page_id, lang FROM pages "
        "WHERE status BETWEEN 200 AND 299 AND error IS NULL AND rendered_html IS NOT NULL"
        + only + " ORDER BY page_id" + (" LIMIT ?" if limit else ""), params,
    ).fetchall()


def resumable_run(con: duckdb.DuckDBPyConnection, model: str) -> int | None:
    """A modell legutóbbi LLM-futása (a folytatás ezt viszi tovább)."""
    return con.execute("SELECT max(run_id) FROM entity_runs WHERE method = 'llm' AND model = ?",
                       [model]).fetchone()[0]


def run_pages(con: duckdb.DuckDBPyConnection, run_id: int | None) -> dict[int, dict]:
    """A futás oldalankénti naplója (`entity_run_pages`), oldal szerint."""
    if run_id is None:
        return {}
    rows = con.execute(
        "SELECT page_id, status, reasons, call_ids, extraction, refined FROM entity_run_pages "
        "WHERE run_id = ?", [run_id]).fetchall()
    return {page_id: {"status": status, "reasons": json.loads(reasons or "{}"),
                      "call_ids": list(call_ids or []),
                      "extraction": json.loads(extraction) if extraction else None,
                      "refined": json.loads(refined) if refined else None}
            for page_id, status, reasons, call_ids, extraction, refined in rows}


@dataclass
class _PageLog:
    status: str
    reasons: Counter[str] = field(default_factory=Counter)
    call_ids: list[int] = field(default_factory=list)
    chunks: int = 0
    fabricated: int = 0
    error: str | None = None
    extraction: dict | None = None
    refined: dict | None = None


def run_llm(con: duckdb.DuckDBPyConnection, client: LLMClient, *,
            naming_client: LLMClient | None = None, refine: Refine | None = None,
            save: bool = True, limit: int | None = None,
            page_ids: Sequence[int] | None = None, resume: bool = False,
            max_usd: float | None = None, clock: Callable[[], datetime] | None = None,
            monotonic: Callable[[], float] | None = None) -> LLMRun:
    """`page_ids`: csak ezek közül az alkalmas oldalak; `limit`: legfeljebb ennyi oldal;
    `naming_client`: az elnevezési hívás kliense (None: nincs elnevezés); `refine`: a kinyerés
    utáni lépés (`v3.V3Step`; None: nincs); `save`: mentés az említés- és entitástáblába;
    `resume`: a modell legutóbbi futásának folytatása; `max_usd`: a futás költséghatára (ha a
    futás hívásai elérik, a többi oldal kimarad)."""
    clock = clock or _now
    monotonic = monotonic or time.monotonic
    began = monotonic()
    started = clock()
    pages = select_pages(con, page_ids, limit)
    build_blocks(con, [page_id for page_id, _ in pages])
    run_id = resumable_run(con, client.model) if resume else None
    if run_id is None:
        (run_id,) = con.execute(
            "INSERT INTO entity_runs (started_at, method, model, llm_calls) "
            "VALUES (?, 'llm', ?, 0) RETURNING run_id", [started, client.model]).fetchone()
    previous = run_pages(con, run_id)
    con.execute("DELETE FROM entities WHERE source = 'llm' AND entity_id NOT IN "
                "(SELECT entity_id FROM page_entities)")
    index = _EntityIndex(con)
    site = site_line(con) or ""
    for number, (page_id, lang) in enumerate(pages):
        prior = previous.get(page_id) or {}
        if prior.get("status") in FINAL:
            continue
        if max_usd is not None and _run_cost(con, run_id) >= max_usd:
            _stop(con, run_id, pages[number:], previous, "cost_cap_stopped_pages", clock)
            break
        page_began = monotonic()
        blocks = page_blocks(con, page_id, region="content")
        if not blocks:
            _log(con, run_id, page_id, _PageLog("skipped", Counter(no_content_blocks=1)),
                 0.0, clock)
            continue
        log = _PageLog("failed", call_ids=list(prior.get("call_ids") or []),
                       extraction=prior.get("extraction"), refined=prior.get("refined"))
        if log.extraction is None:
            try:
                log.extraction = _extract(client, naming_client, site, blocks, page_id)
            except BudgetExceeded:
                _stop(con, run_id, pages[number:], previous, "budget_stopped_pages", clock)
                break
        extraction = log.extraction
        log.call_ids = list(dict.fromkeys([*log.call_ids, *extraction["call_ids"]]))
        log.chunks = extraction["chunks"]
        log.reasons.update(extraction["reasons"])
        if extraction["entities"] is None:
            log.extraction = None
            _log(con, run_id, page_id, log, monotonic() - page_began, clock)
            continue
        record = log.refined
        if record is None and refine is not None:
            try:
                record = refine(extraction, page_id, blocks, lang)
            except BudgetExceeded:
                log.status = "stopped"
                log.reasons = Counter(budget_stopped_pages=1)
                _log(con, run_id, page_id, log, monotonic() - page_began, clock)
                _stop(con, run_id, pages[number + 1:], previous, "budget_stopped_pages", clock)
                break
            log.call_ids = list(dict.fromkeys(
                [*log.call_ids, *(i for i in record.get("call_ids") or [] if i is not None)]))
            if record.get("verify_error"):
                log.status, log.error = "verify_error", record["verify_error"]
                log.reasons["verify_error"] += 1
                _log(con, run_id, page_id, log, monotonic() - page_began, clock)
                continue
            log.refined = record
        record = record or extraction
        log.status = "done" if save else "extracted"
        con.begin()
        try:
            log.fabricated = _store(con, run_id, page_id, lang, record, blocks, index, started,
                                    save, client.model)
            _log(con, run_id, page_id, log, monotonic() - page_began, clock)
            con.commit()
        except Exception:
            con.rollback()
            raise
    con.execute("DELETE FROM entities WHERE source = 'llm' AND entity_id NOT IN "
                "(SELECT entity_id FROM page_entities)")
    return _finish(con, run_id, client.model,
                   naming_client.model if naming_client else None, monotonic() - began, clock)


def _extract(client: LLMClient, naming_client: LLMClient | None, site: str,
             blocks: Sequence[dict], page_id: int) -> dict:
    """A kinyerés (és az elnevezés) rekordja: `entities` (None: minden darab hibás),
    `primary_entities`, `call_id` (az első sikeres kinyerő hívás), `call_ids`, `chunks`,
    `reasons` (a kimaradás okai)."""
    page = extract_page(client, site, blocks, page_id)
    failed = {call_id for _, call_id in page.failures}
    record = {"entities": None, "primary_entities": page.primary_entities, "call_id": None,
              "call_ids": list(page.call_ids), "chunks": page.chunks, "reasons": {}}
    if len(page.failures) == page.chunks:
        record["reasons"] = {page.failures[0][0]: 1}
        return record
    reasons: Counter[str] = Counter(f"chunk_{reason}" for reason, _ in page.failures)
    record.update(entities=page.entities,
                  call_id=next(i for i in page.call_ids if i not in failed))
    if naming_client is not None:
        by_id = {block["id"]: block for block in blocks}
        try:
            named = name_record(naming_client, record, by_id, page_id=page_id)
        except BudgetExceeded:
            reasons["naming_budget_stopped"] += 1
        else:
            if named.get("naming_error"):
                reasons["naming_error"] += 1
            record = {**named, "chunks": page.chunks,
                      "call_ids": list(dict.fromkeys([*record["call_ids"],
                                                      *named.get("call_ids", [])]))}
    record["reasons"] = dict(reasons)
    return record


def _store(con: duckdb.DuckDBPyConnection, run_id: int, page_id: int, lang: str | None,
           record: Mapping, blocks: Sequence[dict], index: _EntityIndex, started: datetime,
           save: bool, model: str) -> int:
    """Az oldal említései (a korábbi, azonos modelltől származó llm-források helyett), a
    típus-szavazatok és a `v3` bizonyítékai; `save` nélkül csak a kitaláltak száma. Visszaad:
    a kitalált említések száma."""
    by_id = {block["id"]: block for block in blocks}
    extract_call = record.get("call_id")
    if save:
        con.execute(
            "DELETE FROM mention_sources WHERE source = 'llm' AND mention_id IN "
            "(SELECT mention_id FROM page_entities WHERE page_id = ?) AND llm_call_id IN "
            "(SELECT call_id FROM llm_calls WHERE model = ?)", [page_id, model])
        con.execute(
            "DELETE FROM page_entities WHERE page_id = ? AND mention_id NOT IN "
            "(SELECT mention_id FROM mention_sources)", [page_id])
    fabricated = 0
    written: set[int] = set()
    entity_of: dict[str, int] = {}
    for raw in record["entities"] or []:
        block = by_id.get(raw.get("block_id"))
        offsets = surface_offsets(raw.get("surface_form", ""), block["text"]) if block else []
        if not offsets:
            fabricated += 1
            continue
        if not save:
            continue
        start, end = offsets[0]
        entity_id = index.resolve(con, raw["canonical_name"], raw["type"], raw.get("subtype"),
                                  _primary(lang), started)
        entity_of.setdefault(alias_key(raw["canonical_name"]), entity_id)
        index.vote(entity_id, raw["type"])
        mention_id = _store_mention(con, page_id, entity_id, block, start, end,
                                    _position(block), raw.get("description"))
        if mention_id in written:
            continue
        written.add(mention_id)
        con.execute(
            "INSERT INTO mention_sources (mention_id, source, run_id, llm_call_id) "
            "VALUES (?, 'llm', ?, ?)", [mention_id, run_id, extract_call])
    if extract_call is not None:
        con.execute("UPDATE llm_calls SET fabricated_count = ? WHERE call_id = ?",
                    [fabricated, extract_call])
    if save:
        index.write_votes(con)
        if record.get("v3") is not None:
            store_soft_checks(con, run_id, page_id, record["v3"], entity_of)
    return fabricated


def _log(con: duckdb.DuckDBPyConnection, run_id: int, page_id: int, log: _PageLog,
         seconds: float, clock: Callable[[], datetime]) -> None:
    con.execute(
        "INSERT OR REPLACE INTO entity_run_pages (run_id, page_id, status, reasons, call_ids, "
        "chunks, fabricated, seconds, error, extraction, refined, finished_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, page_id, log.status, json.dumps(dict(log.reasons)), log.call_ids, log.chunks,
         log.fabricated, seconds, log.error,
         json.dumps(log.extraction, ensure_ascii=False) if log.extraction else None,
         json.dumps(log.refined, ensure_ascii=False) if log.refined else None, clock()])


def _stop(con: duckdb.DuckDBPyConnection, run_id: int, pages: Sequence[tuple[int, str | None]],
          previous: Mapping[int, dict], reason: str, clock: Callable[[], datetime]) -> None:
    """A hátralévő, még nem kész oldalak `stopped` állapotba, a meglévő rekordjukkal."""
    for page_id, _ in pages:
        prior = previous.get(page_id) or {}
        if prior.get("status") in FINAL:
            continue
        _log(con, run_id, page_id,
             _PageLog("stopped", Counter({reason: 1}), list(prior.get("call_ids") or []),
                      extraction=prior.get("extraction"), refined=prior.get("refined")),
             0.0, clock)


def _run_cost(con: duckdb.DuckDBPyConnection, run_id: int) -> float:
    return con.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM llm_calls WHERE call_id IN "
        "(SELECT DISTINCT unnest(call_ids) FROM entity_run_pages WHERE run_id = ?)",
        [run_id]).fetchone()[0]


def _finish(con: duckdb.DuckDBPyConnection, run_id: int, model: str, naming_model: str | None,
            seconds: float, clock: Callable[[], datetime]) -> LLMRun:
    """A futás mérőszámai az oldalnaplóból és az említésekből (folytatásnál az egész
    futásra), az `entity_runs` sorába írva."""
    logs = con.execute("SELECT status, reasons, call_ids, fabricated FROM entity_run_pages "
                       "WHERE run_id = ?", [run_id]).fetchall()
    skipped: Counter[str] = Counter()
    call_ids: set[int] = set()
    for _, reasons, ids, _ in logs:
        skipped.update(json.loads(reasons or "{}"))
        call_ids.update(ids or [])
    done = sum(status in ATTEMPTED for status, _, _, _ in logs)
    fabricated = sum(count or 0 for _, _, _, count in logs)
    positions = dict(con.execute(
        "SELECT pe.position, count(*) FROM mention_sources ms JOIN page_entities pe "
        "USING (mention_id) WHERE ms.run_id = ? AND ms.source = 'llm' GROUP BY pe.position "
        "ORDER BY pe.position", [run_id]).fetchall())
    entities, pages_with = con.execute(
        "SELECT count(DISTINCT pe.entity_id), count(DISTINCT pe.page_id) FROM mention_sources ms "
        "JOIN page_entities pe USING (mention_id) WHERE ms.run_id = ? AND ms.source = 'llm'",
        [run_id]).fetchone()
    (cost,) = con.execute("SELECT coalesce(sum(cost_usd), 0) FROM llm_calls "
                          "WHERE list_contains(?, call_id)", [sorted(call_ids)]).fetchone()
    rows = sum(positions.values())
    reasons = {k: v for k, v in sorted(skipped.items()) if v}
    con.execute(
        "UPDATE entity_runs SET finished_at = ?, pages = ?, pages_with_entities = ?, "
        "entities = ?, row_count = ?, llm_calls = ?, cost_usd = ?, fabricated = ?, "
        "by_position = ?, skipped = ?, seconds = coalesce(seconds, 0) + ? WHERE run_id = ?",
        [clock(), done, pages_with, entities, rows, len(call_ids), cost, fabricated,
         json.dumps(positions), json.dumps(reasons), seconds, run_id])
    return LLMRun(run_id, model, naming_model, done, pages_with, entities, rows, len(call_ids),
                  cost, fabricated, positions, reasons)


# ---------------------------------------------------------------------------
# költségbecslés
# ---------------------------------------------------------------------------

ESTIMATE_CHARS_PER_TOKEN = 3.0       # a becsléshez; a keret-őr konzervatívabb (CHARS_PER_TOKEN)
EXTRACT_OUTPUT_RATIO = 2.0           # kimeneti / bemeneti token a kinyerésnél (mért: 1,1–2,1)
VERIFY_ESTIMATE = Usage(input=1500, output=600)      # egy ellenőrző hívás (csak service-tételek)


@dataclass(frozen=True)
class Estimate:
    """A futás becsült költsége: a kinyerés (és az elnevezés) darabonként, az ellenőrzés
    felső becsléssel (minden oldalon egy hívás)."""

    pages: int
    chunks: int
    tokens_in: int
    extract_usd: float
    verify_pages: int
    verify_usd: float

    @property
    def total_usd(self) -> float:
        return self.extract_usd + self.verify_usd


def estimate_llm(con: duckdb.DuckDBPyConnection, config: LLMConfig, model: str,
                 naming_model: str | None, verify_model: str | None, day: date, *,
                 limit: int | None = None, page_ids: Sequence[int] | None = None,
                 resume: bool = False) -> Estimate:
    """A `run_llm` oldalaira, ugyanazzal a kiválasztással és folytatással: darabonként a
    prompt és a bemenet karakterei `ESTIMATE_CHARS_PER_TOKEN`-nel, a kimenet
    `EXTRACT_OUTPUT_RATIO`-val; az elnevezés a kinyeréssel azonos becsléssel; az ellenőrzés
    `VERIFY_ESTIMATE` oldalanként. A meglévő kinyerés nem számít újra."""
    pages = select_pages(con, page_ids, limit)
    build_blocks(con, [page_id for page_id, _ in pages])
    previous = run_pages(con, resumable_run(con, model) if resume else None)
    site = site_line(con) or ""
    count = chunks = tokens_in = verify_pages = 0
    extract_usd = verify_usd = 0.0
    for page_id, _ in pages:
        prior = previous.get(page_id) or {}
        if prior.get("status") in FINAL:
            continue
        blocks = page_blocks(con, page_id, region="content")
        if not blocks:
            continue
        count += 1
        if prior.get("extraction") is None:
            for chunk in chunk_blocks(blocks):
                chunks += 1
                tokens = round((len(BLOCK_PROMPT) + len(block_input(site, chunk)))
                               / ESTIMATE_CHARS_PER_TOKEN)
                tokens_in += tokens
                usage = Usage(input=tokens, output=round(tokens * EXTRACT_OUTPUT_RATIO))
                extract_usd += config.cost_usd(model, usage, day)
                if naming_model:
                    extract_usd += config.cost_usd(naming_model, usage, day)
        if verify_model and prior.get("refined") is None:
            verify_pages += 1
            verify_usd += config.cost_usd(verify_model, VERIFY_ESTIMATE, day)
    return Estimate(count, chunks, tokens_in, extract_usd, verify_pages, verify_usd)


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
