"""LLM-es entitás-kör, blokkos bemenettel: oldalanként egy kinyerő hívás a content-régió
blokkjaira (`blocks.BLOCK_PROMPT`, `BlockExtraction`); az említések a `page_entities`-be, a
determinisztikus kör entitásaival összevonva.

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
  mentődik (`verify_error`). Ha a lépésnek van `current` vizsgálata, és a tárolt rekord nem
  aktuális, a lépés újra lefut a korábbi rekorddal (`prior`; `refined_again`), a visszajátszott
  ellenőrzés nem új hívás (`verify_replayed`).
- Újrafuttatható: a feldolgozott oldal korábbi llm-forrásai ugyanattól a kinyerő modelltől
  törlődnek, a forrás nélkül maradt említés is; az említés nélküli llm-entitás a futás előtt és
  után törlődik (az összevonás nem köt új említést korábbi, említés nélküli entitáshoz).
- Részleges kinyerés: ha egy oldal darabjai közül legalább egy minden újrapróbálkozás után
  hibás, de van sikeres darab, az oldal állapota `partial` (nem `done`): a sikeres darabok
  említései mentődnek, a rekord `failed_chunks` mezője a hibás darabok sorszáma (0-tól), a
  futás okai között `partial_pages` számolja. A `partial` oldal nem végleges: a folytatás
  (`resume`) a hibás darabokat kéri újra, ha a rekord tárolt bemenet-hash-e (`input_hash`)
  egyezik a jelenlegivel; ha eltér (a blokkok, a prompt vagy a modellek változtak), az egész
  oldal újra kinyerődik (`partial_input_changed`). Utána a kinyerés utáni lépés is újra lefut;
  az újrahasználat (`reuse`) a részleges rekordot nem veszi át, az oldal teljes kinyerést kap.
- Oldalnapló (`entity_run_pages`): állapot, okok, hívások, időtartam, a kinyerés rekordja és a
  lépés utáni rekord. Folytatás (`resume`): a modell legutóbbi futása megy tovább, a kész
  oldal kimarad, a meglévő rekord nem hív újra. A keret-őr leállítása és a költséghatár
  (`max_usd`, a futás hívásainak összege) a hátralévő oldalakat `stopped` állapotba teszi.
- Költséghatár (`max_usd`, `_CostCap`): egy oldal csak akkor indul, ha a futás lekönyvelt
  költsége, a folyamatban lévő oldalak lefoglalt költsége és az új oldal foglalása együtt a
  határ alatt marad. A foglalás az oldal becsült költsége (`page_estimate`: a kinyerés
  darabonként, és az ellenőrzés), de legalább a futás eddigi legdrágább oldalának valós
  költsége. Ha a foglalás nem fér be, de van folyamatban lévő oldal, a futás megvárja (a kész
  oldal foglalása felszabadul, a valós költsége könyvelődik); ha nincs, megáll.
- Párhuzamosság (`workers`, `fork`): az oldalak LLM-lépései (kinyerés, ellenőrzés)
  szálanként saját kurzorral és klienssel futnak, egyszerre legfeljebb `2 × workers` oldal van
  folyamatban; a mentés és a napló a fő szálon, oldalsorrendben. A keret-őr a még futó
  hívásokat is beszámítja (`llm.client`).
- `entity_runs`: method = llm, a kinyerő modell, a vizsgált oldalak, a hívások (kinyerés,
  ellenőrzés) és a költségük, az említések, a kitaláltak, az időtartam; az
  oldalnaplóból és az említésekből, folytatásnál az egész futásra.
- Költségbecslés a futás előtt: `estimate_llm` (a kinyerés darabonként; az ellenőrzés a
  meglévő kinyerésből, csak a szerkezeti helyű service-es oldalakra).
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import time
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import duckdb

from aaa2.db.stable_json import dumps
from aaa2.engine import queries as crawl
from aaa2.entities import store
from aaa2.entities.blocks import BLOCK_PROMPT, block_input, chunk_blocks
from aaa2.entities.dom import build_blocks, page_blocks
from aaa2.entities.llm import site_line
from aaa2.entities.rules import ATTACH_ORDER, SOURCE_STRENGTH, alias_key
from aaa2.entities.v3 import (
    ESTIMATE_CHARS_PER_TOKEN,
    VERIFY_OUTPUT_BASE,
    VERIFY_OUTPUT_PER_ITEM,
    page_context,
    store_soft_checks,
    verify_usage,
)
from aaa2.llm import calls as llm_calls
from aaa2.llm.client import BudgetExceeded, LLMClient, LLMError, SchemaMismatch
from aaa2.llm.config import LLMConfig, Usage
from aaa2.llm.schemas import ENTITY_TYPES, BlockExtraction


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
    failed_chunks: list[int] = field(default_factory=list)      # a hibás darabok sorszáma


def extract_page(client: LLMClient, site: str, blocks: Sequence[dict],
                 page_id: int | None = None,
                 only: Sequence[int] | None = None) -> PageExtraction:
    """A blokkos kinyerés egy oldalra, darabonként egy hívással; a `BudgetExceeded` továbbmegy,
    a többi hiba a sikertelen darabok közé kerül (`failures`, `failed_chunks`). `only`: csak
    ezek a darabok (sorszám, 0-tól) kapnak hívást; a `chunks` így is az oldal összes darabja."""
    result = PageExtraction([], [], [], 0, [])
    seen: set[str] = set()
    for index, chunk in enumerate(chunk_blocks(blocks)):
        result.chunks += 1
        if only is not None and index not in only:
            continue
        try:
            reply = client.extract(BlockExtraction, BLOCK_PROMPT, block_input(site, chunk),
                                   domain="entity", page_id=page_id)
        except SchemaMismatch as exc:
            result.call_ids.append(exc.call_id)
            result.failures.append(("schema_mismatch", exc.call_id))
            result.failed_chunks.append(index)
            continue
        except BudgetExceeded:
            raise
        except LLMError:
            result.failures.append(("call_error", None))
            result.failed_chunks.append(index)
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
ATTEMPTED = ("done", "extracted", "partial", "failed", "verify_error")


def select_pages(con: duckdb.DuckDBPyConnection, page_ids: Sequence[int] | None = None,
                 limit: int | None = None) -> list[tuple[int, str | None]]:
    """Az alkalmas (2xx, hiba nélküli, renderelt DOM-mal bíró) oldalak és a nyelvük."""
    wanted = None if page_ids is None else set(page_ids)
    found = [(page.page_id, page.lang) for page in crawl.rendered_pages(con)
             if wanted is None or page.page_id in wanted]
    return found[:limit] if limit else found


def resumable_run(con: duckdb.DuckDBPyConnection, model: str) -> int | None:
    """A modell legutóbbi LLM-futása (a folytatás ezt viszi tovább)."""
    return store.entity_runs_for_resumable_run(con, model)[0]


def run_pages(con: duckdb.DuckDBPyConnection, run_id: int | None) -> dict[int, dict]:
    """A futás oldalankénti naplója (`entity_run_pages`), oldal szerint."""
    if run_id is None:
        return {}
    rows = con.execute(
        "SELECT page_id, status, reasons, call_ids, extraction, refined, input_hash "
        "FROM entity_run_pages WHERE run_id = ? ORDER BY ALL", [run_id]).fetchall()
    return {page_id: {"status": status, "reasons": json.loads(reasons or "{}"),
                      "call_ids": list(call_ids or []),
                      "extraction": json.loads(extraction) if extraction else None,
                      "refined": json.loads(refined) if refined else None,
                      "input_hash": input_hash}
            for page_id, status, reasons, call_ids, extraction, refined, input_hash in rows}


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
    raw_html_hash: str | None = None
    input_hash: str | None = None


class _CostCap:
    """A futás költséghatára foglalással: a lekönyvelt költség (`booked`), a folyamatban lévő
    oldalak foglalása (`reserved`) és a futás legdrágább oldala (`largest`)."""

    def __init__(self, cap: float, booked: float) -> None:
        self.cap, self.booked, self.largest = cap, booked, 0.0
        self.reserved: dict[int, float] = {}

    def admit(self, page_id: int, estimate: float) -> bool:
        """Lefoglalja az oldal költségét, ha a lekönyvelttel és a többi foglalással együtt a
        határ alatt marad; különben False (nincs foglalás). A hívás nélküli oldal (nulla
        becslés: újrahasznált vagy kész rekord) mindig indulhat."""
        amount = max(estimate, self.largest) if estimate > 0 else 0.0
        if self.booked + sum(self.reserved.values()) + amount > self.cap:
            return False
        self.reserved[page_id] = amount
        return True

    def settle(self, page_id: int, actual: float) -> None:
        """A kész oldal foglalása felszabadul, a valós költsége könyvelődik."""
        self.reserved.pop(page_id, None)
        self.booked += actual
        self.largest = max(self.largest, actual)


def page_estimate(client: LLMClient, refine: Refine | None, site: str, blocks: Sequence[Mapping],
                  prior: Mapping, day: date) -> float:
    """Az oldal LLM-lépéseinek becsült költsége (USD), ahogy az `estimate_llm` számol: a
    kinyerés a hívást igénylő darabokra (kész kinyerésnél nulla, részlegesnél a hibás darabok),
    és az ellenőrzés, ha a lépésnek van ellenőrző modellje és nincs érvényes tárolt eredménye
    (bemenete a kinyerésével azonos méretűnek véve, kimenete `v3.VERIFY_OUTPUT_BASE` és tíz
    tétel)."""
    extraction = prior.get("extraction")
    retried = set((extraction or {}).get("failed_chunks") or [])
    tokens_in, usd = 0, 0.0
    for index, chunk in enumerate(chunk_blocks(blocks)):
        tokens = round((len(BLOCK_PROMPT) + len(block_input(site, chunk)))
                       / ESTIMATE_CHARS_PER_TOKEN)
        tokens_in += tokens
        if extraction is None or index in retried:
            usd += client.config.cost_usd(
                client.model, Usage(input=tokens, output=round(tokens * EXTRACT_OUTPUT_RATIO)),
                day)
    verifier = getattr(refine, "verifier", None)
    if verifier is not None and (prior.get("refined") is None or extraction is None or retried):
        usd += verifier.config.cost_usd(
            verifier.model,
            Usage(input=tokens_in, output=VERIFY_OUTPUT_BASE + 10 * VERIFY_OUTPUT_PER_ITEM), day)
    return usd


@dataclass(frozen=True)
class Worker:
    """Egy szál LLM-lépései: a kinyerő kliens és a kinyerés utáni lépés, a szál saját
    adatbázis-kurzorához kötve (`fork`)."""
    client: LLMClient
    refine: Refine | None = None


def run_llm(con: duckdb.DuckDBPyConnection, client: LLMClient, *,
            refine: Refine | None = None,
            save: bool = True, limit: int | None = None,
            page_ids: Sequence[int] | None = None, resume: bool = False,
            max_usd: float | None = None, workers: int = 1, reuse: bool = False,
            fork: Callable[[duckdb.DuckDBPyConnection], Worker] | None = None,
            clock: Callable[[], datetime] | None = None,
            monotonic: Callable[[], float] | None = None) -> LLMRun:
    """`page_ids`: csak ezek közül az alkalmas oldalak; `limit`: legfeljebb ennyi oldal;
    `refine`: a kinyerés utáni lépés (`v3.V3Step`; None: nincs); `save`: mentés az említés- és entitástáblába;
    `resume`: a modell legutóbbi futásának folytatása; `max_usd`: a futás költséghatára
    foglalással (`_CostCap`: új oldal csak akkor indul, ha a lekönyvelt és a lefoglalt költség
    az oldaléval együtt a határ alatt marad; különben a többi oldal kimarad). `workers`: ennyi oldal LLM-lépései futnak
    egyszerre, a `fork`-kal szálanként épített klienssel (a kapcsolat kurzorán); a mentés a fő
    szálon, oldalsorrendben, így az eredmény a párhuzamosságtól független. `workers = 1` vagy
    `fork` nélkül sorban, a megadott kliensekkel. `reuse`: a változatlan oldal (azonos
    kinyerő modell és bemenet-hash, `input_fingerprint`) a modell korábbi futásának tárolt
    kinyerését és ellenőrzését kapja, hívás nélkül (inkrementális futás)."""
    clock = clock or _now
    monotonic = monotonic or time.monotonic
    began = monotonic()
    started = clock()
    pages = select_pages(con, page_ids, limit)
    build_blocks(con, [page_id for page_id, _ in pages])
    run_id = resumable_run(con, client.model) if resume else None
    if run_id is None:
        (run_id,) = store.insert_entity_runs_in_run_llm(con, started, client.model)
    previous = run_pages(con, run_id)
    store.delete_entities_in_run_llm(con)
    index = _EntityIndex(con)
    site = site_line(con) or ""
    models = input_models(client.model,
                          getattr(refine, "fingerprint", None) if refine else None)
    reusable = reusable_pages(con, client.model) if reuse else {}
    raw_hashes = {page.page_id: page.raw_html_hash for page in crawl.pages(con)}
    hashes: dict[int, str] = {}
    parallel = workers > 1 and fork is not None
    pool = queue.Queue()
    for _ in range(workers if parallel else 1):
        pool.put(fork(con.cursor()) if parallel else Worker(client, refine))

    def work(page_id: int, lang: str | None, blocks: list,
             prior: dict) -> tuple[_PageLog, str, float]:
        """Az oldal LLM-lépései egy szálon; a harmadik elem a saját ideje (a sorban állás
        nélkül)."""
        worker = pool.get()
        page_began = monotonic()
        try:
            return (*_page_llm(worker, site, page_id, lang, blocks, prior),
                    monotonic() - page_began)
        finally:
            pool.put(worker)

    executor = ThreadPoolExecutor(max_workers=workers) if parallel else None
    cap = _CostCap(max_usd, _run_cost(con, run_id)) if max_usd is not None else None
    pending: deque = deque()
    stop_reason: str | None = None
    waiting = list(pages)
    try:
        while waiting or pending:
            while waiting and stop_reason is None and len(pending) < (2 * workers if parallel
                                                                      else 1):
                page_id, lang = waiting[0]
                prior = previous.get(page_id) or {}
                if prior.get("status") in FINAL:
                    waiting.pop(0)
                    continue
                blocks = page_blocks(con, page_id, region="content")
                if not blocks:
                    waiting.pop(0)
                    _log(con, run_id, page_id,
                         _PageLog("skipped", Counter(no_content_blocks=1)), 0.0, clock)
                    continue
                hashes[page_id] = input_fingerprint(site, blocks, models)
                if ((prior.get("extraction") or {}).get("failed_chunks")
                        and prior.get("input_hash") != hashes[page_id]):
                    # a részleges rekord más bemenetből készült: az egész oldal újra megy
                    prior = {**prior, "extraction": None, "refined": None, "restarted": True}
                stored = reusable.get(page_id)
                if not prior.get("extraction") and stored and stored[0] == hashes[page_id]:
                    prior = {"extraction": stored[1], "refined": stored[2], "call_ids": [],
                             "reused": True}
                if cap is not None and not cap.admit(page_id, page_estimate(
                        client, refine, site, blocks, prior, started.date())):
                    if not pending:
                        stop_reason = "cost_cap_stopped_pages"
                    break                      # megvárja a folyamatban lévő oldalakat
                waiting.pop(0)
                args = (page_id, lang, blocks, prior)
                pending.append((page_id, lang, blocks,
                                executor.submit(work, *args) if executor else work(*args)))
            if not pending:
                break
            page_id, lang, blocks, outcome = pending.popleft()
            log, status, seconds = outcome.result() if executor else outcome
            log.raw_html_hash, log.input_hash = raw_hashes.get(page_id), hashes.get(page_id)
            if cap is not None:
                before = set((previous.get(page_id) or {}).get("call_ids") or [])
                cap.settle(page_id, llm_calls.total_cost(
                    con, [i for i in log.call_ids if i not in before]))
            if status in ("budget_extract", "budget_refine"):
                stop_reason = "budget_stopped_pages"
                if status == "budget_refine":
                    _log(con, run_id, page_id, log, seconds, clock)
                else:
                    _stop(con, run_id, [(page_id, lang)], previous, stop_reason, clock)
                continue
            if status in ("failed", "verify_error"):
                _log(con, run_id, page_id, log, seconds, clock)
                continue
            record = log.refined or log.extraction
            log.status = "partial" if status == "partial" else "done" if save else "extracted"
            store_began = monotonic()
            con.begin()
            try:
                log.fabricated = _store(con, run_id, page_id, lang, record, blocks, index,
                                        started, save, client.model)
                _log(con, run_id, page_id, log, seconds + monotonic() - store_began, clock)
                con.commit()
            except Exception:
                con.rollback()
                raise
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
    if stop_reason is not None:
        _stop(con, run_id, waiting, previous, stop_reason, clock)
    store.delete_entities_in_run_llm(con)
    return _finish(con, run_id, client.model, monotonic() - began, clock)


def clear_entities(con: duckdb.DuckDBPyConnection) -> None:
    """Az entitások és az említések törlése (`entities`, `page_entities`, `mention_sources`,
    `soft_checks`); a blokkok, a futásnapló és a tárolt kinyerés (`entity_run_pages`) marad."""
    store.delete_all_in_clear_entities(con)


def stored_records(con: duckdb.DuckDBPyConnection) -> dict[int, dict]:
    """Oldalanként a legutóbbi kész LLM-rekord: a modell, a kinyerés, a kinyerés utáni rekord,
    a darabszám és a rekord bemenet-hash-e."""
    found: dict[int, dict] = {}
    for page_id, model, extraction, refined, chunks, input_hash, raw_hash in \
            store.entity_run_pages_for_stored_records(con):
        if page_id not in found:
            found[page_id] = {"model": model, "extraction": json.loads(extraction),
                              "refined": json.loads(refined) if refined else None,
                              "chunks": chunks or 0, "input_hash": input_hash,
                              "raw_html_hash": raw_hash}
    return found


def restore_llm(con: duckdb.DuckDBPyConnection,
                models_for: Callable[[str], Sequence[str | None]] | None = None,
                clock: Callable[[], datetime] | None = None,
                monotonic: Callable[[], float] | None = None) -> LLMRun | None:
    """Az LLM-említések visszaírása a tárolt kinyerésből, hívás nélkül: oldalanként a legutóbbi
    kész rekord (`stored_records`) említései, típus-szavazatai és bizonyítékai kerülnek az
    említés- és entitástáblába, egy új LLM-futás alatt (0 hívás; az oldal oka `restored`, a
    rekordja és a bemenet-hash-e átkerül, így a következő futás újrahasználhatja).

    `models_for`: a kinyerő modellhez a bemenet-hash modelljei (`input_models`); ha adott, az
    az oldal, amelynek a tárolt bemenet-hash-e nem egyezik a mostani blokkokéval, kimarad
    (`restore_input_changed`: a rekord blokk-azonosítói már nem a mostani blokkokra mutatnak).
    Tárolt rekord nélküli oldal: `restore_no_record`. None, ha egy oldalnak sincs rekordja."""
    clock = clock or _now
    monotonic = monotonic or time.monotonic
    began = monotonic()
    started = clock()
    records = stored_records(con)
    if not records:
        return None
    model = next(iter(sorted({r["model"] for r in records.values()},
                             key=lambda m: -sum(1 for r in records.values()
                                                if r["model"] == m))))
    (run_id,) = store.insert_entity_runs_in_run_llm(con, started, model)
    index = _EntityIndex(con)
    site = site_line(con) or ""
    raw_hashes = {page.page_id: page.raw_html_hash for page in crawl.pages(con)}
    for page_id, lang in select_pages(con):
        blocks = page_blocks(con, page_id, region="content")
        if not blocks:
            _log(con, run_id, page_id, _PageLog("skipped", Counter(no_content_blocks=1)), 0.0,
                 clock)
            continue
        stored = records.get(page_id)
        if stored is None:
            _log(con, run_id, page_id, _PageLog("skipped", Counter(restore_no_record=1)), 0.0,
                 clock)
            continue
        current = (input_fingerprint(site, blocks, models_for(stored["model"]))
                   if models_for is not None else stored["input_hash"])
        if stored["input_hash"] is not None and current != stored["input_hash"]:
            _log(con, run_id, page_id, _PageLog("skipped", Counter(restore_input_changed=1)),
                 0.0, clock)
            continue
        log = _PageLog("done", Counter(restored=1), chunks=stored["chunks"],
                       extraction=stored["extraction"], refined=stored["refined"])
        log.raw_html_hash, log.input_hash = raw_hashes.get(page_id), stored["input_hash"]
        con.begin()
        try:
            log.fabricated = _store(con, run_id, page_id, lang,
                                    stored["refined"] or stored["extraction"], blocks, index,
                                    started, True, stored["model"])
            _log(con, run_id, page_id, log, 0.0, clock)
            con.commit()
        except Exception:
            con.rollback()
            raise
    return _finish(con, run_id, model, monotonic() - began, clock)


def input_models(model: str, refine_fingerprint: str | None) -> tuple[str | None, ...]:
    """A bemenet-hash modelljei: (kinyerés, None, a kinyerés utáni lépés beállítása). A
    középső hely a megszűnt elnevezési lépésé; None marad, hogy a tárolt `input_hash`-ek
    érvényesek maradjanak."""
    return (model, None, refine_fingerprint)


def input_fingerprint(site: str, blocks: Sequence[Mapping],
                      models: Sequence[str | None]) -> str:
    """A kinyerés bemenetének hash-e: a prompt, a site-leíró mondat, a content-blokkok
    (azonosító, fajta, heading-útvonal, szöveg, cellák) és a modellek (`input_models`)."""
    payload = json.dumps([BLOCK_PROMPT, site, list(models),
                          [[b.get("id"), b.get("kind"), b.get("heading_path"), b.get("text"),
                            b.get("cells")] for b in blocks]],
                         ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reusable_pages(con: duckdb.DuckDBPyConnection,
                   model: str) -> dict[int, tuple[str, dict, dict | None]]:
    """Oldalanként a modell legutóbbi sikeres (done, extracted) kinyerése bemenet-hash-sel:
    (input_hash, kinyerés, ellenőrzés utáni rekord)."""
    found: dict[int, tuple[str, dict, dict | None]] = {}
    for page_id, input_hash, extraction, refined in store.entity_runs_for_reusable_pages(con, model):
        if page_id not in found:
            found[page_id] = (input_hash, json.loads(extraction),
                              json.loads(refined) if refined else None)
    return found


def _page_llm(worker: Worker, site: str, page_id: int, lang: str | None, blocks: list,
              prior: Mapping) -> tuple[_PageLog, str]:
    """Egy oldal LLM-lépései (kinyerés, a kinyerés utáni lépés), adatbázis-írás
    nélkül a `site`-táblákba: (napló, kimenet). Kimenet: ok, partial (van hibás darab, de
    van sikeres is), failed (minden darab hibás), verify_error, budget_extract / budget_refine
    (a keret-őr megállította). A korábbi részleges kinyerésnél a hibás darabok kapnak új
    hívást, és a kinyerés utáni lépés újra lefut."""
    log = _PageLog("failed", call_ids=list(prior.get("call_ids") or []),
                   extraction=prior.get("extraction"), refined=prior.get("refined"))
    if prior.get("reused"):
        log.reasons["reused_extraction"] += 1
    if prior.get("restarted"):
        log.reasons["partial_input_changed"] += 1
    if log.extraction is not None and log.extraction.get("failed_chunks"):
        try:
            log.extraction = _extract(worker.client, site, blocks, page_id, log.extraction)
        except BudgetExceeded:
            return log, "budget_extract"
        log.refined = None
        log.reasons["partial_retried"] += 1
    elif log.extraction is None:
        try:
            log.extraction = _extract(worker.client, site, blocks, page_id)
        except BudgetExceeded:
            return log, "budget_extract"
    extraction = log.extraction
    if not prior.get("reused"):                # az újrahasznált kinyerés hívásai a régi futáséi
        log.call_ids = list(dict.fromkeys([*log.call_ids, *extraction["call_ids"]]))
    log.chunks = extraction["chunks"]
    log.reasons.update({reason: count for reason, count in extraction["reasons"].items()
                        if not (prior.get("reused") and reason == "primary_retried")})
    if extraction["entities"] is None:
        log.extraction = None
        return log, "failed"
    current = getattr(worker.refine, "current", None)
    stale = log.refined is not None and current is not None and not current(log.refined)
    if (log.refined is None or stale) and worker.refine is not None:
        try:
            record = (worker.refine(extraction, page_id, blocks, lang, prior=log.refined)
                      if stale else worker.refine(extraction, page_id, blocks, lang))
        except BudgetExceeded:
            log.status = "stopped"
            log.reasons = Counter(budget_stopped_pages=1)
            return log, "budget_refine"
        if stale:
            log.reasons["refined_again"] += 1
        old = set(extraction["call_ids"]) if prior.get("reused") else set()
        if record.get("verify_replayed"):
            log.reasons["verify_replayed"] += 1
            old.add(record.get("verify_call_id"))
        log.call_ids = list(dict.fromkeys(
            [*log.call_ids, *(i for i in record.get("call_ids") or []
                              if i is not None and i not in old)]))
        if record.get("verify_error"):
            log.status, log.error = "verify_error", record["verify_error"]
            log.reasons["verify_error"] += 1
            return log, "verify_error"
        log.refined = record
    if extraction.get("failed_chunks"):
        log.reasons["partial_pages"] += 1
        return log, "partial"
    return log, "ok"


def _extract(client: LLMClient, site: str, blocks: Sequence[dict], page_id: int,
             partial: Mapping | None = None) -> dict:
    """A kinyerés rekordja: `entities` (None: minden darab hibás),
    `primary_entities`, `call_id` (az első sikeres kinyerő hívás), `call_ids`, `chunks`,
    `reasons` (a kimaradás okai), és ha van hibás darab a sikeresek mellett, `failed_chunks`
    (a hibás darabok sorszáma). Ha a hibátlan kinyerés `primary_entities` listája üres, az
    oldal egyszer újra megy (`primary_retried`); ha a második válasz hibátlan és megnevez fő
    témát, az lép az első helyére, különben az első marad, és a rekord okai között
    `primary_empty` áll (a hívások mindkét körből számítanak).
    `partial`: egy korábbi részleges rekord; csak a hibás darabjai
    kapnak új hívást, az eredmény a korábbi sikeres darabokkal összefésülve, dokumentum-
    sorrendben. A hívó csak azonos bemenetű (`input_hash`) részleges rekordot ad át."""
    only = None if partial is None else list(partial["failed_chunks"])
    page = extract_page(client, site, blocks, page_id, only)
    retried, empty = False, False
    if partial is None and not page.failures and not page.primary_entities:
        # a kinyerés nem adott fő témát: egyszer újra megy az egész oldal
        retried = True
        try:
            again = extract_page(client, site, blocks, page_id)
        except BudgetExceeded:
            again = None
        all_calls = list(page.call_ids)
        if again is not None:
            all_calls += again.call_ids
            if not again.failures and again.primary_entities:
                page = again
        empty = not page.primary_entities
    failed = {call_id for _, call_id in page.failures}
    record = {"entities": None, "primary_entities": page.primary_entities, "call_id": None,
              "call_ids": list(page.call_ids), "chunks": page.chunks, "reasons": {}}
    if partial is None and len(page.failures) == page.chunks:
        record["reasons"] = {page.failures[0][0]: 1}
        return record
    reasons: Counter[str] = Counter(f"chunk_{reason}" for reason, _ in page.failures)
    entities, call_id = page.entities, next(
        (i for i in page.call_ids if i not in failed), None)
    if partial is not None:
        order = {block["id"]: index for index, block in enumerate(blocks)}
        entities = sorted([*partial["entities"], *page.entities],
                          key=lambda entity: order.get(entity.get("block_id"), len(order)))
        known = {alias_key(name) for name in partial["primary_entities"]}
        record["primary_entities"] = [*partial["primary_entities"],
                                      *(n for n in page.primary_entities
                                        if alias_key(n) not in known)]
        record["call_ids"] = list(dict.fromkeys([*partial["call_ids"], *page.call_ids]))
        call_id = partial.get("call_id") if partial.get("call_id") is not None else call_id
    record.update(entities=entities, call_id=call_id)
    if retried:
        record["call_ids"] = list(dict.fromkeys(all_calls))
        reasons["primary_retried"] += 1
    if empty:
        reasons["primary_empty"] += 1
    record["reasons"] = dict(reasons)
    if page.failed_chunks:
        record["failed_chunks"] = list(page.failed_chunks)
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
        store.delete_mention_sources_in_store(con, page_id, llm_calls.model_call_ids(con, model))
        store.delete_page_entities_in_store(con, page_id)
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
        store.insert_mention_sources_in_store(con, mention_id, run_id, extract_call)
    if extract_call is not None:
        llm_calls.set_fabricated_count(con, extract_call, fabricated)
    if save:
        index.write_votes(con)
        if record.get("v3") is not None:
            store_soft_checks(con, run_id, page_id, record["v3"], entity_of)
    return fabricated


def _log(con: duckdb.DuckDBPyConnection, run_id: int, page_id: int, log: _PageLog,
         seconds: float, clock: Callable[[], datetime]) -> None:
    con.execute(
        "INSERT OR REPLACE INTO entity_run_pages (run_id, page_id, status, reasons, call_ids, "
        "chunks, fabricated, seconds, error, extraction, refined, finished_at, raw_html_hash, "
        "input_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, page_id, log.status, dumps(dict(log.reasons)), log.call_ids, log.chunks,
         log.fabricated, seconds, log.error,
         json.dumps(log.extraction, ensure_ascii=False) if log.extraction else None,
         json.dumps(log.refined, ensure_ascii=False) if log.refined else None, clock(),
         log.raw_html_hash, log.input_hash])


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
    return llm_calls.total_cost(con, run_call_ids(con, run_id))


def run_call_ids(con: duckdb.DuckDBPyConnection, run_id: int) -> list[int]:
    """A futás oldalainak LLM-hívásai (`entity_run_pages.call_ids`)."""
    return [call_id for (call_id,) in con.execute(
        "SELECT DISTINCT unnest(call_ids) FROM entity_run_pages WHERE run_id = ? ORDER BY 1",
        [run_id]).fetchall() if call_id is not None]


def _finish(con: duckdb.DuckDBPyConnection, run_id: int, model: str, seconds: float,
            clock: Callable[[], datetime]) -> LLMRun:
    """A futás mérőszámai az oldalnaplóból és az említésekből (folytatásnál az egész
    futásra), az `entity_runs` sorába írva."""
    logs = con.execute("SELECT status, reasons, call_ids, fabricated FROM entity_run_pages "
                       "WHERE run_id = ? ORDER BY ALL", [run_id]).fetchall()
    skipped: Counter[str] = Counter()
    call_ids: set[int] = set()
    for _, reasons, ids, _ in logs:
        skipped.update(json.loads(reasons or "{}"))
        call_ids.update(ids or [])
    done = sum(status in ATTEMPTED for status, _, _, _ in logs)
    fabricated = sum(count or 0 for _, _, _, count in logs)
    positions = dict(store.mention_sources_for_finish_2(con, run_id))
    entities, pages_with = store.mention_sources_for_finish(con, run_id)
    cost = llm_calls.total_cost(con, call_ids)
    rows = sum(positions.values())
    reasons = {k: v for k, v in sorted(skipped.items()) if v}
    store.update_entity_runs_in_finish(con, clock(), done, pages_with, entities, rows, len(call_ids), cost, fabricated, dumps(positions), dumps(reasons), seconds, run_id)
    return LLMRun(run_id, model, done, pages_with, entities, rows, len(call_ids),
                  cost, fabricated, positions, reasons)


# ---------------------------------------------------------------------------
# költségbecslés
# ---------------------------------------------------------------------------

EXTRACT_OUTPUT_RATIO = 2.0           # kimeneti / bemeneti token a kinyerésnél (mért: 1,1–2,1)


@dataclass(frozen=True)
class Estimate:
    """A futás becsült költsége: a kinyerés darabonként; az ellenőrzés csak a
    már meglévő kinyerésű oldalakra, ahol szerkezeti helyen áll service (`verify_pages`); a még
    kinyeretlen oldalakon a kinyerés után dől el (`verify_pending`), a futás közben a
    költséghatár őrzi."""

    pages: int
    chunks: int
    tokens_in: int
    extract_usd: float
    verify_pages: int
    verify_usd: float
    verify_pending: int = 0
    reused: int = 0                  # változatlan oldal a korábbi kinyerésével, hívás nélkül

    @property
    def total_usd(self) -> float:
        return self.extract_usd + self.verify_usd


def estimate_llm(con: duckdb.DuckDBPyConnection, config: LLMConfig, model: str,
                 verify_model: str | None, day: date, *,
                 limit: int | None = None, page_ids: Sequence[int] | None = None,
                 resume: bool = False,
                 reuse_models: Sequence[str | None] | None = None) -> Estimate:
    """A `run_llm` oldalaira, ugyanazzal a kiválasztással és folytatással: darabonként a
    prompt és a bemenet karakterei `v3.ESTIMATE_CHARS_PER_TOKEN`-nel, a kimenet
    `EXTRACT_OUTPUT_RATIO`-val. Az ellenőrzés a
    meglévő kinyerésből (`v3.verify_usage`), a kinyeretlen oldalak száma külön. A meglévő
    kinyerés nem számít újra. `reuse_models`: a `run_llm` újrahasználatának modelljei
    (`input_models`); a változatlan oldal nem számít (`reused`)."""
    pages = select_pages(con, page_ids, limit)
    build_blocks(con, [page_id for page_id, _ in pages])
    previous = run_pages(con, resumable_run(con, model) if resume else None)
    site = site_line(con) or ""
    reusable = reusable_pages(con, model) if reuse_models is not None else {}
    count = chunks = tokens_in = verify_pages = verify_pending = reused = 0
    extract_usd = verify_usd = 0.0
    for page_id, lang in pages:
        prior = previous.get(page_id) or {}
        if prior.get("status") in FINAL:
            continue
        blocks = page_blocks(con, page_id, region="content")
        if not blocks:
            continue
        stored = reusable.get(page_id)
        if not prior.get("extraction") and stored and stored[0] == input_fingerprint(
                site, blocks, reuse_models):
            reused += 1
            continue
        count += 1
        extraction = prior.get("extraction")
        retried = set((extraction or {}).get("failed_chunks") or [])
        if extraction is None or retried:
            for index, chunk in enumerate(chunk_blocks(blocks)):
                if retried and index not in retried:
                    continue
                chunks += 1
                tokens = round((len(BLOCK_PROMPT) + len(block_input(site, chunk)))
                               / ESTIMATE_CHARS_PER_TOKEN)
                tokens_in += tokens
                usage = Usage(input=tokens, output=round(tokens * EXTRACT_OUTPUT_RATIO))
                extract_usd += config.cost_usd(model, usage, day)
        if not verify_model or (prior.get("refined") is not None and not retried):
            continue
        if extraction is None or retried:
            verify_pending += 1
        elif usage := verify_usage(extraction, page_context(con, page_id, blocks, lang)):
            verify_pages += 1
            verify_usd += config.cost_usd(verify_model, usage, day)
    return Estimate(count, chunks, tokens_in, extract_usd, verify_pages, verify_usd,
                    verify_pending, reused)


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
    found = store.page_entities_for_store_mention(con, page_id, block["block_id"], start, end, entity_id)
    if found:
        store.update_page_entities_in_store_mention(con, description, found[0])
        return found[0]
    (mention_id,) = store.insert_page_entities_in_store_mention(con, page_id, entity_id, block["block_id"], start, end, block["text"][start:end], position, description)
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
        for entity_id, name, kind, source, aliases, votes in store.entities_for_entityindex___init__(con):
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
            store.update_entities_in_entityindex_write_votes(con, json.dumps(dict(sorted(votes.items()))), suggested, entity_id)
        self.voted.clear()

    def resolve(self, con: duckdb.DuckDBPyConnection, name: str, kind: str,
                subtype: str | None, lang: str | None, created_at: datetime) -> int:
        form = name.strip()
        match = self._find(form, kind)
        if match is None:
            (entity_id,) = store.insert_entities_in_entityindex_resolve(con, form, lang, kind, subtype, created_at)
            self._add(entity_id, kind, "llm", [form])
            return entity_id
        entity_id, found_kind, source = match
        store.update_entities_in_entityindex_resolve(con, form, entity_id, form, form)
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
