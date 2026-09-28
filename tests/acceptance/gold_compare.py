"""Mérés a referencialista (gold) ellen: a jelölt modellek × {egész oldal, heading-szakaszonként}
× a `GOLD_PAGES` oldalai, a prompt v2-vel.

    python -m tests.acceptance.gold_compare run [--models gpt-6-luna,...] [--modes page,sections]
    python -m tests.acceptance.gold_compare report

`run` (élő, költséges) csak akkor indul, ha mindhárom oldal gold-listája megvan
(`tests/acceptance/gold/<oldal>.json`). A hívások a `data/compare/<készlet>.duckdb` oldalain
mennek (a `llm_compare` visszajátszott készletei), `page_entities`-t nem írnak; a nyers kimenet
`data/compare/gold/<oldal>.<modell>.<mód>.jsonl`.

- `page`: egy hívás az egész oldalra (`page_input`, ahogy az LLM-kör küldi).
- `sections`: a main content szakaszokra bontva (`split_sections`); hívásonként a site-mondat, a
  title és egy szakasz szövege. A kimenetek uniója az oldal eredménye.

`report` (hálózat nélkül): `tests/acceptance/out/llm-compare/gold-report.md`, egy táblában
modellenként és módonként (a három oldal összesítve):

- recall külön a megnevezett entitásokra (gold-típus ≠ concept) és a fogalmakra (concept): a
  gold-tételek aránya, amelyeknek a neve vagy egy aliasa (`alias_key` szerint) szerepel a modell
  nem fabrikált kimenetében;
- precizitás: a modell különböző (kulcs szerinti) nem fabrikált neveinek aránya, amely egy
  gold-tételhez illeszkedik;
- típuspontosság: az illeszkedő gold-tételek aránya, amelyeknél a modell típusa a gold-típus;
- `primary_entity`: hány oldalon illeszkedik a gold `primary_entity`-jéhez (szakaszos módban a
  szakaszok leggyakoribb válasza);
- fabrikált sor, az idézetben nincs a név (csak mérés), hívások, USD, medián késleltetés.
Alatta oldalanként a kihagyott gold-tételek és a gold-listán nem szereplő nevek.

Gold-fájl: `{"url": …, "primary_entity": {"name": …, "aliases": […]}, "entities": [{"name": …,
"aliases": […], "type": …}]}`.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import duckdb

from aaa2.db.connect import DATA_DIR, connect
from aaa2.entities.llm import (
    PROMPT,
    check_evidence,
    name_in_evidence,
    page_input,
    site_line,
)
from aaa2.entities.rules import alias_key
from aaa2.llm.client import LLMError, SchemaMismatch, open_clients
from aaa2.llm.schemas import ENTITY_TYPES, PageExtraction
from tests.acceptance.gold_draft import GOLD_PAGES
from tests.acceptance.llm_compare import OUT_DIR, _Entity, page_sources, read_jsonl, write_jsonl

GOLD_DIR = Path(__file__).parent / "gold"
MODELS: tuple[tuple[str, str], ...] = (
    ("openai", "gpt-6-luna"),
    ("openai", "gpt-6-sol"),
    ("anthropic", "claude-haiku-4-5-20251001"),
)
MODES = ("page", "sections")
MIN_SECTIONS = 3


# ---------------------------------------------------------------------------
# gold-lista
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldEntity:
    name: str
    type: str
    keys: frozenset[str]

    @property
    def concept(self) -> bool:
        return self.type == "concept"


@dataclass(frozen=True)
class Gold:
    url: str
    primary: frozenset[str]
    entities: tuple[GoldEntity, ...]

    def match(self, key: str) -> GoldEntity | None:
        return next((e for e in self.entities if key in e.keys), None)


def load_gold(path: Path) -> Gold:
    """Beolvas és ellenőriz: ismert típus, nem üres név, egy kulcs legfeljebb egy tételé."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    entities, seen = [], {}
    for item in raw["entities"]:
        if item["type"] not in ENTITY_TYPES:
            raise ValueError(f"{path.name}: ismeretlen típus: {item['type']} ({item['name']})")
        keys = frozenset(k for k in (alias_key(n) for n in [item["name"], *item.get("aliases", [])])
                         if k)
        if not keys:
            raise ValueError(f"{path.name}: üres név")
        for key in keys:
            if key in seen:
                raise ValueError(f"{path.name}: a „{key}” kulcs két tételé: {seen[key]}, "
                                 f"{item['name']}")
            seen[key] = item["name"]
        entities.append(GoldEntity(item["name"], item["type"], keys))
    primary = raw["primary_entity"]
    return Gold(raw["url"], frozenset(alias_key(n) for n in [primary["name"],
                                                               *primary.get("aliases", [])]),
                tuple(entities))


def gold_path(slug: str) -> Path:
    return GOLD_DIR / f"{slug}.json"


# ---------------------------------------------------------------------------
# bemenet
# ---------------------------------------------------------------------------


def split_sections(main: str, headings: list[tuple[int, str]]) -> list[str]:
    """A main content szakaszai: a határ az első olyan heading-szint H1 alatt, amelyikből
    legalább `MIN_SECTIONS` megtalálható a main contentben (sorrendben keresve); az első
    ilyen heading előtti szöveg is egy szakasz. Ha nincs ilyen szint, egy szakasz: az egész."""
    for level in range(2, 7):
        starts, cursor = [], 0
        for heading_level, text in headings:
            if heading_level != level or not text:
                continue
            at = main.find(text, cursor)
            if at >= 0:
                starts.append(at)
                cursor = at + len(text)
        if len(starts) >= MIN_SECTIONS:
            bounds = [0, *starts, len(main)]
            return [part for a, b in pairwise(bounds) if (part := main[a:b].strip())]
    return [main]


def call_inputs(con: duckdb.DuckDBPyConnection, page_id: int, mode: str) -> list[str]:
    title, main = con.execute("SELECT title, main_content FROM pages WHERE page_id = ?",
                              [page_id]).fetchone()
    headings = con.execute("SELECT level, text FROM headings WHERE page_id = ? ORDER BY ordinal",
                           [page_id]).fetchall()
    site = site_line(con)
    if mode == "page":
        return [page_input(title, headings, main, site)[0]]
    return [page_input(title, [], section, site)[0]
            for section in split_sections(main or "", headings)]


# ---------------------------------------------------------------------------
# futás
# ---------------------------------------------------------------------------


def output_path(data_dir: Path, slug: str, model: str, mode: str) -> Path:
    return data_dir / "gold" / f"{slug}.{model}.{mode}.jsonl"


def run(models: list[tuple[str, str]], modes: list[str], data_dir: Path,
        require_gold: bool = True) -> None:
    """`require_gold=False`: a hívások a gold-listák előtt is futnak (a nyers kimenet nem függ
    tőlük); a pontozás a listák után, hálózat nélkül."""
    missing = [slug for slug, _, _ in GOLD_PAGES if not gold_path(slug).exists()]
    if missing and require_gold:
        raise SystemExit(f"a futás a referencialistára vár; hiányzik: {missing}")
    (data_dir / "gold").mkdir(parents=True, exist_ok=True)
    for slug, name, url in GOLD_PAGES:
        con = connect(data_dir / f"{name}.duckdb")
        try:
            (page_id,) = con.execute("SELECT page_id FROM pages WHERE url = ?", [url]).fetchone()
            for provider, model in models:
                clients, skipped = open_clients(con, models={provider: model})
                if provider not in clients:
                    print(f"{slug}: {model} kihagyva ({skipped.get(provider)})")
                    continue
                for mode in modes:
                    records = []
                    for number, text in enumerate(call_inputs(con, page_id, mode)):
                        records.append(_call(clients[provider], text, page_id, number))
                    write_jsonl(output_path(data_dir, slug, model, mode), records)
                    print(f"{slug}: {model} {mode}: {len(records)} hívás, "
                          f"{sum(r['error'] is not None for r in records)} hiba")
        finally:
            con.close()


def _call(client, text: str, page_id: int, number: int) -> dict:
    record = {"model": client.model, "page_id": page_id, "part": number, "call_id": None,
              "primary_entity": None, "entities": None, "error": None}
    try:
        result = client.extract(PageExtraction, PROMPT, text, domain="entity", page_id=page_id)
    except SchemaMismatch as exc:
        record.update(call_id=exc.call_id, error=f"schema_mismatch: {exc}"[:500])
        return record
    except LLMError as exc:
        record.update(error=f"call_error: {exc}"[:500])
        return record
    record.update(call_id=result.call_id, primary_entity=result.parsed.primary_entity,
                  entities=[entity.model_dump() for entity in result.parsed.entities])
    return record


# ---------------------------------------------------------------------------
# pontozás
# ---------------------------------------------------------------------------


@dataclass
class Score:
    named: int = 0
    named_found: int = 0
    concepts: int = 0
    concepts_found: int = 0
    model_keys: int = 0
    model_matched: int = 0
    typed_right: int = 0
    matched_gold: int = 0
    primary_hits: int = 0
    pages: int = 0
    fabricated: int = 0
    name_missing: int = 0
    calls: int = 0
    errors: int = 0
    cost_usd: float = 0.0
    latencies: list = field(default_factory=list)
    missed: dict = field(default_factory=dict)       # oldal → kihagyott gold-nevek
    extra: dict = field(default_factory=dict)        # oldal → gold-listán nem szereplő nevek

    def add(self, other: Score) -> None:
        for name in ("named", "named_found", "concepts", "concepts_found", "model_keys",
                     "model_matched", "typed_right", "matched_gold", "primary_hits", "pages",
                     "fabricated", "name_missing", "calls", "errors"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.cost_usd += other.cost_usd
        self.latencies += other.latencies
        self.missed.update(other.missed)
        self.extra.update(other.extra)


def score_page(slug: str, gold: Gold, records: list[dict], sources: list[str],
               calls: list[tuple]) -> Score:
    """Egy oldal egy modell-mód kimenete a gold ellen; `calls`: (cost_usd, latency_ms) sorok."""
    score = Score(pages=1, calls=len(records),
                  errors=sum(r["error"] is not None for r in records))
    types: dict[str, str] = {}
    names: dict[str, str] = {}
    primaries = Counter()
    for record in records:
        if record.get("primary_entity"):
            primaries[alias_key(record["primary_entity"])] += 1
        for entity in record.get("entities") or []:
            if check_evidence(_Entity(entity), sources) == "fabricated":
                score.fabricated += 1
                continue
            score.name_missing += not name_in_evidence(_Entity(entity))
            key = alias_key(entity["name"])
            if key and key not in types:
                types[key], names[key] = entity["type"], entity["name"]
    found: dict[GoldEntity, str] = {}
    for key, kind in types.items():
        target = gold.match(key)
        score.model_keys += 1
        if target is not None:
            score.model_matched += 1
            found.setdefault(target, kind)
    for entity in gold.entities:
        if entity.concept:
            score.concepts += 1
            score.concepts_found += entity in found
        else:
            score.named += 1
            score.named_found += entity in found
    score.matched_gold = len(found)
    score.typed_right = sum(kind == entity.type for entity, kind in found.items())
    score.primary_hits = int(bool(primaries) and primaries.most_common(1)[0][0] in gold.primary)
    score.missed[slug] = [e.name for e in gold.entities if e not in found]
    score.extra[slug] = [names[k] for k in types if gold.match(k) is None]
    score.cost_usd = sum(cost or 0.0 for cost, _ in calls)
    score.latencies = [latency or 0 for _, latency in calls]
    return score


def measure(data_dir: Path, models=MODELS, modes=MODES) -> dict[tuple[str, str], Score]:
    scores: dict[tuple[str, str], Score] = {}
    for slug, name, url in GOLD_PAGES:
        if not gold_path(slug).exists():
            continue
        gold = load_gold(gold_path(slug))
        con = duckdb.connect(str(data_dir / f"{name}.duckdb"), read_only=True)
        try:
            (page_id,) = con.execute("SELECT page_id FROM pages WHERE url = ?", [url]).fetchone()
            sources = page_sources(con, page_id)
            for _, model in models:
                for mode in modes:
                    records = read_jsonl(output_path(data_dir, slug, model, mode))
                    if not records:
                        continue
                    ids = [r["call_id"] for r in records if r["call_id"] is not None]
                    calls = con.execute("SELECT cost_usd, latency_ms FROM llm_calls WHERE "
                                        "list_contains(?, call_id)", [ids]).fetchall()
                    page = score_page(slug, gold, records, sources, calls)
                    scores.setdefault((model, mode), Score()).add(page)
        finally:
            con.close()
    return scores


# ---------------------------------------------------------------------------
# jelentés
# ---------------------------------------------------------------------------


def _pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.1f}% ({part}/{whole})" if whole else "—"


def report_markdown(scores: dict[tuple[str, str], Score]) -> str:
    lines = ["# Mérés a referencialista ellen", "",
             ("Prompt v2. Recall és precizitás a gold-lista ellen (a név vagy egy alias kulcsa "
              "szerint), a fabrikált sorok nélkül; a három oldal összesítve. A költség mért "
              "adat."),
             "",
             ("| modell | mód | recall: megnevezett | recall: fogalom | precizitás | "
              "típuspontosság | primary_entity | fabrikált | nincs név az idézetben | hívás "
              "(hiba) | USD | medián késleltetés |"),
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (model, mode), s in scores.items():
        latency = f"{statistics.median(s.latencies) / 1000:.1f} mp" if s.latencies else "—"
        lines.append(
            f"| `{model}` | {mode} | {_pct(s.named_found, s.named)} | "
            f"{_pct(s.concepts_found, s.concepts)} | {_pct(s.model_matched, s.model_keys)} | "
            f"{_pct(s.typed_right, s.matched_gold)} | {s.primary_hits}/{s.pages} | "
            f"{s.fabricated} | {s.name_missing} | {s.calls} ({s.errors}) | "
            f"{s.cost_usd:.4f} | {latency} |")
    for (model, mode), s in scores.items():
        lines += ["", f"## `{model}`, {mode}", ""]
        for slug in s.missed:
            lines.append(f"- {slug}: kihagyott ({len(s.missed[slug])}): "
                         + (", ".join(s.missed[slug]) or "—"))
            lines.append(f"- {slug}: nincs a gold-listán ({len(s.extra[slug])}): "
                         + (", ".join(s.extra[slug]) or "—"))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["run", "report"])
    parser.add_argument("--models", default=",".join(model for _, model in MODELS))
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--without-gold", action="store_true",
                        help="a hívások a gold-listák előtt (a pontozás később)")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=OUT_DIR / "gold-report.md")
    args = parser.parse_args(argv)
    by_name = {model: (provider, model) for provider, model in MODELS}
    wanted = [m for m in args.models.split(",") if m]
    unknown = [m for m in wanted if m not in by_name]
    modes = [m for m in args.modes.split(",") if m]
    if unknown or set(modes) - set(MODES):
        parser.error(f"ismeretlen modell vagy mód: {unknown or modes}")
    if args.command == "run":
        run([by_name[m] for m in wanted], modes, args.data_dir,
            require_gold=not args.without_gold)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report_markdown(measure(args.data_dir)), encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
