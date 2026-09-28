"""A megközelítés v3 (M2 spec, „Megközelítés v3”): a kinyerés utáni szabály, a pipeline lépései
és a tudásbázis-egyezés az entitásokra.

A szabály (`apply_v3`), oldalanként, a kinyerés rekordján (3a prompt, elnevezés nélkül):

- megnevezett entitás (a concept és a service kivételével minden típus): változatlan;
- saját ajánlat (`service`): marad, ha szerkezeti helyen áll (title, heading, card vagy
  table_row blokk, navigáció; anchor-szöveg nem elég), és az ellenőrző hívás nem vétózza. Csak
  a szerkezeti helyű szolgáltatások mennek a hívásba (`verify.verify_record`, `select`); ha
  nincs ilyen, nincs hívás;
- fogalom (`concept`): kapu és ellenőrzés nélkül marad. A bizonyíték tételenként a rekord `v3`
  mezőjében: szerkezet (`gate.structure`), ismétlődés (blokkszám), tudásbázis-egyezés
  (Wikidata / Wikipedia, `gate.KnowledgeBase`), title- vagy heading-hely, említésszám és
  fontossági sorszám (title- vagy heading-helyű előre, aztán az említésszám, aztán az első
  említés sorrendje).

A navigáció (chrome-régió) és az anchor-szövegek az oldal renderelt DOM-jából jönnek
(`dom_context`). A lépések ki-bekapcsolása: `config/pipeline.toml` (`load_pipeline`).
"""
from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
import zstandard

from aaa2.entities.dom import parse_blocks
from aaa2.entities.gate import (
    PROMINENT_KINDS,
    KnowledgeBase,
    PageContext,
    SoftItem,
    base_language,
    repetition,
    soft_items,
    structure,
)
from aaa2.entities.rules import alias_key
from aaa2.entities.verify import verify_record

PIPELINE_FILE = Path(__file__).parent / "config" / "pipeline.toml"
STEPS = ("rules", "blocks", "extraction", "services", "concepts", "knowledge", "save")


@dataclass(frozen=True)
class Steps:
    rules: bool = True
    blocks: bool = True
    extraction: bool = True
    services: bool = True
    concepts: bool = True
    knowledge: bool = True
    save: bool = True


@dataclass(frozen=True)
class Pipeline:
    steps: Steps
    max_usd: float


def load_pipeline(path: Path = PIPELINE_FILE) -> Pipeline:
    """A lépések (mind a hét megadva, logikai értékkel) és a költséghatár (> 0)."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    steps = raw.get("steps", {})
    if set(steps) != set(STEPS) or not all(isinstance(v, bool) for v in steps.values()):
        raise ValueError(f"{path.name} [steps]: a lépések {', '.join(STEPS)}, true / false")
    max_usd = (raw.get("limits") or {}).get("max_usd")
    if not isinstance(max_usd, int | float) or max_usd <= 0:
        raise ValueError(f"{path.name} [limits]: max_usd > 0")
    return Pipeline(Steps(**steps), float(max_usd))


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
             concepts: bool = True) -> dict:
    """A rekord a v3 szabállyal, a `v3` bizonyíték-mezővel. `verifier`: az ellenőrző hívás
    kliense (None: csak a szerkezeti hely); `knowledge`: a tudásbázis (None: nincs egyezés);
    `services` / `concepts`: kikapcsolva a service-tételek változatlanok, illetve a fogalmaknak
    nincs bizonyítéka."""
    blocks = page.by_id()
    items = soft_items(record.get("entities") or [], blocks)
    places = {item.key: service_place(item, page) for item in items
              if item.type == "service"} if services else {}
    out = verify_record(verifier, record, blocks, page_id,
                        select=lambda item: item.type == "service" and places.get(item.key)
                        is not None) if verifier is not None and services else dict(record)
    vetoed = {alias_key(name) for name, kind, keep in out.get("verify_decisions") or []
              if kind == "service" and keep is False}
    dropped = {key for key, place in places.items() if place is None} | vetoed
    out["entities"] = [raw for raw in record.get("entities") or []
                       if alias_key(raw["canonical_name"]) not in dropped]
    decided = {alias_key(name): keep for name, _, keep in out.get("verify_decisions") or []}
    found = [item for item in items if item.type == "concept"] if concepts else []
    order = {id(item): index for index, item in enumerate(found)}
    prominent = {item.key: structure(item, page, PROMINENT_KINDS) is not None for item in found}
    ranked = sorted(found, key=lambda item: (not prominent[item.key], -len(item.mentions),
                                             order[id(item)]))
    out["v3"] = {
        "services": [{"canonical": item.canonical, "structure": places[item.key],
                      "mentions": len(item.mentions), "sol": decided.get(item.key),
                      "kept": item.key not in dropped}
                     for item in items if item.type == "service" and item.key in places],
        "concepts": [{"canonical": item.canonical, "rank": rank + 1,
                      "mentions": len(item.mentions), "prominent": prominent[item.key],
                      "structure": structure(item, page), "blocks": repetition(item, page),
                      "knowledge": knowledge(item.names(), page.lang) if knowledge else None}
                     for rank, item in enumerate(ranked)]}
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
    title, blob = con.execute("SELECT title, rendered_html FROM pages WHERE page_id = ?",
                              [page_id]).fetchone()
    html = zstandard.ZstdDecompressor().decompress(blob).decode("utf-8", "replace")
    chrome, anchors = dom_context(html, title)
    return PageContext(list(blocks), chrome, anchors, lang or "en")


class V3Step:
    """A v3 szabály egy oldalra a pipeline-ban: `(rekord, oldal, blokkok, nyelv) → rekord`."""

    def __init__(self, con: duckdb.DuckDBPyConnection, steps: Steps, verifier=None,
                 knowledge: KnowledgeBase | None = None, site_lang: str | None = None):
        self.con, self.steps, self.verifier, self.knowledge = con, steps, verifier, knowledge
        self.site_lang = site_lang

    def __call__(self, record: Mapping, page_id: int, blocks: Sequence[Mapping],
                 lang: str | None) -> dict:
        page = page_context(self.con, page_id, blocks, lang or self.site_lang)
        knowledge = self.knowledge if self.steps.knowledge else None
        return apply_v3(record, page, self.verifier, knowledge, page_id,
                        services=self.steps.services, concepts=self.steps.concepts)


def store_soft_checks(con: duckdb.DuckDBPyConnection, run_id: int, page_id: int,
                      v3: Mapping, entity_of: Mapping[str, int]) -> int:
    """Az oldal `soft_checks` sorai a `v3` mezőből (a korábbiak helyett); `entity_of`: kulcs →
    a mentett entitás. Visszaad: a sorok száma."""
    con.execute("DELETE FROM soft_checks WHERE page_id = ?", [page_id])
    rows = [[run_id, page_id, entity_of.get(alias_key(s["canonical"])) if s["kept"] else None,
             s["canonical"], "service", s["structure"], None, s.get("mentions"), None, None,
             None, s["sol"], s["kept"]] for s in v3.get("services") or []]
    rows += [[run_id, page_id, entity_of.get(alias_key(c["canonical"])), c["canonical"],
              "concept", c["structure"], c["blocks"], c["mentions"], c["prominent"], c["rank"],
              c["knowledge"], None, True] for c in v3.get("concepts") or []]
    if rows:
        con.executemany(
            "INSERT INTO soft_checks (run_id, page_id, entity_id, canonical, type, structure, "
            "blocks, mentions, prominent, rank, knowledge, sol, kept) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


# ---------------------------------------------------------------------------
# tudásbázis-egyezés az entitásokra
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeRun:
    entities: int
    wikidata: int
    wikipedia: int
    errors: int


def link_entities(con: duckdb.DuckDBPyConnection, knowledge: KnowledgeBase,
                  clock: Callable[[], datetime], site_lang: str | None = None) -> KnowledgeRun:
    """Az említéssel bíró, még nem ellenőrzött entitások neve a Wikidatán (címke vagy alias),
    utána a Wikipedián (cím vagy átirányítás), az entitás nyelvén (ha nincs, a site-én), majd
    angolul; nyelvenként az első találat. Ha egy kérés hibára fut, az entitás ellenőrizetlen
    marad (a következő futás újra kérdezi)."""
    rows = con.execute(
        "SELECT entity_id, name, lang FROM entities WHERE knowledge_checked_at IS NULL "
        "AND entity_id IN (SELECT entity_id FROM page_entities) ORDER BY entity_id").fetchall()
    wikidata = wikipedia = errors = 0
    for entity_id, name, lang in rows:
        before = knowledge.failures
        codes = list(dict.fromkeys([base_language(lang or site_lang), "en"]))
        qid = next((hit["id"] for code in codes if (hit := knowledge.wikidata(name, code))),
                   None)
        title = next((f"{code}:{page['title']}" for code in codes
                      if (page := knowledge.wikipedia(name, code))), None)
        if knowledge.failures > before:
            errors += 1
            continue
        con.execute("UPDATE entities SET wikidata_id = ?, wikipedia = ?, "
                    "knowledge_checked_at = ? WHERE entity_id = ?",
                    [qid, title, clock(), entity_id])
        wikidata += qid is not None
        wikipedia += title is not None
    return KnowledgeRun(len(rows), wikidata, wikipedia, errors)
