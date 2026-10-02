"""A megközelítés v3 a mérőkörnyezetben (M2 spec, „Megközelítés v3”): a kinyerés utáni szabály
és a pontozás, a zárolt és a fejlesztési oldalakon.

    python -m tests.acceptance.v3_eval apply --set locked --from v3 --tag v3p
    python -m tests.acceptance.v3_eval apply --set development-real --page kk_coach_meres_hu \\
        --page ngx_accordion_en --from v3 --tag v3p

A szabály (`aaa2.entities.v3.apply_v3`, a pipeline is ezt futtatja), oldalanként, a `--from`
kör rekordján (Luna-kinyerés, 3a prompt, elnevezés nélkül): a megnevezett entitás
változatlan; a service szerkezeti helyen és a Sol vétója nélkül marad; a concept kapu nélkül
marad, bizonyítékkal és fontossági sorszámmal (lásd az `aaa2/entities/v3.py` leírását).

Pontozás (`report`, hálózat nélkül, a végleges referencialistán; `verdicts`: a még nem
megítélt tételek a verdiktfájlba):

- megnevezett entitások (a referencia nem concept és nem service típusú kötelező tételei):
  felismerés és végső recall (`synthetic_eval.score_page` szabályával); precizitás a modell
  nem concept és nem service típusú csoportjain, a referencialistához (`good_hard`);
- saját ajánlatok: recall a referencia service típusú kötelező tételein a szabály után;
  precizitás a megmaradt service-tételeken, verdikt alapján;
- fogalmak: a kötelező (concept típusú kötelező) tételek felismerése; a legfontosabb 10 és 20
  fogalom (a `v3` fontossági sorszáma szerint) precizitása verdikt alapján;
- költség és token oldalanként: a kinyerés hívásai és a Sol-hívás (`llm_calls`).

A fő sor a zárolt oldalak összege, mellette a fejlesztési oldalaké; oldalanként is.

A navigáció és az anchor-szövegek a rögzített készlet renderelt DOM-jából jönnek
(`gate_eval.page_context`). A kimenet a `--tag` alá kerül; ha a Sol-hívás hibára fut, az oldalnak nincs kimenete. A konzolra csak darabszám.
"""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx

import tests.acceptance.gate_eval as ge
import tests.acceptance.synthetic_eval as se
from aaa2.db.connect import DATA_DIR, connect
from aaa2.entities.blocks import block_text
from aaa2.entities.gate import KnowledgeBase, soft_items
from aaa2.entities.rules import alias_key
from aaa2.entities.v3 import apply_v3, service_place  # noqa: F401  (a mérés és a tesztek innen)
from aaa2.entities.verify import item_block
from aaa2.llm.client import Retry
from aaa2.llm.config import load_config
from aaa2.resolver.validate import _Api

SOL = "gpt-6-sol"


def run_apply(model: str, data_dir: Path, pages: list[dict], source: str, tag: str) -> None:
    con = connect(data_dir / "synthetic.duckdb")
    cache = connect(data_dir / "gate.duckdb")
    try:
        verifier = se._client(con, SOL)
        api = _Api(cache, None, httpx.Client(timeout=20.0), Retry(),
                   lambda: datetime.now(UTC).replace(tzinfo=None), time.monotonic)
        knowledge = KnowledgeBase(api.get)
        for page in pages:
            record = ge.read_record(data_dir, page["page_id"], model, source)
            if record is None or record.get("entities") is None:
                print(f"{page['page_id']}: nincs kinyerés ({source})")
                continue
            out = apply_v3(record, ge.page_context(page, data_dir), verifier, knowledge)
            if out.get("verify_error"):
                print(f"{page['page_id']}: a Sol-hívás hibára futott, nincs kimenet: "
                      f"{out['verify_error']}")
                continue
            out["v3_source"] = source
            ge.write_record(data_dir, page["page_id"], model, tag, out)
            services = out["v3"]["services"]
            print(f"{page['page_id']}: {source} → {tag}: szolgáltatás {len(services)} "
                  f"(marad {sum(s['kept'] for s in services)}), fogalom "
                  f"{len(out['v3']['concepts'])}; Sol-hívás "
                  f"{'van' if out.get('verify_call_id') else 'nincs'}"
                  + (f"; hiba: {out['verify_error']}" if out.get("verify_error") else ""))
    finally:
        con.close()
        cache.close()


SOFT = ("concept", "service")
TOP = (10, 20)


@dataclass
class V3Score:
    page_id: str
    named: int = 0
    named_recognized: int = 0
    named_found: int = 0
    good_hard: int = 0
    wrong_hard: int = 0
    services: int = 0
    services_found: int = 0
    service_verdicts: list = field(default_factory=list)     # a megmaradt service-tételek
    concepts: int = 0
    concepts_recognized: int = 0
    top: dict = field(default_factory=dict)                  # k → verdiktek (None: nincs)
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    missed: list = field(default_factory=list)               # (név, típus, ok)
    false_named: list = field(default_factory=list)          # (név, típus): téves megnevezett
    rule_dropped: list = field(default_factory=list)         # (név, hely): a szabály ejtette ki


def call_totals(data_dir: Path, ids) -> tuple[float, int, int]:
    db = data_dir / "synthetic.duckdb"
    ids = [i for i in ids if i is not None]
    if not ids or not db.exists():
        return 0.0, 0, 0
    con = duckdb.connect(str(db), read_only=True)
    try:
        row = con.execute("SELECT coalesce(sum(cost_usd), 0), coalesce(sum(tokens_in), 0), "
                          "coalesce(sum(tokens_out), 0) FROM llm_calls WHERE "
                          "list_contains(?, call_id)", [ids]).fetchone()
    finally:
        con.close()
    return float(row[0]), int(row[1]), int(row[2])


def score_v3(page: Mapping, record: Mapping, verdicts: Mapping, data_dir: Path | None = None,
             source: Mapping | None = None) -> V3Score:
    """`source`: a szabály előtti rekord; ha megvan, a szabály által kiejtett
    referenciatételek a `rule_dropped`-ba kerülnek."""
    base = se.score_page(page, record)
    missed = {name: reason for name, _, _, reason in base.missed}
    score = V3Score(page["page_id"], good_hard=base.good_hard, wrong_hard=base.wrong_hard)
    score.false_named = [(name, kind) for name, kind, _ in base.false_hits if kind not in SOFT]
    if source is not None:
        score.rule_dropped = ge.dropped_references(page, source, record)
    for item in page["gold"]["entities"]:
        found = item["canonical"] not in missed
        recognized = found or missed.get(item["canonical"]) == se.NAMING
        if item["type"] == "concept":
            score.concepts += 1
            score.concepts_recognized += recognized
        elif item["type"] == "service":
            score.services += 1
            score.services_found += found
        else:
            score.named += 1
            score.named_recognized += recognized
            score.named_found += found
        if not found:
            score.missed.append((item["canonical"], item["type"], missed[item["canonical"]]))
    blocks = {b["id"]: b for b in page["blocks"]}
    score.service_verdicts = [
        verdicts.get(ge.verdict_key(page["page_id"], item.canonical, item.type))
        for item in soft_items(record.get("entities") or [], blocks) if item.type == "service"]
    concepts = (record.get("v3") or {}).get("concepts") or []
    for k in TOP:
        score.top[k] = [verdicts.get(ge.verdict_key(page["page_id"], c["canonical"], "concept"))
                        for c in concepts[:k]]
    if data_dir is not None:
        ids = list(record.get("call_ids") or [record.get("call_id")])
        score.cost_usd, score.tokens_in, score.tokens_out = call_totals(data_dir, ids)
    return score


def export_v3_verdicts(pages: list[dict], records: Mapping, path: Path = ge.VERDICTS_FILE
                       ) -> int:
    """A megmaradt service-tételek és a legfontosabb 20 fogalom közül a még nem megítéltek a
    verdiktfájl végére; visszaad: hány új tétel."""
    data = ge.load_verdicts(path)
    known = ge.verdict_index(data)
    added = 0
    for page in pages:
        record = records.get(page["page_id"])
        if record is None:
            continue
        blocks = {b["id"]: b for b in page["blocks"]}
        items = {(item.key, item.type): item
                 for item in soft_items(record.get("entities") or [], blocks)}
        top = {alias_key(c["canonical"]) for c in (record.get("v3") or {}).get(
            "concepts", [])[:max(TOP)]}
        wanted = [item for (key, kind), item in items.items()
                  if kind == "service" or (kind == "concept" and key in top)]
        for item in wanted:
            key = ge.verdict_key(page["page_id"], item.canonical, item.type)
            if key in known:
                continue
            block = item_block(item, blocks)
            data["items"].append({
                "page_id": page["page_id"], "canonical": item.canonical, "type": item.type,
                "block": block["id"], "kind": block.get("kind"),
                "block_text": block_text(block), "reference": ge.reference_place(page, item),
                "verdict": None, "note": ""})
            known[key] = None
            added += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return added


def _pct(part: int, whole: int) -> str:
    return f"{se.pct_of(part, whole)} ({part}/{whole})"


def _valid(verdicts_: list) -> tuple[int, int]:
    judged = [v for v in verdicts_ if v is not None]
    return judged.count("valid"), len(judged)


def total(scores: list[V3Score], label: str) -> V3Score:
    out = V3Score(label)
    for s in scores:
        for name in ("named", "named_recognized", "named_found", "good_hard", "wrong_hard",
                     "services", "services_found", "concepts", "concepts_recognized",
                     "tokens_in", "tokens_out"):
            setattr(out, name, getattr(out, name) + getattr(s, name))
        out.cost_usd += s.cost_usd
        out.service_verdicts += s.service_verdicts
        for k in TOP:
            out.top.setdefault(k, []).extend(s.top.get(k, []))
    return out


HEADER = ("| sor | megnev. felismerés | megnev. végső recall | megnev. precizitás | "
          "ajánlat recall | ajánlat precizitás (verdikt) | kötelező fogalom felismerés | "
          "fogalom top 10 | fogalom top 20 | megítéletlen | USD / oldal | token be / ki / oldal |")
RULE = "|---|---|---|---|---|---|---|---|---|---|---|---|"


def row(label: str, s: V3Score, pages: int) -> str:
    unjudged = (s.service_verdicts.count(None) + sum(v is None for v in s.top.get(20, [])))
    return (f"| {label} | {_pct(s.named_recognized, s.named)} | {_pct(s.named_found, s.named)} "
            f"| {_pct(s.good_hard, s.good_hard + s.wrong_hard)} | "
            f"{_pct(s.services_found, s.services)} | {_pct(*_valid(s.service_verdicts))} | "
            f"{_pct(s.concepts_recognized, s.concepts)} | {_pct(*_valid(s.top.get(10, [])))} | "
            f"{_pct(*_valid(s.top.get(20, [])))} | {unjudged} | "
            f"{s.cost_usd / (pages or 1):.4f} | {s.tokens_in // (pages or 1)} / "
            f"{s.tokens_out // (pages or 1)} |")


def goals(s: V3Score) -> list[str]:
    checks = [("megnevezett precizitás", s.good_hard, s.good_hard + s.wrong_hard),
              ("megnevezett végső recall", s.named_found, s.named),
              ("saját ajánlat precizitás", *_valid(s.service_verdicts)),
              ("saját ajánlat recall", s.services_found, s.services),
              ("kötelező fogalom felismerés", s.concepts_recognized, s.concepts)]
    out = []
    for name, part, whole in checks:
        mark = "—" if not whole else ("teljesül" if part / whole >= 0.9 else "NEM")
        out.append(f"- {name}: {_pct(part, whole)} (≥ 90: {mark})")
    return out


def previous_lines(groups: list[tuple[str, list[V3Score]]],
                   previous: Mapping[str, tuple[int, int]]) -> list[str]:
    """A megnevezett precizitás a korábbi és a mostani referencialistával, egymás mellett."""
    lines = ["## Megnevezett precizitás: korábbi és végleges referencialista", "",
             "| sor | korábbi | végleges | változás (pont) |", "|---|---|---|---|"]

    def cells(label, scores):
        old = [previous[s.page_id] for s in scores if s.page_id in previous]
        if not old:
            return None
        good, wrong = sum(g for g, _ in old), sum(w for _, w in old)
        new_good = sum(s.good_hard for s in scores if s.page_id in previous)
        new_all = sum(s.good_hard + s.wrong_hard for s in scores if s.page_id in previous)
        before = good / (good + wrong) if good + wrong else None
        after = new_good / new_all if new_all else None
        delta = "—" if before is None or after is None else f"{(after - before) * 100:+.1f}"
        return (f"| {label} | {_pct(good, good + wrong)} | {_pct(new_good, new_all)} | "
                f"{delta} |")

    for label, scores in groups:
        if line := cells(f"**{label}**", scores):
            lines.append(line)
        lines += [line for s in scores if (line := cells(s.page_id, [s]))]
    return lines + [""]


def report_markdown(groups: list[tuple[str, list[V3Score]]],
                    previous: Mapping[str, tuple[int, int]] | None = None) -> str:
    """`previous`: oldalanként (jó, hibás) megnevezett csoport a korábbi referencialistával."""
    lines = ["# Megközelítés v3: zárolt és fejlesztési oldalak", "",
             ("Luna-kinyerés (3a prompt, elnevezés nélkül, darabolva), utána a v3 szabály "
              "(`tests/acceptance/v3_eval.py`). A fő sor a zárolt oldalak összege; a fejlesztési "
              "oldalak csak összehasonlításra. A költség a kinyerés és a Sol-hívás."), "",
             HEADER, RULE]
    for label, scores in groups:
        lines.append(row(f"**{label}**", total(scores, label), len(scores)))
    if previous:
        lines += ["", *previous_lines(groups, previous)]
    for label, scores in groups:
        lines += ["", f"## {label}", "", HEADER, RULE]
        lines += [row(s.page_id, s, 1) for s in scores]
        lines += ["", "**Célok (≥ 90%):**", "", *goals(total(scores, label)), "",
                  "**Kihagyott kötelező tételek:**", ""]
        for s in scores:
            lines.append(f"- {s.page_id}: " + (", ".join(
                f"{name} ({kind}; {why})" for name, kind, why in s.missed) or "—"))
        lines += ["", "**A szabály által kiejtett referenciatételek** (concept és service):", ""]
        lines += [f"- {s.page_id}: " + (", ".join(
            f"{name} ({place})" for name, place in s.rule_dropped) or "—") for s in scores]
        lines += ["", "**Téves megnevezett találatok** (a referencián kívül vagy negatív):", ""]
        lines += [f"- {s.page_id}: " + (", ".join(
            f"{name} ({kind})" for name, kind in s.false_named) or "—") for s in scores]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["apply", "verdicts", "report"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--from", dest="source", default="v3")
    parser.add_argument("--tag", default="v3p")
    parser.add_argument("--set", dest="page_set", choices=(se.LOCKED, se.REAL), default=se.LOCKED)
    parser.add_argument("--page", action="append", default=[])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--dev-page", action="append", default=[],
                        help="verdicts, report: a fejlesztési oldalak (alapból kk.coach mérés "
                             "és ngx Accordion)")
    parser.add_argument("--out", type=Path, default=se.OUT_DIR.parent / "v3")
    parser.add_argument("--previous-pages-dir", type=Path, default=None,
                        help="report: a zárolt oldalak korábbi referencialistája (a megnevezett "
                             "precizitás összevetéséhez)")
    args = parser.parse_args(argv)
    model = args.model or load_config().pipeline["extraction"]
    if args.command == "apply":
        pages = [p for p in se.load_pages(page_set=args.page_set)
                 if not args.page or p["page_id"] in args.page]
        run_apply(model, args.data_dir, pages, args.source, args.tag)
        return
    locked = se.load_pages(page_set=se.LOCKED)
    wanted = args.dev_page or ["kk_coach_meres_hu", "ngx_accordion_en"]
    dev = [p for p in se.load_pages(page_set=se.REAL) if p["page_id"] in wanted]
    records = {p["page_id"]: ge.read_record(args.data_dir, p["page_id"], model, args.tag)
               for p in locked + dev}
    if args.command == "verdicts":
        added = export_v3_verdicts(locked + dev, records)
        print(f"{ge.VERDICTS_FILE}: {added} új tétel")
        return
    verdicts = ge.verdict_index(ge.load_verdicts())
    groups = [(label, [score_v3(p, records[p["page_id"]], verdicts, args.data_dir,
                                ge.read_record(args.data_dir, p["page_id"], model, args.source))
                       for p in pages if records.get(p["page_id"]) is not None])
              for label, pages in (("zárolt oldalak", locked), ("fejlesztési oldalak", dev))]
    previous = None
    if args.previous_pages_dir is not None:
        previous = {}
        for page in se.load_pages(args.previous_pages_dir, se.LOCKED):
            record = records.get(page["page_id"])
            if record is not None:
                old = se.score_page(page, record)
                previous[page["page_id"]] = (old.good_hard, old.wrong_hard)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{model}-{args.tag}-report.md"
    out.write_text(report_markdown(groups, previous), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
