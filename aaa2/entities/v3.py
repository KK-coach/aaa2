"""A megközelítés v3 (M2 spec, „Megközelítés v3”): a kinyerés utáni szabály, a pipeline lépései
és a tudásbázis-egyezés az entitásokra.

A szabály (`apply_v3`), oldalanként, a kinyerés rekordján (3a prompt, elnevezés nélkül):

- megnevezett entitás (a concept és a service kivételével minden típus): változatlan;
- saját ajánlat (`service`): marad, ha szerkezeti helyen áll (title, heading, card vagy
  table_row blokk, navigáció; anchor-szöveg nem elég), és az ellenőrző hívás nem vétózza. Csak
  a szerkezeti helyű szolgáltatások mennek a hívásba (`verify.verify_record`, `select`); ha
  nincs ilyen, nincs hívás. Az elvetett service concept-jelöltként marad (a nyers tételen
  `type_changed_from = service`; a `v3` mezőben a service-nél `as_concept`, a fogalomnál
  `from_service`: `no_structure` vagy `sol_veto`);
- fogalom (`concept`): kapu és ellenőrzés nélkül marad. A bizonyíték tételenként a rekord `v3`
  mezőjében: szerkezet (`gate.structure`), ismétlődés (blokkszám), tudásbázis-egyezés
  (Wikidata / Wikipedia, `gate.KnowledgeBase`), title- vagy heading-hely, említésszám és
  fontossági sorszám (title- vagy heading-helyű előre, aztán az említésszám, aztán az első
  említés sorrendje).

A navigáció (chrome-régió) és az anchor-szövegek az oldal renderelt DOM-jából jönnek
(`dom_context`). A lépések ki-bekapcsolása: `config/pipeline.toml` (`load_pipeline`).

A rekord a szabály változatát is hordozza (`v3.rule`, `RULE`). A régebbi szabállyal tárolt
rekord újrahasznált kinyerésnél újraszámolódik (`V3Step.current`); a korábbi ellenőrző döntések
ilyenkor hívás nélkül visszajátszódnak, ha minden kiválasztott tételről van döntés (`prior`).
"""
from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb
import zstandard

from aaa2.engine import queries as crawl
from aaa2.entities import store
from aaa2.entities.dom import parse_blocks
from aaa2.entities.gate import (
    PROMINENT_KINDS,
    KnowledgeBase,
    PageContext,
    SoftItem,
    repetition,
    soft_items,
    structure,
)
from aaa2.entities.rules import alias_key
from aaa2.entities.verify import VERIFY_PROMPT, verify_input, verify_record
from aaa2.llm.config import Usage

PIPELINE_FILE = Path(__file__).parent / "config" / "pipeline.toml"
ESTIMATE_CHARS_PER_TOKEN = 3.0       # a becsléshez; a keret-őr konzervatívabb (CHARS_PER_TOKEN)
VERIFY_OUTPUT_BASE = 200             # az ellenőrző hívás kimeneti tokenje: alap
VERIFY_OUTPUT_PER_ITEM = 40          # és tételenként
STEPS = ("rules", "blocks", "extraction", "services", "concepts", "site", "knowledge",
         "save")
RULE = 2                             # 2: az elvetett service concept-jelöltként marad


@dataclass(frozen=True)
class Steps:
    rules: bool = True
    blocks: bool = True
    extraction: bool = True
    services: bool = True
    concepts: bool = True
    site: bool = True
    knowledge: bool = True
    save: bool = True


@dataclass(frozen=True)
class Pipeline:
    steps: Steps
    max_usd: float
    workers: int = 1


def load_pipeline(path: Path = PIPELINE_FILE) -> Pipeline:
    """A lépések (mind megadva, logikai értékkel), a költséghatár (> 0) és a párhuzamosan
    feldolgozott oldalak száma (`workers`, legalább 1; alapból 1)."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    steps = raw.get("steps", {})
    if set(steps) != set(STEPS) or not all(isinstance(v, bool) for v in steps.values()):
        raise ValueError(f"{path.name} [steps]: a lépések {', '.join(STEPS)}, true / false")
    limits = raw.get("limits") or {}
    max_usd = limits.get("max_usd")
    if not isinstance(max_usd, int | float) or max_usd <= 0:
        raise ValueError(f"{path.name} [limits]: max_usd > 0")
    workers = limits.get("workers", 1)
    if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
        raise ValueError(f"{path.name} [limits]: workers ≥ 1 egész")
    return Pipeline(Steps(**steps), float(max_usd), workers)


# ---------------------------------------------------------------------------
# a szabály
# ---------------------------------------------------------------------------


def service_place(item: SoftItem, page: PageContext) -> str | None:
    """A szolgáltatás szerkezeti helye; az anchor-szöveg nem számít."""
    where = structure(item, page)
    return None if where == "anchor" else where


def apply_v3(record: Mapping, page: PageContext, verifier=None,
             knowledge: Callable[[Sequence[str], str], str | None] | None = None,
             page_id: int | None = None, *, services: bool = True,
             concepts: bool = True, prior: Mapping | None = None) -> dict:
    """A rekord a v3 szabállyal, a `v3` bizonyíték-mezővel. `verifier`: az ellenőrző hívás
    kliense (None: csak a szerkezeti hely); `knowledge`: a tudásbázis (None: nincs egyezés);
    `services` / `concepts`: kikapcsolva a service-tételek változatlanok, illetve a fogalmaknak
    nincs bizonyítéka; `prior`: ugyanennek a kinyerésnek egy korábbi szabály utáni rekordja,
    az ellenőrző döntései hívás nélkül visszajátszódnak, ha minden kiválasztott tételről van
    döntés ugyanattól a modelltől."""
    blocks = page.by_id()
    items = soft_items(record.get("entities") or [], blocks)
    places = {item.key: service_place(item, page) for item in items
              if item.type == "service"} if services else {}

    def select(item: SoftItem) -> bool:
        return item.type == "service" and places.get(item.key) is not None

    if verifier is None or not services:
        out = dict(record)
    else:
        out = (replay_verify(record, items, select, prior, verifier.model)
               or verify_record(verifier, record, blocks, page_id, select=select))
    vetoed = {alias_key(name) for name, kind, keep in out.get("verify_decisions") or []
              if kind == "service" and keep is False}
    reasons = {key: "no_structure" for key, place in places.items() if place is None}
    reasons.update({key: "sol_veto" for key in vetoed if key in places})
    out["entities"] = [{**raw, "type": "concept", "subtype": None,
                        "type_changed_from": "service"}
                       if raw["type"] == "service" and alias_key(raw["canonical_name"]) in reasons
                       else raw for raw in record.get("entities") or []]
    decided = {alias_key(name): keep for name, _, keep in out.get("verify_decisions") or []}
    found = [item for item in soft_items(out["entities"], blocks)
             if item.type == "concept"] if concepts else []
    order = {id(item): index for index, item in enumerate(found)}
    prominent = {item.key: structure(item, page, PROMINENT_KINDS) is not None for item in found}
    ranked = sorted(found, key=lambda item: (not prominent[item.key], -len(item.mentions),
                                             order[id(item)]))
    out["v3"] = {
        "rule": RULE,
        "services": [{"canonical": item.canonical, "structure": places[item.key],
                      "mentions": len(item.mentions), "sol": decided.get(item.key),
                      "kept": item.key not in reasons, "as_concept": item.key in reasons}
                     for item in items if item.type == "service" and item.key in places],
        "concepts": [{"canonical": item.canonical, "rank": rank + 1,
                      "mentions": len(item.mentions), "prominent": prominent[item.key],
                      "structure": structure(item, page), "blocks": repetition(item, page),
                      "knowledge": knowledge(item.names(), page.lang) if knowledge else None,
                      "from_service": reasons.get(item.key)}
                     for rank, item in enumerate(ranked)]}
    return out


def replay_verify(record: Mapping, items: Sequence[SoftItem],
                  select: Callable[[SoftItem], bool], prior: Mapping | None,
                  model: str | None) -> dict | None:
    """A `prior` ellenőrző döntései a `record` kiválasztott tételeire, hívás nélkül, a
    `verify_record` kimenetének alakjában (`verify_replayed` = True). None, ha nincs korábbi
    rekord, más modell döntött, hiba volt, vagy valamelyik kiválasztott tételről nincs döntés."""
    if not prior or prior.get("verify_error") or prior.get("verify_model") != model:
        return None
    known = {alias_key(name): keep for name, kind, keep in prior.get("verify_decisions") or []
             if kind == "service"}
    chosen = [item for item in items if select(item)]
    if any(item.key not in known for item in chosen):
        return None
    out = dict(record)
    out.update(verify_model=model, verify_call_id=prior.get("verify_call_id"),
               verify_decisions=[(item.canonical, item.type, known[item.key])
                                 for item in chosen],
               verify_missing=sum(known[item.key] is None for item in chosen),
               verify_error=None, verify_replayed=True,
               call_ids=list(record.get("call_ids")
                             or [i for i in [record.get("call_id")] if i is not None]))
    return out


# ---------------------------------------------------------------------------
# a pipeline lépése
# ---------------------------------------------------------------------------


def dom_context(html: str, title: str | None) -> tuple[list[str], list[str]]:
    """A chrome-régió blokkjainak szövege és az oldal összes anchor-szövege."""
    chrome: list[str] = []
    anchors: list[str] = []
    for block in parse_blocks(html, title):
        if block.region == "chrome":
            chrome.append(block.text)
        anchors += block.anchors
    return chrome, anchors


def page_context(con: duckdb.DuckDBPyConnection, page_id: int, blocks: Sequence[Mapping],
                 lang: str | None) -> PageContext:
    """A tartalmi blokkok, és a renderelt DOM-ból a chrome-régió és az anchor-szövegek."""
    title, blob = crawl.rendered(con, page_id)
    html = zstandard.ZstdDecompressor().decompress(blob).decode("utf-8", "replace")
    chrome, anchors = dom_context(html, title)
    return PageContext(list(blocks), chrome, anchors, lang or "en")


def v3_fingerprint(steps: Steps, verify_model: str | None) -> str:
    """A kinyerés utáni lépés beállítása a kinyerés újrahasználatához: a lépések és az
    ellenőrző modell."""
    return f"v3:{steps.services}:{steps.concepts}:{steps.knowledge}:{verify_model}"


class V3Step:
    """A v3 szabály egy oldalra a pipeline-ban: `(rekord, oldal, blokkok, nyelv) → rekord`."""

    def __init__(self, con: duckdb.DuckDBPyConnection, steps: Steps, verifier=None,
                 knowledge: KnowledgeBase | None = None, site_lang: str | None = None):
        self.con, self.steps, self.verifier, self.knowledge = con, steps, verifier, knowledge
        self.site_lang = site_lang

    @property
    def fingerprint(self) -> str:
        return v3_fingerprint(self.steps, getattr(self.verifier, "model", None))

    @staticmethod
    def current(refined: Mapping) -> bool:
        """A tárolt szabály utáni rekord a mostani szabállyal készült."""
        return (refined.get("v3") or {}).get("rule") == RULE

    def __call__(self, record: Mapping, page_id: int, blocks: Sequence[Mapping],
                 lang: str | None, prior: Mapping | None = None) -> dict:
        page = page_context(self.con, page_id, blocks, lang or self.site_lang)
        knowledge = self.knowledge if self.steps.knowledge else None
        return apply_v3(record, page, self.verifier, knowledge, page_id,
                        services=self.steps.services, concepts=self.steps.concepts,
                        prior=prior)


def verify_items(record: Mapping, page: PageContext) -> list[SoftItem]:
    """A rekord szerkezeti helyű service-tételei: ezek mennek az ellenőrző hívásba."""
    return [item for item in soft_items(record.get("entities") or [], page.by_id())
            if item.type == "service" and service_place(item, page) is not None]


def verify_usage(record: Mapping, page: PageContext) -> Usage | None:
    """Az ellenőrző hívás becsült tokenjei a kinyerés rekordjából: a bemenet a prompt és a
    tételek (`verify.verify_input`) karakterei `ESTIMATE_CHARS_PER_TOKEN`-nel, a kimenet
    `VERIFY_OUTPUT_BASE` és tételenként `VERIFY_OUTPUT_PER_ITEM`; ha nincs szerkezeti helyű
    service, nincs hívás (None)."""
    items = verify_items(record, page)
    if not items:
        return None
    chars = len(VERIFY_PROMPT) + len(verify_input(items, page.by_id()))
    return Usage(input=round(chars / ESTIMATE_CHARS_PER_TOKEN),
                 output=VERIFY_OUTPUT_BASE + VERIFY_OUTPUT_PER_ITEM * len(items))


def store_soft_checks(con: duckdb.DuckDBPyConnection, run_id: int, page_id: int,
                      v3: Mapping, entity_of: Mapping[str, int]) -> int:
    """Az oldal `soft_checks` sorai a `v3` mezőből (a korábbiak helyett); `entity_of`: kulcs →
    a mentett entitás. Visszaad: a sorok száma."""
    store.delete_soft_checks_in_drop_page_blocks(con, page_id)
    rows = [[run_id, page_id, entity_of.get(alias_key(s["canonical"]))
             if s["kept"] or s.get("as_concept") else None,
             s["canonical"], "service", s["structure"], None, s.get("mentions"), None, None,
             None, s["sol"], s["kept"], None, None] for s in v3.get("services") or []]
    rows += [[run_id, page_id, entity_of.get(alias_key(c["canonical"])), c["canonical"],
              "concept", c["structure"], c["blocks"], c["mentions"], c["prominent"], c["rank"],
              c["knowledge"], None, True, "service" if c.get("from_service") else None,
              c.get("from_service")] for c in v3.get("concepts") or []]
    if rows:
        store.insert_soft_checks_in_store_soft_checks(con, rows)
    return len(rows)
