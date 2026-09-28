"""Egységteszt mesterséges oldalakon: a blokkos prompt v2 egy modellel, oldalanként egy hívás,
pontozás a referencialista ellen a nyers kimenetből.

    python -m tests.acceptance.synthetic_eval run [--model gpt-6-luna] [--tag r2]
    python -m tests.acceptance.synthetic_eval report [--model gpt-6-luna] [--tag r2]
    python -m tests.acceptance.synthetic_eval name --from r4a1 --tag r4a1n [--naming-model M]

A tesztoldalak: `tests/acceptance/synthetic/*.json` (blokkok, site-leírás, referencialista:
kötelező, opcionális, negatív). A `set` mező szerint fejlesztési (a mező nélküli oldalak, s1–s3)
vagy általánosítási (`generalization`, s4–s6); a `--set` választ közülük (alapból fejlesztési).
A valódi fejlesztési oldalak (`--set development-real`) a `tests/acceptance/dev_pages/` alól
jönnek, ugyanebben a formátumban (`tests/acceptance/annotation.py`). A bemenet a JSON blokkjaiból jön (`blocks.block_input`). A nyers
kimenet `data/compare/synthetic/<oldal>.<modell>[.<címke>].json` (a `--tag` a kört
különbözteti meg); a hívások a
`data/compare/synthetic.duckdb` `llm_calls`-ába és a főkönyvbe kerülnek. A pontozás nem ír
entitástárba, és nem használja az összevonási szabályokat.

Pontozás (`score_page`), oldalanként:

- ellenőrzés: a `surface_form` szerepel-e a megadott blokkban; ami nem, az kitalált, és kimarad;
- recall a kötelező listán, összesítve és nehézség szerint. Találat: a modell egy említése a
  referencia egy szöveg szerinti alakját ugyanabban a blokkban átfedi, és az említés kanonikus
  nevének vagy szöveg szerinti alakjának kulcsa (`alias_key`: kis-nagybetű, ékezet, kötőjel) a
  tétel kanonikus nevének vagy egy aliasának kulcsa; így a recall nem nagyobb a felismerésnél.
  A tétel `ambiguous_aliases`-e (többértelmű rövidítés, pl. GTM) a felismeréshez elég (az
  átfedés a név nélkül is számít), de a recall-találathoz és a helyes elnevezéshez nem: ott a
  kanonikus névnek vagy egy egyértelmű aliasnak kell egyeznie;
- precizitás: a modell entitásai a kanonikus név kulcsa szerint csoportosítva; a kötelező
  tételhez név szerint (blokktól függetlenül) illeszkedő csoport jó, a negatív tételhez
  illeszkedő és a referenciában nem szereplő hiba, az opcionális semleges;
- típus- és altípuspontosság a talált kötelező tételeken, a találatot adó említés csoportjának
  típusával (altípus csak ahol a referenciában van);
- forráshely: a talált kötelező tételek említéseiből hány áll a referencia szerinti blokkban;
- felismerés: a kötelező tétel egy szöveg szerinti alakját ugyanabban a blokkban átfedi a
  modell egy (nem kitalált) `surface_form`-ja (a kanonikus névtől függetlenül); elnevezés: a
  felismert tételek közül azok, amelyeknél egy átfedő említés kanonikus neve a referencia
  kanonikus neve vagy egy aliasa;
- a recall, a precizitás, a felismerés és az elnevezés külön a megnevezett tételekre (nem
  concept) és a fogalmakra (concept); a precizitás hibáit a modell típusa sorolja be;
- a megnevezett entitások precizitása a v2 definíció szerint is (`good_hard`, `wrong_hard`):
  a modell concept és service típusú csoportjai nélkül (azokat a verdiktek mérik);
- `primary_entities`, oldalanként: jó, ha a referencia összes elsődleges tételét visszaadta;
  üres referenciánál csak az üres lista jó;
- költség, token be / ki.

Elnevezés (`name`): egy meglévő kör rekordjaira a célzott elnevezési hívás
(`entities.naming`), a kinyerés újrahívása nélkül; az új rekord a `--tag` alá kerül, a
költsége a kinyerés és az elnevezés hívásainak összege (`call_ids`). A kinyerés modellje a
`--model`, az elnevezésé a `--naming-model` (alapból a `models.toml` `[pipeline]` szakasza).

Önkonzisztencia (`consensus`): több futás uniója és többsége, a felismerés szerint szavazva
(blokk + átfedő szöveg szerinti alak), a név a jelölt említéseinek leggyakoribb kanonikus neve.
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from aaa2.db.connect import DATA_DIR, connect
from aaa2.entities.blocks import check_surface, surface_spans
from aaa2.entities.extract import extract_page
from aaa2.entities.gate import SOFT_TYPES
from aaa2.entities.naming import name_record
from aaa2.entities.rules import alias_key
from aaa2.llm.client import LLMError, open_clients
from aaa2.llm.config import load_config
from aaa2.llm.schemas import BlockEntity

PAGES_DIR = Path(__file__).parent / "synthetic"
DEV_PAGES_DIR = Path(__file__).parent / "dev_pages"
OUT_DIR = Path(__file__).parent / "out" / "synthetic"
TARGETS = (                     # (mérték, küszöb): AAAV2-42, M2 spec B
    ("felismerés/megnevezett", 0.95), ("felismerés/fogalom", 0.90),
    ("precizitás/megnevezett", 0.90), ("precizitás/fogalom", 0.90), ("recall", 0.90),
)


DEVELOPMENT = "development"
REAL = "development-real"
SETS = (DEVELOPMENT, "generalization", REAL, "all")


def load_pages(pages_dir: Path | None = None, page_set: str = DEVELOPMENT) -> list[dict]:
    """A `page_set` oldalai (`all`: a mesterségesek mind); a `set` mező nélküli oldal
    fejlesztési. A valódi fejlesztési oldalak (`development-real`) a `dev_pages/` alól."""
    pages_dir = pages_dir or (DEV_PAGES_DIR if page_set == REAL else PAGES_DIR)
    pages = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(pages_dir.glob("*.json"))]
    return [page for page in pages
            if page_set == "all" or page.get("set", DEVELOPMENT) == page_set]


# ---------------------------------------------------------------------------
# futás
# ---------------------------------------------------------------------------


def output_path(data_dir: Path, page_id: str, model: str, tag: str = "") -> Path:
    return data_dir / "synthetic" / f"{page_id}.{model}{'.' + tag if tag else ''}.json"


def _client(con, model: str):
    provider = load_config().provider_of(model)
    if provider is None:
        raise SystemExit(f"a {model} nincs a konfigurált modellek között")
    clients, skipped = open_clients(con, models={provider: model})
    if provider not in clients:
        raise SystemExit(f"{model}: {skipped.get(provider)}")
    return clients[provider]


def run(model: str, data_dir: Path, pages: list[dict], tag: str = "") -> None:
    (data_dir / "synthetic").mkdir(parents=True, exist_ok=True)
    con = connect(data_dir / "synthetic.duckdb")
    try:
        client = _client(con, model)
        for page in pages:
            record = call(client, page)
            output_path(data_dir, page["page_id"], model, tag).write_text(
                json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{page['page_id']}: {model}: "
                  + (record["error"] or f"{len(record['entities'])} említés, "
                     f"{len(record['primary_entities'])} elsődleges"))
    finally:
        con.close()


def name_run(model: str, data_dir: Path, pages: list[dict], source: str, tag: str,
             naming_model: str) -> None:
    """A `source` kör (`model` kinyerése) rekordjaira az elnevezési hívás a `naming_model`-lel;
    az eredmény a `tag` alá kerül."""
    con = connect(data_dir / "synthetic.duckdb")
    try:
        client = _client(con, naming_model)
        for page in pages:
            path = output_path(data_dir, page["page_id"], model, source)
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("entities") is None:
                named = {**record, "call_ids": [i for i in [record.get("call_id")] if i]}
            else:
                named = name_record(client, record, {b["id"]: b for b in page["blocks"]})
            output_path(data_dir, page["page_id"], model, tag).write_text(
                json.dumps(named, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{page['page_id']}: {source} → {tag}: "
                  + (named.get("naming_error") or record.get("error")
                     or f"{len(record['entities'])} → {len(named['entities'])} említés, "
                        f"válasz nélkül {named['naming_missing']}"))
    finally:
        con.close()


def call(client, page: dict) -> dict:
    """Egy oldal kinyerése, hosszú oldalon darabolva (`extract.extract_page`). A rekord
    `call_id`-je az első sikeres hívás, a `call_ids` mind (a költséghez), `chunks` a darabok
    száma, a sikertelen darabok a `chunk_errors`-ban; `error` csak akkor, ha egy darab sem
    sikerült."""
    record = {"page_id": page["page_id"], "model": client.model, "call_id": None,
              "call_ids": [], "chunks": 0, "chunk_errors": [], "primary_entities": None,
              "entities": None, "error": None}
    try:
        result = extract_page(client, page["site_description"], page["blocks"])
    except LLMError as exc:
        record.update(error=f"call_error: {exc}"[:500])
        return record
    failed = {call_id for _, call_id in result.failures}
    succeeded = [i for i in result.call_ids if i not in failed]
    record.update(call_ids=result.call_ids, chunks=result.chunks,
                  chunk_errors=[f"{reason}: call {call_id}" for reason, call_id in result.failures])
    if not succeeded:
        record.update(call_id=result.call_ids[0] if result.call_ids else None,
                      error="; ".join(record["chunk_errors"]))
        return record
    record.update(call_id=succeeded[0], primary_entities=result.primary_entities,
                  entities=result.entities)
    return record


# ---------------------------------------------------------------------------
# pontozás
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    canonical: str
    type: str | None
    subtype: str | None
    difficulty: str | None
    keys: frozenset[str]
    blocks: frozenset[str]
    types: frozenset[str] = frozenset()      # a helyes típusok: `acceptable_types`, vagy a típus
    subtypes: frozenset[str] = frozenset()   # a helyes altípusok: `acceptable_subtypes`, vagy az
                                             # altípus
    naming_keys: frozenset[str] = frozenset()  # a helyes elnevezés kulcsai: `keys` az
                                               # `ambiguous_aliases` nélkül


def _items(entries: list[dict]) -> list[Item]:
    items = []
    for e in entries:
        keys = frozenset(alias_key(n) for n in [e["canonical"], *e.get("aliases", [])])
        ambiguous = frozenset(alias_key(n) for n in e.get("ambiguous_aliases", []))
        items.append(Item(
            e["canonical"], e.get("type"), e.get("subtype"), e.get("difficulty"), keys,
            frozenset(s["block"] for s in e.get("surface_forms", [])),
            frozenset(e.get("acceptable_types") or [e.get("type")]),
            frozenset(alias_key(s) for s in e.get("acceptable_subtypes")
                      or ([e["subtype"]] if e.get("subtype") else [])),
            keys - ambiguous))
    return items


@dataclass
class Group:
    """A modell egy entitása: az azonos kanonikus kulcsú (nem kitalált) említések."""

    canonical: str
    type: str
    subtype: str | None
    surfaces: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)

    def keys(self) -> set[str]:
        return {alias_key(self.canonical), *(alias_key(s) for s in self.surfaces)}


@dataclass
class PageScore:
    page_id: str
    required: int = 0
    found: int = 0
    by_difficulty: dict = field(default_factory=dict)       # nehézség → [talált, összes]
    named: int = 0                                          # nem concept típusú kötelező
    named_found: int = 0
    concepts: int = 0                                       # concept típusú kötelező
    concepts_found: int = 0
    recognized_named: int = 0
    recognized_concepts: int = 0
    well_named_named: int = 0                               # felismert és jól elnevezett
    well_named_concepts: int = 0
    good: int = 0
    good_named: int = 0
    good_concepts: int = 0
    wrong: int = 0
    wrong_named: int = 0                                    # a modell típusa szerint
    wrong_concepts: int = 0
    good_hard: int = 0                                      # a modell típusa nem concept vagy
    wrong_hard: int = 0                                     # service (megnevezett entitás, v2)
    neutral: int = 0
    type_right: int = 0
    subtype_right: int = 0
    subtype_total: int = 0
    block_right: int = 0
    block_total: int = 0
    primary_found: int = 0                                  # oldal, ahol az elsődleges jó
    primary_total: int = 0
    mentions: int = 0
    missed: list = field(default_factory=list)              # (kanonikus, nehézség, típus, ok)
    wrong_types: list = field(default_factory=list)         # (kanonikus, referencia, modell)
    wrong_subtypes: list = field(default_factory=list)
    wrong_blocks: list = field(default_factory=list)        # (kanonikus, blokk)
    negatives_hit: list = field(default_factory=list)       # (modell neve, negatív tétel)
    unlisted: list = field(default_factory=list)            # (modell neve, típus)
    false_hits: list = field(default_factory=list)          # (név, típus, ok)
    fabricated: list = field(default_factory=list)          # (kanonikus, szöveg, blokk)
    extra_primary: list = field(default_factory=list)
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def precision_total(self) -> int:
        return self.good + self.wrong

    @property
    def recognized(self) -> int:
        return self.recognized_named + self.recognized_concepts

    @property
    def well_named(self) -> int:
        return self.well_named_named + self.well_named_concepts


RECOGNITION = "felismerési"
NAMING = "elnevezési"


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def score_page(page: dict, record: dict, call_row: tuple | None = None) -> PageScore:
    """`call_row`: (cost_usd, tokens_in, tokens_out) az `llm_calls`-ból (a rekord hívásainak
    összege), ha van."""
    gold = page["gold"]
    required, optional = _items(gold["entities"]), _items(gold.get("optional", []))
    negatives = {alias_key(n["text"]): n["text"] for n in gold.get("negatives", [])}
    blocks = {b["id"]: b for b in page["blocks"]}
    score = PageScore(page["page_id"], required=len(required))
    if call_row:
        score.cost_usd, score.tokens_in, score.tokens_out = (call_row[0] or 0.0,
                                                             call_row[1] or 0, call_row[2] or 0)
    groups: dict[str, Group] = {}
    spans: list[tuple[str, tuple[int, int], str, str]] = []  # (blokk, hely, kanonikus és
    #                                                           szöveg szerinti kulcs)
    for raw in record.get("entities") or []:
        entity = BlockEntity.model_construct(**raw)          # validálás nélkül: a régi kör is
        score.mentions += 1
        if not check_surface(entity, blocks):
            score.fabricated.append((entity.canonical_name, entity.surface_form, entity.block_id))
            continue
        key = alias_key(entity.canonical_name)
        group = groups.setdefault(key, Group(entity.canonical_name, entity.type, entity.subtype))
        group.surfaces.append(entity.surface_form)
        group.blocks.append(entity.block_id)
        spans += [(entity.block_id, span, key, alias_key(entity.surface_form))
                  for span in surface_spans(entity.surface_form, blocks[entity.block_id])]
    for group in groups.values():
        keys = group.keys()
        target = next((item for item in required if item.keys & keys), None)
        if target is not None:
            score.good += 1
            score.good_hard += group.type not in SOFT_TYPES
            if target.type == "concept":
                score.good_concepts += 1
            else:
                score.good_named += 1
            score.block_total += len(group.blocks)
            right = [b for b in group.blocks if b in target.blocks]
            score.block_right += len(right)
            score.wrong_blocks += [(target.canonical, b) for b in group.blocks
                                   if b not in target.blocks]
            continue
        if any(item.keys & keys for item in optional):
            score.neutral += 1
            continue
        score.wrong += 1
        score.wrong_hard += group.type not in SOFT_TYPES
        if group.type == "concept":
            score.wrong_concepts += 1
        else:
            score.wrong_named += 1
        if negative := next((negatives[k] for k in keys if k in negatives), None):
            score.negatives_hit.append((group.canonical, negative))
            score.false_hits.append((group.canonical, group.type, f"negatív „{negative}”"))
        else:
            score.unlisted.append((group.canonical, group.type))
            score.false_hits.append((group.canonical, group.type, "referencián kívül"))
    for item, entry in zip(required, gold["entities"], strict=True):
        tally = score.by_difficulty.setdefault(item.difficulty or "?", [0, 0])
        tally[1] += 1
        concept = item.type == "concept"
        score.concepts += concept
        score.named += not concept
        gold_spans = [(s["block"], span) for s in entry.get("surface_forms", [])
                      if s["block"] in blocks
                      for span in surface_spans(s["text"], blocks[s["block"]])]
        overlapping = [(key, surface_key) for block, span, key, surface_key in spans
                       if any(block == gb and _overlap(span, gs) for gb, gs in gold_spans)]
        recognized = bool(overlapping)
        well_named = any(key in item.naming_keys for key, _ in overlapping)
        hit = next((key for key, surface_key in overlapping
                    if key in item.naming_keys or surface_key in item.naming_keys), None)
        if concept:
            score.recognized_concepts += recognized
            score.well_named_concepts += well_named
        else:
            score.recognized_named += recognized
            score.well_named_named += well_named
        if hit is None:
            score.missed.append((item.canonical, item.difficulty, item.type,
                                 NAMING if recognized else RECOGNITION))
            continue
        group = groups[hit]
        score.found += 1
        score.concepts_found += concept
        score.named_found += not concept
        tally[0] += 1
        if group.type in item.types:
            score.type_right += 1
        else:
            score.wrong_types.append((item.canonical, item.type, group.type))
        if item.subtype:
            score.subtype_total += 1
            if alias_key(group.subtype or "") in item.subtypes:
                score.subtype_right += 1
            else:
                score.wrong_subtypes.append((item.canonical, item.subtype, group.subtype))
    model_primary = {alias_key(p) for p in record.get("primary_entities") or []}
    gold_primary = []
    for name in gold.get("primary_entities", []):
        item = next((i for i in required if alias_key(name) in i.keys), None)
        gold_primary.append(item.keys if item else frozenset({alias_key(name)}))
    score.primary_total = 1
    score.primary_found = int(all(keys & model_primary for keys in gold_primary)
                              if gold_primary else not model_primary)
    every_gold_key = set().union(*gold_primary) if gold_primary else set()
    score.extra_primary = [p for p in record.get("primary_entities") or []
                           if alias_key(p) not in every_gold_key]
    return score


def _where(raw: dict, gold: dict, required: list[Item], optional: list[Item],
           negatives: dict[str, str], blocks: dict) -> str:
    """Egy kiesett említés helye a referenciában: kötelező vagy opcionális tétel (név szerint,
    vagy ha a szöveg szerinti alakja egy tételét ugyanabban a blokkban átfedi), negatív, vagy a
    referencián kívül."""
    keys = {alias_key(raw["canonical_name"]), alias_key(raw.get("surface_form", ""))}
    block = blocks.get(raw.get("block_id"))
    own = surface_spans(raw.get("surface_form", ""), block) if block else []
    for label, items, entries in (("kötelező", required, gold["entities"]),
                                  ("opcionális", optional, gold.get("optional", []))):
        for item, entry in zip(items, entries, strict=True):
            overlap = any(form["block"] == raw.get("block_id") and any(
                _overlap(a, b) for a in own
                for b in surface_spans(form["text"], blocks[form["block"]]))
                for form in entry.get("surface_forms", []) if form["block"] in blocks)
            if item.keys & keys or overlap:
                return f"{label}: {item.canonical}"
    if negative := next((negatives[k] for k in keys if k in negatives), None):
        return f"negatív „{negative}”"
    return "referencián kívül"


def consensus(page: dict, records: list[dict], majority: bool) -> dict:
    """Több futás rekordja egyben, a felismerés szerint szavazva (nem a kanonikus név szerint).

    - Említés: egy futás nem kitalált entitása, a blokkja és a szöveg szerinti alakjának
      helyei abban a blokkban (`surface_spans`). Két említés ugyanazt ismeri fel, ha
      ugyanabban a blokkban átfedik egymást.
    - Szavazat: hány futásnak van az említéssel átfedő említése. Unió: legalább 1; többség: a
      futások több mint fele (3-ból 2).
    - Az átfedő említések közül a pontosan azonos helyűek egy jelöltet alkotnak. Jelöltek, sorban:
      több futás adta; rövidebb; korábbi. Egy jelölt akkor marad, ha megvan a szavazata, és nem
      fed át egy már megtartottal.
    - A megtartott jelölt neve a leggyakoribb kanonikus kulcs a jelölt említései közül (a
      típus és az altípus az első ilyen említésé).
    - A kitalált említés egyikbe sem kerül. Az elsődleges lista a kanonikus kulcs szerint
      szavaz, mint eddig."""
    blocks = {b["id"]: b for b in page["blocks"]}
    runs = [r for r in records if r.get("entities") is not None]
    need = len(runs) // 2 + 1 if majority else 1
    mentions = []                       # (futás, blokk, helyek, nyers entitás)
    for run_index, record in enumerate(runs):
        for raw in record["entities"]:
            if not check_surface(BlockEntity.model_construct(**raw), blocks):
                continue
            spans = frozenset(surface_spans(raw["surface_form"], blocks[raw["block_id"]]))
            mentions.append((run_index, raw["block_id"], spans, raw))

    def overlaps(block_a, spans_a, block_b, spans_b):
        return block_a == block_b and any(_overlap(a, b) for a in spans_a for b in spans_b)

    candidates: dict[tuple[str, frozenset], list] = {}
    for mention in mentions:
        candidates.setdefault((mention[1], mention[2]), []).append(mention)
    ranked = sorted(candidates.items(), key=lambda item: (
        -len({m[0] for m in item[1]}), sum(e - s for s, e in item[0][1]),
        mentions.index(item[1][0])))
    kept: list[tuple[str, frozenset]] = []
    entities = []
    for (block, spans), members in ranked:
        votes = {m[0] for m in mentions if overlaps(block, spans, m[1], m[2])}
        if len(votes) < need or any(overlaps(block, spans, b, s) for b, s in kept):
            continue
        kept.append((block, spans))
        names = [alias_key(m[3]["canonical_name"]) for m in members]
        winner = max(names, key=lambda key: (names.count(key), -names.index(key)))
        entities.append(next(m[3] for m in members
                             if alias_key(m[3]["canonical_name"]) == winner))
    order = {id(raw): index for index, (_, _, _, raw) in enumerate(mentions)}
    entities.sort(key=lambda raw: order[id(raw)])
    counted: dict[str, int] = {}
    for record in runs:
        for key in {alias_key(p) for p in record.get("primary_entities") or []}:
            counted[key] = counted.get(key, 0) + 1
    primary = list(dict.fromkeys(p for r in runs for p in r.get("primary_entities") or []
                                 if counted[alias_key(p)] >= need))
    return {"page_id": page["page_id"], "entities": entities, "primary_entities": primary}


# ---------------------------------------------------------------------------
# jelentés
# ---------------------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}% ({part}/{whole})" if whole else "—"


SUMMED = ("required", "found", "named", "named_found", "concepts", "concepts_found", "good",
          "good_named", "good_concepts", "wrong_named", "wrong_concepts", "good_hard",
          "wrong_hard", "recognized_named",
          "recognized_concepts", "well_named_named", "well_named_concepts",
          "wrong", "type_right", "subtype_right", "subtype_total", "block_right", "block_total",
          "primary_found", "primary_total", "tokens_in", "tokens_out")
HEADER = ("| {first} | recall | megnevezett | fogalom | easy | hard | precizitás | típus | "
          "altípus | forrásblokk | primary | kitalált | hiba (neg. / kívüli) | USD | token be / ki |")
RULE = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def total_of(scores: list[PageScore], label: str = "összesen") -> PageScore:
    total = PageScore(label)
    for s in scores:
        for name in SUMMED:
            setattr(total, name, getattr(total, name) + getattr(s, name))
        total.cost_usd += s.cost_usd
        total.fabricated += s.fabricated
        total.negatives_hit += s.negatives_hit
        total.unlisted += s.unlisted
        for level, (found, all_) in s.by_difficulty.items():
            tally = total.by_difficulty.setdefault(level, [0, 0])
            tally[0] += found
            tally[1] += all_
    return total


def table_row(label: str, s: PageScore) -> str:
    easy, hard = s.by_difficulty.get("easy", [0, 0]), s.by_difficulty.get("hard", [0, 0])
    return (f"| {label} | {_pct(s.found, s.required)} | {_pct(s.named_found, s.named)} | "
            f"{_pct(s.concepts_found, s.concepts)} | {_pct(*easy)} | {_pct(*hard)} | "
            f"{_pct(s.good, s.precision_total)} | {_pct(s.type_right, s.found)} | "
            f"{_pct(s.subtype_right, s.subtype_total)} | {_pct(s.block_right, s.block_total)} | "
            f"{s.primary_found}/{s.primary_total} | {len(s.fabricated)} | "
            f"{len(s.negatives_hit)} / {len(s.unlisted)} | {s.cost_usd:.5f} | "
            f"{s.tokens_in} / {s.tokens_out} |")


def comparison_markdown(rows: list[tuple[str, list[PageScore]]]) -> str:
    """Több kör vagy változat összesítve, egymás alatt; alatta oldalanként is."""
    lines = [HEADER.format(first="kör"), RULE]
    lines += [table_row(f"**{label}**", total_of(scores)) for label, scores in rows]
    for page_id in [s.page_id for s in rows[0][1]]:
        lines += ["", f"### {page_id}", "", HEADER.format(first="kör"), RULE]
        lines += [table_row(label, s) for label, scores in rows for s in scores
                  if s.page_id == page_id]
    return "\n".join(lines) + "\n"


def report_markdown(model: str, scores: list[PageScore]) -> str:
    lines = [f"# Mesterséges oldalak: `{model}`, blokkos prompt, egész oldal", "",
             ("Pontozás a nyers kimenetből. Találat: a kanonikus név vagy a szöveg szerinti alak "
              "kulcsa (kis-nagybetű, ékezet, kötőjel nélkül) egyezik a referencia kanonikus "
              "nevével vagy egy aliasával. Megnevezett: a nem concept típusú kötelező tétel; "
              "fogalom: a concept típusú. Precizitás: negatív és referencián kívüli találat hiba, "
              "opcionális semleges."), "",
             HEADER.format(first="oldal"), RULE]
    lines += [table_row(s.page_id, s) for s in scores]
    if len(scores) > 1:
        lines.append(table_row("**összesen**", total_of(scores)))
    for s in scores:
        lines += ["", f"## {s.page_id}", "", f"Említés a kimenetben: {s.mentions}.", ""]
        lines.append(f"- **Kihagyott kötelező ({len(s.missed)}):** " + (", ".join(
            f"{name} ({kind}, {level}, {why})" for name, level, kind, why in s.missed) or "—"))
        lines.append(f"- **Hibás típus ({len(s.wrong_types)}):** " + (", ".join(
            f"{name}: {gold} → {got}" for name, gold, got in s.wrong_types) or "—"))
        lines.append(f"- **Hibás altípus ({len(s.wrong_subtypes)}):** " + (", ".join(
            f"{name}: {gold} → {got}" for name, gold, got in s.wrong_subtypes) or "—"))
        lines.append(f"- **Téves találat, negatív ({len(s.negatives_hit)}):** " + (", ".join(
            f"{name} (= „{neg}”)" for name, neg in s.negatives_hit) or "—"))
        lines.append(f"- **Téves találat, a referenciában nincs ({len(s.unlisted)}):** " + (
            ", ".join(f"{name} ({kind})" for name, kind in s.unlisted) or "—"))
        lines.append(f"- **Kitalált (a szöveg nincs a megadott blokkban) ({len(s.fabricated)}):** "
                     + (", ".join(f"{name}: „{text}” [{block}]"
                                  for name, text, block in s.fabricated) or "—"))
        lines.append(f"- **Rossz blokk ({len(s.wrong_blocks)}):** " + (", ".join(
            f"{name} [{block}]" for name, block in s.wrong_blocks) or "—"))
        lines.append("- **Elsődleges a referencián kívül:** "
                     + (", ".join(s.extra_primary) or "—"))
    return "\n".join(lines) + "\n"


def measure(model: str, data_dir: Path, pages: list[dict], tag: str = ""
            ) -> list[PageScore]:
    scores = []
    db = data_dir / "synthetic.duckdb"
    con = duckdb.connect(str(db), read_only=True) if db.exists() else None
    try:
        for page in pages:
            path = output_path(data_dir, page["page_id"], model, tag)
            if not path.exists():
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            ids = record.get("call_ids") or [i for i in [record.get("call_id")] if i]
            row = con.execute("SELECT sum(cost_usd), sum(tokens_in), sum(tokens_out) "
                              "FROM llm_calls WHERE list_contains(?, call_id)",
                              [ids]).fetchone() if con is not None and ids else None
            scores.append(score_page(page, record, row))
    finally:
        if con is not None:
            con.close()
    return scores


# ---------------------------------------------------------------------------
# több futás: átlag, szórás, unió, többség
# ---------------------------------------------------------------------------

METRICS = (
    ("recall", "found", "required", "named_found", "named", "concepts_found", "concepts"),
    ("precizitás", "good", "precision_total", "good_named", None, "good_concepts", None),
    ("felismerés", "recognized", "required", "recognized_named", "named",
     "recognized_concepts", "concepts"),
    ("elnevezés", "well_named", "recognized", "well_named_named", "recognized_named",
     "well_named_concepts", "recognized_concepts"),
)


def rates(total: PageScore) -> dict[str, float | None]:
    """Az összesített számok arányai (0–1): mértékenként össz / megnevezett / fogalom; a
    precizitás nevezője a megnevezettre és a fogalomra a jó és a hibás csoportok összege."""
    def ratio(part, whole):
        return part / whole if whole else None

    out: dict[str, float | None] = {}
    for name, part, whole, npart, nwhole, cpart, cwhole in METRICS:
        out[name] = ratio(getattr(total, part), getattr(total, whole))
        if name == "precizitás":
            out[f"{name}/megnevezett"] = ratio(total.good_named,
                                               total.good_named + total.wrong_named)
            out[f"{name}/fogalom"] = ratio(total.good_concepts,
                                           total.good_concepts + total.wrong_concepts)
        else:
            out[f"{name}/megnevezett"] = ratio(getattr(total, npart), getattr(total, nwhole))
            out[f"{name}/fogalom"] = ratio(getattr(total, cpart), getattr(total, cwhole))
    out["típus"] = ratio(total.type_right, total.found)
    out["altípus"] = ratio(total.subtype_right, total.subtype_total)
    out["primary"] = ratio(total.primary_found, total.primary_total)
    return out


def _cell(values: list[float | None]) -> str:
    """Egy futás: százalék; több futás: átlag ± szórás (mintaszórás)."""
    present = [v * 100 for v in values if v is not None]
    if not present:
        return "—"
    if len(present) == 1:
        return f"{present[0]:.1f}"
    mean = statistics.mean(present)
    return f"{mean:.1f} ± {statistics.stdev(present):.1f}"


ROUND_HEADER = ("| sor | végső recall (össz · megnev. · fogalom) | precizitás (össz · megnev. · "
                "fogalom) | felismerés (össz · megnev. · fogalom) | elnevezési pontosság (össz · "
                "megnev. · fogalom) | típus | altípus | primary (oldal) | kitalált | USD / oldal | "
                "token be / ki / oldal |")
ROUND_RULE = "|---|---|---|---|---|---|---|---|---|---|---|"


def round_row(label: str, runs: list[list[PageScore]], cost_runs: list[list[PageScore]]
              | None = None) -> str:
    """Egy sor: `runs` futásonként az oldalak pontszámai; több futásnál átlag ± szórás. A
    költség és a token a `cost_runs` (alapból a `runs`) összege futásonként, átlagolva, a
    `runs` egy futásának oldalszámával osztva."""
    totals = [total_of(scores) for scores in runs]
    per = [rates(t) for t in totals]
    cost_totals = [total_of(s) for s in (cost_runs or runs)]

    def triple(name):
        return " · ".join(_cell([r[key] for r in per])
                          for key in (name, f"{name}/megnevezett", f"{name}/fogalom"))

    pages = len(runs[0]) or 1
    usd = statistics.mean(t.cost_usd for t in cost_totals) / pages
    tin = statistics.mean(t.tokens_in for t in cost_totals) / pages
    tout = statistics.mean(t.tokens_out for t in cost_totals) / pages
    fabricated = statistics.mean(len(t.fabricated) for t in totals)
    primary = " / ".join(f"{t.primary_found}/{t.primary_total}" for t in totals)
    return (f"| {label} | {triple('recall')} | {triple('precizitás')} | {triple('felismerés')} | "
            f"{triple('elnevezés')} | {_cell([r['típus'] for r in per])} | "
            f"{_cell([r['altípus'] for r in per])} | {primary} | {fabricated:g} | "
            f"{usd:.5f} | {tin:.0f} / {tout:.0f} |")


def misses_markdown(label: str, runs: list[list[PageScore]],
                    majority: list[PageScore]) -> list[str]:
    """Oldalanként a kötelező tételek, amelyek legalább egy futásból kimaradtak: hány futásból,
    felismerési vagy elnevezési hibaként, és a többségi eredményben."""
    lines = [f"### {label}", ""]
    for index, page in enumerate(majority):
        tally: dict[str, list] = {}
        for scores in runs:
            for name, _, kind, why in scores[index].missed:
                tally.setdefault(name, [kind, []])[1].append(why)
        in_majority = {name: why for name, _, _, why in page.missed}
        lines.append(f"- **{page.page_id}:** " + ("—" if not tally else ""))
        for name, (kind, whys) in sorted(tally.items(), key=lambda x: (-len(x[1][1]), x[0])):
            counted = ", ".join(f"{whys.count(w)}× {w}" for w in (RECOGNITION, NAMING)
                                if whys.count(w))
            final = f"többségben: kimarad ({in_majority[name]})" if name in in_majority \
                else "többségben: megvan"
            lines.append(f"  - {name} ({kind}): {len(whys)}/{len(runs)} futásból kimaradt "
                         f"({counted}); {final}")
    return lines + [""]


def targets_markdown(rows: list[tuple[str, dict[str, float | None]]]) -> list[str]:
    """Soronként a célok (`TARGETS`): az érték, a küszöb, teljesül-e."""
    lines = ["## Célok", "",
             ("Felismerés: megnevezett ≥ 95%, fogalom ≥ 90%; precizitás ≥ 90%, a megnevezett "
              "entitásokra és a fogalmakra külön; végső recall (elnevezés után) ≥ 90% "
              "(javasolt). Futásonként az átlag."), ""]
    for label, values in rows:
        cells = []
        for name, threshold in TARGETS:
            value = values.get(name)
            mark = "—" if value is None else ("teljesül" if value >= threshold else "NEM")
            shown = "—" if value is None else f"{value * 100:.1f}"
            cells.append(f"{name} {shown} (≥ {threshold * 100:.0f}: {mark})")
        met = all(values.get(n) is not None and values[n] >= t for n, t in TARGETS)
        lines.append(f"- **{label}** ({'mind teljesül' if met else 'nem mind'}): "
                     + "; ".join(cells))
    return lines + [""]


def _mean_rates(runs: list[list[PageScore]]) -> dict[str, float | None]:
    per = [rates(total_of(scores)) for scores in runs]
    return {key: (statistics.mean(present) if (present := [r[key] for r in per
                                                            if r[key] is not None]) else None)
            for key in per[0]}


CHECKPOINT_HEADER = ("| oldal | felismerés megnev. | felismerés fogalom | elnevezési pontosság | "
                     "végső recall (össz · megnev. · fogalom) | precizitás megnev. | "
                     "precizitás fogalom | típus | altípus | főtéma | USD / oldal |")
CHECKPOINT_RULE = "|---|---|---|---|---|---|---|---|---|---|---|"


def checkpoint_row(label: str, scores: list[PageScore]) -> str:
    total = total_of(scores)
    r = rates(total)

    def pct(key):
        return "—" if r[key] is None else f"{r[key] * 100:.1f}"

    def counted(part, whole):
        return f"{pct_of(part, whole)} ({part}/{whole})"

    return (f"| {label} | {counted(total.recognized_named, total.named)} | "
            f"{counted(total.recognized_concepts, total.concepts)} | "
            f"{counted(total.well_named, total.recognized)} | "
            f"{pct('recall')} · {pct('recall/megnevezett')} · {pct('recall/fogalom')} "
            f"({total.found}/{total.required}) | "
            f"{counted(total.good_named, total.good_named + total.wrong_named)} | "
            f"{counted(total.good_concepts, total.good_concepts + total.wrong_concepts)} | "
            f"{counted(total.type_right, total.found)} | "
            f"{counted(total.subtype_right, total.subtype_total)} | "
            f"{total.primary_found}/{total.primary_total} | "
            f"{total.cost_usd / (len(scores) or 1):.4f} |")


def pct_of(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}" if whole else "—"


def checkpoint_markdown(model: str, data_dir: Path, pages: list[dict],
                        series: dict[str, str]) -> str:
    """Sorozatonként (név → címke) oldalanként és összesítve: felismerés (megnevezett, fogalom),
    elnevezési pontosság, végső recall, precizitás (megnevezett, fogalom), típus- és
    altípus-pontosság, főtéma, költség/oldal; a célok az összesítettre; oldalanként a
    kihagyások (felismerési vagy elnevezési hiba) és a téves találatok, megnevezett és fogalom
    külön."""
    lines = [f"# Ellenőrzőpont-mérés: `{model}` kinyerés", "",
             ("Pontozás hálózat nélkül a tárolt kimenetekből, a jelenlegi szabályokkal (recall "
              "a referencia blokkjában; az `ambiguous_aliases` felismerésnek elég, elnevezésnek "
              "nem). A költség egy oldalra: a kinyerő hívás(ok) és az elnevezés."), ""]
    goals: list[tuple[str, dict]] = []
    details: list[str] = []
    for label, tag in series.items():
        scores = measure(model, data_dir, pages, tag)
        lines += [f"## {label} (`{tag}`)", "", CHECKPOINT_HEADER, CHECKPOINT_RULE]
        lines += [checkpoint_row(s.page_id, [s]) for s in scores]
        lines += [checkpoint_row("**összesen**", scores), ""]
        goals.append((label, rates(total_of(scores))))
        details += [f"### {label}", ""]
        for s in scores:
            details.append(f"- **{s.page_id}**")
            missed = ", ".join(f"{name} ({kind}, {why})" for name, _, kind, why in s.missed)
            details.append(f"  - kihagyás ({len(s.missed)}): {missed or '—'}")
            named = [f for f in s.false_hits if f[1] != "concept"]
            concepts = [f for f in s.false_hits if f[1] == "concept"]
            details.append(f"  - téves találat, megnevezett ({len(named)}): " + (", ".join(
                f"{name} ({kind}; {why})" for name, kind, why in named) or "—"))
            details.append(f"  - téves találat, fogalom ({len(concepts)}): " + (", ".join(
                f"{name} ({why})" for name, _, why in concepts) or "—"))
        details.append("")
    return "\n".join(lines + targets_markdown(goals) + ["## Részletek oldalanként", "",
                                                         *details])


def load_records(model: str, data_dir: Path, pages: list[dict], tag: str) -> list[dict]:
    return [json.loads(output_path(data_dir, page["page_id"], model, tag).read_text(
        encoding="utf-8")) for page in pages]


def compare_markdown(model: str, data_dir: Path, pages: list[dict], single: list[str],
                     repeated: dict[str, list[str]]) -> str:
    """Egyszeri körök (`single`: címkék) és ismételt változatok (`repeated`: név → a futások
    címkéi): futásonként, átlag ± szórás, unió, többség (felismerés szerint szavazva),
    oldalanként a kihagyások okkal."""
    lines = [f"# Mesterséges oldalak: `{model}`, körök összevetése", "",
             ("Mindegyik sor a jelenlegi referencialistával, a nyers kimenetből pontozva. "
              "Megnevezett: nem concept típusú kötelező tétel; fogalom: concept típusú. Több "
              "futásnál átlag ± mintaszórás; az unió és a többség a felismerés szerint szavaz "
              "(blokk + átfedő szöveg szerinti alak), a költségük a futások összege. A költség "
              "egy futásnál a kinyerés és (ha van) az elnevezés hívása együtt."),
             "", ROUND_HEADER, ROUND_RULE]
    lines += [round_row(tag, [measure(model, data_dir, pages, tag)]) for tag in single]
    details = []
    goals: list[tuple[str, dict]] = []
    for label, tags in repeated.items():
        runs = [measure(model, data_dir, pages, tag) for tag in tags]
        per_run = [load_records(model, data_dir, pages, tag) for tag in tags]
        every = [s for scores in runs for s in scores]
        lines += [round_row(f"{label} · {tag}", [scores]) for tag, scores in zip(tags, runs)]
        lines.append(round_row(f"{label} ({len(tags)} futás)", runs))
        goals.append((f"{label}, futásonként", _mean_rates(runs)))
        both = {}
        for name, majority in (("unió", False), ("többség", True)):
            both[name] = [score_page(page, consensus(page, [records[i] for records in per_run],
                                                     majority))
                          for i, page in enumerate(pages)]
            lines.append(round_row(f"{label} {name}", [both[name]], [every]))
            goals.append((f"{label} {name}", rates(total_of(both[name]))))
        details += misses_markdown(label, runs, both["többség"])
    return "\n".join(lines + ["", *targets_markdown(goals), "## Kihagyások oldalanként", "",
                              *details])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["run", "report", "compare", "name", "checkpoint"])
    parser.add_argument("--model", default=None,
                        help="a kinyerés modellje (alapból a [pipeline] extraction)")
    parser.add_argument("--naming-model", default=None,
                        help="name: az elnevezés modellje (alapból a [pipeline] naming)")
    parser.add_argument("--tag", default="", help="a kör címkéje (r1, r2, …)")
    parser.add_argument("--from", dest="source", default="",
                        help="name: a kör címkéje, amelynek a rekordjait elnevezi")
    parser.add_argument("--single", default="", help="compare: egyszeri körök címkéi, vesszővel")
    parser.add_argument("--repeated", action="append", default=[],
                        help="compare: név=címke1,címke2,… (ismételt futások)")
    parser.add_argument("--series", action="append", default=[],
                        help="checkpoint: név=címke (egy sorozat)")
    parser.add_argument("--set", dest="page_set", choices=SETS, default=DEVELOPMENT,
                        help="az oldalak köre: fejlesztési (s1–s3), általánosítási, mind")
    parser.add_argument("--page", action="append", default=[],
                        help="csak ez az oldal (page_id; ismételhető)")
    parser.add_argument("--pages-dir", type=Path, default=None,
                        help="az oldalak JSON-jai innen (pl. a futáskori blokkokkal)")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    pipeline = load_config().pipeline
    args.model = args.model or pipeline["extraction"]
    pages = [page for page in load_pages(args.pages_dir, args.page_set)
             if not args.page or page["page_id"] in args.page]
    suffix = "" if args.page_set == DEVELOPMENT else f"-{args.page_set}"
    if args.command == "checkpoint":
        series = dict(item.split("=", 1) for item in args.series)
        args.out.mkdir(parents=True, exist_ok=True)
        out = args.out / f"{args.model}{suffix}-checkpoint.md"
        out.write_text(checkpoint_markdown(args.model, args.data_dir, pages, series),
                       encoding="utf-8")
        print(out)
        return
    if args.command == "compare":
        repeated = dict(item.split("=", 1) for item in args.repeated)
        text = compare_markdown(args.model, args.data_dir, pages,
                                [s for s in args.single.split(",") if s],
                                {k: v.split(",") for k, v in repeated.items()})
        args.out.mkdir(parents=True, exist_ok=True)
        out = args.out / f"{args.model}{suffix}-compare.md"
        out.write_text(text, encoding="utf-8")
        print(out)
        return
    if args.command == "run":
        run(args.model, args.data_dir, pages, args.tag)
    if args.command == "name":
        if not args.source or not args.tag or args.source == args.tag:
            raise SystemExit("name: --from és egy tőle eltérő --tag kell")
        naming_model = args.naming_model or pipeline["naming"]
        if naming_model == "off":
            raise SystemExit("name: az elnevezés ki van kapcsolva ([pipeline] naming = off); "
                             "a --naming-model adja meg a modellt")
        name_run(args.model, args.data_dir, pages, args.source, args.tag, naming_model)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{args.model}{'-' + args.tag if args.tag else ''}-report.md"
    out.write_text(report_markdown(args.model, measure(args.model, args.data_dir, pages,
                                                       args.tag)),
                   encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
