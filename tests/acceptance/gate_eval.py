"""Kapu (E1) és entitásonkénti ellenőrzés (E2) a tárolt kinyerési kimeneteken, verdikt-alapú
precizitással.

    python -m tests.acceptance.gate_eval e1 --from cp --tag cp-e1
    python -m tests.acceptance.gate_eval e2 --from cp --tag cp-e2-luna --verify-model gpt-6-luna
    python -m tests.acceptance.gate_eval verdicts --series cp --series cp-e1 …
    python -m tests.acceptance.gate_eval report --series "szűrés nélkül=cp" --series "E1=cp-e1" …

Közös kapcsolók: `--model` (a kinyerés modellje, alapból a `[pipeline] extraction`),
`--pages-dir` (az oldalak JSON-jai; alapból a `dev_pages/`), `--page`, `--data-dir`. Az oldalak
a valódi fejlesztési oldalak (`synthetic_eval.REAL`).

- `e1`: a `--from` kör rekordjaira a kapu (`entities.gate`); a navigáció (chrome-régió) és az
  anchor-szövegek a rögzített készlet renderelt DOM-jából (`annotation.PAGES`, `v3.dom_context`),
  a tartalmi blokkok az oldal JSON-jából. A tudásbázis-kérések gyorsítótára és naplója:
  `<data-dir>/gate.duckdb`. Kimenet: a rekord a `--tag` alá, a döntések mellé
  (`<oldal>.<modell>.<címke>.gate.json`).
- `e2`: a `--from` kör rekordjaira az ellenőrző hívás (`entities.verify`) a `--verify-model`-lel;
  a hívás a `synthetic.duckdb` `llm_calls`-ába kerül, a költsége a rekord `call_ids`-ában.
- `verdicts`: a sorozatok kimenetéből a még nem megítélt concept- és service-tételek a
  verdiktfájlba (`tests/acceptance/verdicts/verdicts.json`): oldal, kanonikus név, típus, a
  tétel blokkja és szövege, a helye a referencialistában; tételenként (oldal, név kulcsa, típus)
  egyszer. A `verdict` üres, kitöltendő: `valid` / `descriptive` / `wrong_name` / `wrong_type`.
- `report`: sorozatonként (név=címke) a fő sor (kk.coach + ngx) és a Materia külön regressziós
  sorként: felismerés, végső recall, megnevezett precizitás (a referencialistához, concept és
  service nélkül), a concept- és service-tételek precizitása a verdiktekből (összesen és a
  legfontosabb 10 / 20 tételre oldalanként), költség/oldal; a megítéletlen tételek listája; a
  kapu és az ellenőrzés által kiejtett referenciatételek; a kapu feltételenként.

Fontossági sorrend (legfontosabb 10 / 20): előre a tétel, amelynek említése vagy neve title-
vagy heading-blokkban áll; azon belül az említésszám szerint csökkenő, majd az első említés
sorrendje.
"""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import zstandard

import tests.acceptance.synthetic_eval as se
from aaa2.db.connect import DATA_DIR, connect
from aaa2.entities.blocks import block_text, surface_spans
from aaa2.entities.gate import (
    PROMINENT_KINDS,
    KnowledgeBase,
    PageContext,
    SoftItem,
    gate_record,
    soft_items,
    structure,
)
from aaa2.entities.rules import alias_key
from aaa2.entities.v3 import dom_context
from aaa2.entities.validate import _Api
from aaa2.entities.verify import item_block, verify_record
from aaa2.llm.client import Retry
from aaa2.llm.config import load_config
from tests.acceptance.annotation import PAGES as CRAWLED
from tests.acceptance.annotation import block_mapping, locked_sources

VERDICTS_FILE = Path(__file__).parent / "verdicts" / "verdicts.json"
VERDICTS = ("valid", "descriptive", "wrong_name", "wrong_type")
MAIN = ("kk_coach_meres_hu", "ngx_accordion_en")           # B2B-oldalak: a fő sor
REGRESSION = ("materia_etlap_hu",)
TOP = (10, 20)
VERDICT_RULES = (
    "Egy tétel verdiktje a fogalom- és szolgáltatásdefiníció v2 szerint (M2 spec, "
    "Ellenőrzőpont). valid: concept: fogalomrendszerbe tartozó szakkifejezés (bevett szakmai "
    "fogalom, vagy a site saját, névként használt fogalma); service: megnevezett ajánlat "
    "(heading, kártya, árazási vagy táblázatsor, menüpont). descriptive: leíró kifejezés vagy "
    "bekezdésbeli tevékenység-leírás, nem entitás. wrong_name: a dolog entitás, de a név rossz "
    "(ragozott, csonka, összevont, a szövegben nem álló alak). wrong_type: entitás, de nem "
    "ilyen típusú (pl. termék, technológia, szervezet). A verdikt a tétel nevéről és "
    "szerepéről szól az oldalon, nem arról, hogy a referencialistában szerepel-e.")


# ---------------------------------------------------------------------------
# oldalak és rekordok
# ---------------------------------------------------------------------------


def load_real_pages(pages_dir: Path | None, only: Sequence[str] = ()) -> list[dict]:
    return [p for p in se.load_pages(pages_dir, se.REAL) if not only or p["page_id"] in only]


def page_context(page: Mapping, data_dir: Path) -> PageContext:
    """A tartalmi blokkok az oldal JSON-jából; a chrome-régió szövegei és az anchor-szövegek a
    rögzített készlet renderelt DOM-jából."""
    chrome: list[str] = []
    anchors: list[str] = []
    sources = [*CRAWLED, *((pid, db, url) for pid, db, url, _ in locked_sources())]
    source = next((c for c in sources if c[0] == page["page_id"]), None)
    if source is not None and (data_dir / f"{source[1]}.duckdb").exists():
        _, db, url = source
        con = duckdb.connect(str(data_dir / f"{db}.duckdb"), read_only=True)
        try:
            row = con.execute("SELECT title, rendered_html FROM pages WHERE url = ?",
                              [url]).fetchone()
        finally:
            con.close()
        if row is not None:
            html = zstandard.ZstdDecompressor().decompress(row[1]).decode("utf-8", "replace")
            chrome, anchors = dom_context(html, row[0])
    return PageContext(page["blocks"], chrome, anchors, page.get("lang") or "en")


def read_record(data_dir: Path, page_id: str, model: str, tag: str) -> dict | None:
    path = se.output_path(data_dir, page_id, model, tag)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write_record(data_dir: Path, page_id: str, model: str, tag: str, record: Mapping) -> None:
    se.output_path(data_dir, page_id, model, tag).write_text(
        json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")


def decisions_path(data_dir: Path, page_id: str, model: str, tag: str) -> Path:
    return se.output_path(data_dir, page_id, model, tag).with_suffix(".gate.json")


def run_e1(model: str, data_dir: Path, pages: list[dict], source: str, tag: str,
           knowledge=None) -> None:
    con = None
    if knowledge is None:
        con = connect(data_dir / "gate.duckdb")
        api = _Api(con, None, httpx.Client(timeout=20.0), Retry(), _now, time.monotonic)
        knowledge = KnowledgeBase(api.get)
    try:
        for page in pages:
            record = read_record(data_dir, page["page_id"], model, source)
            if record is None or record.get("entities") is None:
                continue
            gated, decisions = gate_record(record, page_context(page, data_dir), knowledge)
            gated["gate_source"] = source
            write_record(data_dir, page["page_id"], model, tag, gated)
            decisions_path(data_dir, page["page_id"], model, tag).write_text(json.dumps(
                [d.__dict__ for d in decisions], ensure_ascii=False, indent=1), encoding="utf-8")
            kept = sum(d.keep for d in decisions)
            print(f"{page['page_id']}: {source} → {tag}: {kept}/{len(decisions)} tétel marad")
    finally:
        if con is not None:
            con.close()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def run_e2(model: str, data_dir: Path, pages: list[dict], source: str, tag: str,
           verify_model: str) -> None:
    con = connect(data_dir / "synthetic.duckdb")
    try:
        client = se._client(con, verify_model)
        for page in pages:
            record = read_record(data_dir, page["page_id"], model, source)
            if record is None or record.get("entities") is None:
                continue
            blocks = {b["id"]: b for b in page["blocks"]}
            verified = verify_record(client, record, blocks)
            verified["verify_source"] = source
            write_record(data_dir, page["page_id"], model, tag, verified)
            dropped = sum(1 for _, _, keep in verified["verify_decisions"] if keep is False)
            print(f"{page['page_id']}: {source} → {tag}: "
                  + (verified["verify_error"] or f"{dropped}/{len(verified['verify_decisions'])}"
                     f" tétel kiesik, válasz nélkül {verified['verify_missing']}"))
    finally:
        con.close()


# ---------------------------------------------------------------------------
# verdiktek
# ---------------------------------------------------------------------------


def verdict_key(page_id: str, canonical: str, kind: str) -> tuple[str, str, str]:
    return page_id, alias_key(canonical), kind


def load_verdicts(path: Path = VERDICTS_FILE) -> dict:
    if not path.exists():
        return {"rules": VERDICT_RULES, "verdicts": list(VERDICTS), "items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def verdict_index(data: Mapping) -> dict[tuple[str, str, str], str | None]:
    return {verdict_key(i["page_id"], i["canonical"], i["type"]): i.get("verdict")
            for i in data["items"]}


def reference_place(page: Mapping, item: SoftItem) -> str:
    """A tétel helye a referencialistában (`synthetic_eval._where` az első említésre)."""
    gold = page["gold"]
    required, optional = se._items(gold["entities"]), se._items(gold.get("optional", []))
    negatives = {alias_key(n["text"]): n["text"] for n in gold.get("negatives", [])}
    blocks = {b["id"]: b for b in page["blocks"]}
    places = {se._where(raw, gold, required, optional, negatives, blocks)
              for raw in item.mentions}
    ranked = sorted(places, key=lambda p: (not p.startswith("kötelező"),
                                           not p.startswith("opcionális"), p))
    return ranked[0]


def export_verdicts(model: str, data_dir: Path, pages: list[dict], tags: Sequence[str],
                    path: Path = VERDICTS_FILE) -> int:
    """A még nem megítélt tételek a verdiktfájl végére; visszaad: hány új tétel."""
    data = load_verdicts(path)
    known = verdict_index(data)
    added = 0
    for page in pages:
        blocks = {b["id"]: b for b in page["blocks"]}
        for tag in tags:
            record = read_record(data_dir, page["page_id"], model, tag)
            if record is None:
                continue
            for item in soft_items(record.get("entities") or [], blocks):
                key = verdict_key(page["page_id"], item.canonical, item.type)
                if key in known:
                    continue
                block = item_block(item, blocks)
                data["items"].append({
                    "page_id": page["page_id"], "canonical": item.canonical,
                    "type": item.type, "block": block["id"], "kind": block.get("kind"),
                    "block_text": block_text(block), "reference": reference_place(page, item),
                    "verdict": None, "note": ""})
                known[key] = None
                added += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return added


# ---------------------------------------------------------------------------
# pontozás
# ---------------------------------------------------------------------------


@dataclass
class SoftScore:
    page_id: str
    items: int = 0
    judged: dict = field(default_factory=dict)          # verdikt → db
    by_type: dict = field(default_factory=dict)         # típus → [valid, megítélt]
    top: dict = field(default_factory=dict)             # k → [valid, megítélt]
    unjudged: list = field(default_factory=list)        # (név, típus)

    @property
    def valid(self) -> int:
        return self.judged.get("valid", 0)

    @property
    def judged_total(self) -> int:
        return sum(self.judged.values())


def ranked(items: Sequence[SoftItem], page: PageContext) -> list[SoftItem]:
    """Fontossági sorrend: title- vagy heading-helyű előre, aztán az említésszám, aztán az első
    említés sorrendje."""
    order = {id(item): index for index, item in enumerate(items)}
    return sorted(items, key=lambda item: (
        structure(item, page, PROMINENT_KINDS) is None, -len(item.mentions), order[id(item)]))


def score_soft(page: Mapping, record: Mapping, verdicts: Mapping) -> SoftScore:
    context = PageContext(page["blocks"], lang=page.get("lang") or "en")
    items = soft_items(record.get("entities") or [], context.by_id())
    score = SoftScore(page["page_id"], items=len(items))

    def verdict_of(item):
        return verdicts.get(verdict_key(page["page_id"], item.canonical, item.type))

    for item in items:
        verdict = verdict_of(item)
        if verdict is None:
            score.unjudged.append((item.canonical, item.type))
            continue
        score.judged[verdict] = score.judged.get(verdict, 0) + 1
        tally = score.by_type.setdefault(item.type, [0, 0])
        tally[0] += verdict == "valid"
        tally[1] += 1
    order = ranked(items, context)
    for k in TOP:
        judged = [verdict_of(item) for item in order[:k] if verdict_of(item) is not None]
        score.top[k] = [judged.count("valid"), len(judged)]
    return score


def soft_total(scores: Sequence[SoftScore]) -> SoftScore:
    total = SoftScore("összesen")
    for s in scores:
        total.items += s.items
        total.unjudged += s.unjudged
        for name, count in s.judged.items():
            total.judged[name] = total.judged.get(name, 0) + count
        for kind, (valid, judged) in s.by_type.items():
            tally = total.by_type.setdefault(kind, [0, 0])
            tally[0] += valid
            tally[1] += judged
        for k, (valid, judged) in s.top.items():
            tally = total.top.setdefault(k, [0, 0])
            tally[0] += valid
            tally[1] += judged
    return total


def dropped_references(page: Mapping, base: Mapping, variant: Mapping) -> list[tuple[str, str]]:
    """A `base` concept- és service-tételei közül a `variant`-ból hiányzók, amelyek a
    referencialistában vannak: (név, hely)."""
    blocks = {b["id"]: b for b in page["blocks"]}
    kept = {alias_key(raw["canonical_name"]) for raw in variant.get("entities") or []}
    out = []
    for item in soft_items(base.get("entities") or [], blocks):
        if item.key in kept:
            continue
        place = reference_place(page, item)
        if place.startswith(("kötelező", "opcionális")):
            out.append((item.canonical, place))
    return out


# ---------------------------------------------------------------------------
# jelentés
# ---------------------------------------------------------------------------

HEADER = ("| sor | felismerés megnev. · fogalom | végső recall | precizitás megnev. | "
          "concept+service precizitás (verdikt) | concept · service | legfontosabb 10 | "
          "legfontosabb 20 | megítéletlen | USD / oldal |")
RULE = "|---|---|---|---|---|---|---|---|---|---|"


def _count(part: int, whole: int) -> str:
    return f"{se.pct_of(part, whole)} ({part}/{whole})"


def row(label: str, scores: Sequence[se.PageScore], soft: Sequence[SoftScore]) -> str:
    total, st = se.total_of(list(scores)), soft_total(soft)
    kinds = " · ".join(_count(*st.by_type.get(kind, [0, 0])) for kind in ("concept", "service"))
    return (f"| {label} | {se.pct_of(total.recognized_named, total.named)} · "
            f"{se.pct_of(total.recognized_concepts, total.concepts)} | "
            f"{_count(total.found, total.required)} | "
            f"{_count(total.good_hard, total.good_hard + total.wrong_hard)} | "
            f"{_count(st.valid, st.judged_total)} | {kinds} | {_count(*st.top[10])} | "
            f"{_count(*st.top[20])} | {len(st.unjudged)} | "
            f"{total.cost_usd / (len(scores) or 1):.4f} |")


def condition_lines(decisions: Sequence[Mapping]) -> list[str]:
    """A kapu feltételenként: hány tételnél teljesül (megtartaná) és hánynál nem."""
    concepts = [d for d in decisions if d["type"] == "concept"]
    services = [d for d in decisions if d["type"] == "service"]

    def split(items, test):
        yes = sum(1 for d in items if test(d))
        return f"{yes} teljesül / {len(items) - yes} nem"

    def alone(items, test, others):
        return sum(1 for d in items if test(d) and not any(o(d) for o in others))

    def has_structure(d):
        return d["structure"] is not None

    def repeated(d):
        return d["blocks"] >= 2

    def known(d):
        return bool(d["knowledge"])

    tests = (("(a) szerkezet", has_structure), ("(b) ismétlődés", repeated),
             ("(c) tudásbázis", known))
    lines = [(f"- concept ({len(concepts)} tétel; marad {sum(d['keep'] for d in concepts)}, "
              f"kiesik {sum(not d['keep'] for d in concepts)}):")]
    for name, test in tests:
        others = [t for n, t in tests if n != name]
        lines.append(f"  - {name}: {split(concepts, test)}; csak ez tartja meg: "
                     f"{alone(concepts, test, others)}")
    lines.append(f"- service ({len(services)} tétel; csak (a) számít): "
                 f"{split(services, has_structure)}")
    return lines


def report_markdown(model: str, data_dir: Path, pages: list[dict], series: dict[str, str],
                    verdicts_path: Path = VERDICTS_FILE) -> str:
    verdicts = verdict_index(load_verdicts(verdicts_path))
    lines = [f"# Kapu és ellenőrzés: `{model}` kinyerés, tárolt kimeneteken", "",
             ("Felismerés és végső recall a referencialistához. Megnevezett precizitás a "
              "referencialistához, a modell concept és service típusú tételei nélkül. A concept- "
              "és service-tételek precizitása a verdiktekből (`tests/acceptance/verdicts/`): "
              "valid / megítélt; a legfontosabb 10 / 20 tétel oldalanként (title- vagy "
              "heading-helyű előre, aztán az említésszám). A költség egy oldalra: a kinyerés és "
              "az ellenőrzés hívásai."), ""]
    groups = (("fő sor: kk.coach + ngx", MAIN), ("regresszió: Materia", REGRESSION))
    measured = {label: se.measure(model, data_dir, pages, tag) for label, tag in series.items()}
    records = {label: {p["page_id"]: read_record(data_dir, p["page_id"], model, tag)
                       for p in pages} for label, tag in series.items()}
    by_id = {p["page_id"]: p for p in pages}
    softs = {label: [score_soft(by_id[pid], rec, verdicts)
                     for pid, rec in recs.items() if rec is not None]
             for label, recs in records.items()}
    for title, members in groups:
        lines += [f"## {title}", "", HEADER, RULE]
        for label, tag in series.items():
            scores = [s for s in measured[label] if s.page_id in members]
            soft = [s for s in softs[label] if s.page_id in members]
            if scores:
                lines.append(row(f"{label} (`{tag}`)", scores, soft))
        lines.append("")
    lines += ["## Oldalanként", "", HEADER, RULE]
    for label, tag in series.items():
        for s in measured[label]:
            soft = [x for x in softs[label] if x.page_id == s.page_id]
            lines.append(row(f"{label} · {s.page_id}", [s], soft))
    lines.append("")
    base_label = next(iter(series))
    lines += ["## Kiejtett referenciatételek", "",
              (f"A `{series[base_label]}` concept- és service-tételei közül, amelyek a "
               "változatból hiányoznak, és a referencialistában kötelezők vagy opcionálisak."),
              ""]
    for label in list(series)[1:]:
        found = []
        for page_id, record in records[label].items():
            base = records[base_label].get(page_id)
            if record is None or base is None:
                continue
            found += [f"{name} ({page_id}; {place})"
                      for name, place in dropped_references(by_id[page_id], base, record)]
        lines.append(f"- **{label}** ({len(found)}): " + (", ".join(found) or "—"))
    lines += ["", "## A kiejtett tételek verdiktjei", ""]
    for title, members in groups:
        lines.append(f"**{title}**")
        lines.append("")
        for label in list(series)[1:]:
            counts: dict[str, int] = {}
            for page_id in members:
                record, base = records[label].get(page_id), records[base_label].get(page_id)
                if record is None or base is None:
                    continue
                blocks = {b["id"]: b for b in by_id[page_id]["blocks"]}
                kept = {alias_key(raw["canonical_name"]) for raw in record.get("entities") or []}
                for item in soft_items(base.get("entities") or [], blocks):
                    if item.key not in kept:
                        name = verdicts.get(verdict_key(page_id, item.canonical, item.type)) \
                            or "megítéletlen"
                        counts[name] = counts.get(name, 0) + 1
            lines.append(f"- **{label}** (kiesett {sum(counts.values())}): " + (", ".join(
                f"{name} {count}" for name, count in sorted(counts.items())) or "—"))
        lines.append("")
    gated = [(label, tag) for label, tag in series.items()
             if any(decisions_path(data_dir, p["page_id"], model, tag).exists() for p in pages)]
    for label, tag in gated:
        lines += ["", f"## A kapu feltételenként: {label} (`{tag}`)", ""]
        for title, members in groups:
            decisions = []
            for page_id in members:
                path = decisions_path(data_dir, page_id, model, tag)
                if path.exists():
                    decisions += json.loads(path.read_text(encoding="utf-8"))
            if decisions:
                lines += [f"**{title}**", "", *condition_lines(decisions), ""]
    lines += ["", "## Megítéletlen tételek", ""]
    for label in series:
        missing = [f"{name} ({kind}; {s.page_id})" for s in softs[label]
                   for name, kind in s.unjudged]
        lines.append(f"- **{label}** ({len(missing)}): " + (", ".join(missing) or "—"))
    return "\n".join(lines) + "\n"


def realign_record(record: Mapping, old: list[dict], new: list[dict]
                   ) -> tuple[dict, list[str]]:
    """A rekord említései az új blokk-azonosítókkal (`annotation.block_mapping`); ami eltűnt
    blokkra mutat, vagy a szöveg szerinti alakja nem áll az új blokkban, kimarad. A már a régi
    blokkokon is kitalált említés (a szöveg nincs a megadott blokkban) marad, kitaláltként."""
    mapping = block_mapping(old, new)
    blocks, before = {b["id"]: b for b in new}, {b["id"]: b for b in old}
    out, changes = dict(record), []
    entities = []
    for raw in record.get("entities") or []:
        target = mapping.get(raw["block_id"])
        source = before.get(raw["block_id"])
        if source is None or not surface_spans(raw["surface_form"], source):
            entities.append({**raw, "block_id": target or raw["block_id"]})
            continue
        if target is None or not surface_spans(raw["surface_form"], blocks[target]):
            changes.append(f"{raw['canonical_name']}: {raw['block_id']} kimarad")
            continue
        entities.append({**raw, "block_id": target})
    if record.get("entities") is not None:
        out["entities"] = entities
    return out, changes


def realign_verdicts(path: Path, page: Mapping, record: Mapping) -> int:
    """A verdiktfájl tételeinek blokkja (`item_block`) az oldal új blokkjai szerint; a verdikt
    nem változik. Visszaad: hány tétel blokkja változott."""
    data = load_verdicts(path)
    blocks = {b["id"]: b for b in page["blocks"]}
    items = {(i.key, i.type): i for i in soft_items(record.get("entities") or [], blocks)}
    changed = 0
    for entry in data["items"]:
        item = items.get((alias_key(entry["canonical"]), entry["type"]))
        if entry["page_id"] != page["page_id"] or item is None:
            continue
        block = item_block(item, blocks)
        new = {"block": block["id"], "kind": block.get("kind"), "block_text": block_text(block)}
        if any(entry.get(k) != v for k, v in new.items()):
            entry.update(new)
            changed += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["e1", "e2", "verdicts", "report", "realign"])
    parser.add_argument("--old-pages-dir", type=Path, default=None,
                        help="realign: a régi blokkok oldalai")
    parser.add_argument("--model", default=None)
    parser.add_argument("--from", dest="source", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--verify-model", default=None)
    parser.add_argument("--series", action="append", default=[],
                        help="report: név=címke; verdicts: címke")
    parser.add_argument("--page", action="append", default=[])
    parser.add_argument("--pages-dir", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=se.OUT_DIR.parent / "gate")
    args = parser.parse_args(argv)
    model = args.model or load_config().pipeline["extraction"]
    pages = load_real_pages(args.pages_dir, args.page)
    if args.command in ("e1", "e2", "realign") and (not args.source or not args.tag
                                         or args.source == args.tag):
        raise SystemExit(f"{args.command}: --from és egy tőle eltérő --tag kell")
    if args.command == "e1":
        run_e1(model, args.data_dir, pages, args.source, args.tag)
    elif args.command == "e2":
        if not args.verify_model:
            raise SystemExit("e2: --verify-model kell")
        run_e2(model, args.data_dir, pages, args.source, args.tag, args.verify_model)
    elif args.command == "realign":
        old_pages = {p["page_id"]: p for p in load_real_pages(args.old_pages_dir, args.page)}
        for page in pages:
            record = read_record(args.data_dir, page["page_id"], model, args.source)
            if record is None:
                continue
            moved, changes = realign_record(record, old_pages[page["page_id"]]["blocks"],
                                            page["blocks"])
            write_record(args.data_dir, page["page_id"], model, args.tag, moved)
            changed = realign_verdicts(VERDICTS_FILE, page, moved)
            print(f"{page['page_id']}: {args.source} → {args.tag}: {len(changes)} említés "
                  f"kimarad, {changed} verdikt-tétel blokkja változott")
            for change in changes:
                print(f"  {change}")
    elif args.command == "verdicts":
        added = export_verdicts(model, args.data_dir, pages,
                                [s.split("=", 1)[-1] for s in args.series])
        print(f"{VERDICTS_FILE}: {added} új tétel")
    else:
        series = dict(item.split("=", 1) for item in args.series)
        args.out.mkdir(parents=True, exist_ok=True)
        out = args.out / f"{model}-gate.md"
        out.write_text(report_markdown(model, args.data_dir, pages, series), encoding="utf-8")
        print(out)


if __name__ == "__main__":
    main()
