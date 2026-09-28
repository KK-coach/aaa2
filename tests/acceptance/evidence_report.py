"""A bizonyíték-jelentés (`tests.acceptance.evidence report`) Markdownja: bizonyíték-tábla,
döntési kombinációk, a volumen mint jel, célok, entitás-áttekintők."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import duckdb

import tests.acceptance.gate_eval as ge
import tests.acceptance.synthetic_eval as se
from tests.acceptance.evidence import (
    OVERVIEW_FIELDS,
    THRESHOLDS,
    Evidence,
    Row,
    apply,
    combos,
    markdown_table,
    overview_record,
    rule,
)

TARGET = 0.90
GROUPS = (("fő sor: kk.coach + ngx", ge.MAIN), ("regresszió: Materia", ge.REGRESSION))


def call_costs(data_dir: Path, ids: Sequence[int]) -> float:
    db = data_dir / "synthetic.duckdb"
    if not ids or not db.exists():
        return 0.0
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute("SELECT coalesce(sum(cost_usd), 0) FROM llm_calls "
                           "WHERE list_contains(?, call_id)", [list(ids)]).fetchone()[0]
    finally:
        con.close()


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}"


def combo_results(pages: Sequence[Mapping], records: Mapping, evidence: Mapping,
                  verdicts: Mapping, costs: Mapping[str, tuple[float, float]]) -> list[dict]:
    """Kombinációnként és csoportonként: precizitás (verdikt), concept · service, legfontosabb
    10 / 20, végső recall, kiejtett referenciatételek, LLM-költség / oldal."""
    by_id = {p["page_id"]: p for p in pages}
    results = []
    for label, name, threshold in combos():
        keep = (lambda e: True) if name == "none" else rule(name, threshold)
        uses_sol = name in ("b", "c", "d")
        for title, members in GROUPS:
            present = [pid for pid in members if records.get(pid) is not None]
            scores, softs, dropped, cost = [], [], [], 0.0
            for pid in present:
                filtered = apply(records[pid], evidence[pid], keep)
                scores.append(se.score_page(by_id[pid], filtered))
                softs.append(ge.score_soft(by_id[pid], filtered, verdicts))
                dropped += [f"{n} ({pid}; {place})" for n, place in
                            ge.dropped_references(by_id[pid], records[pid], filtered)]
                extraction, sol = costs[pid]
                cost += extraction + (sol if uses_sol else 0.0)
            total, soft = se.total_of(scores), ge.soft_total(softs)
            results.append({
                "label": label, "name": name, "threshold": threshold, "group": title,
                "precision": _ratio(soft.valid, soft.judged_total),
                "valid": soft.valid, "judged": soft.judged_total,
                "concept": soft.by_type.get("concept", [0, 0]),
                "service": soft.by_type.get("service", [0, 0]),
                "top10": soft.top.get(10, [0, 0]), "top20": soft.top.get(20, [0, 0]),
                "recall": _ratio(total.found, total.required), "found": total.found,
                "required": total.required, "dropped": dropped,
                "cost": cost / (len(present) or 1), "volume": threshold is not None,
                "unjudged": len(soft.unjudged)})
    return results


def _count(pair) -> str:
    part, whole = pair
    return f"{se.pct_of(part, whole)} ({part}/{whole})"


def combo_table(results: Sequence[dict], group: str) -> list[str]:
    lines = [("| kombináció | concept+service precizitás | concept · service | legfontosabb "
              "10 · 20 | végső recall | kiejtett referenciatétel | USD / oldal (LLM) | volumen |"),
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r["group"] != group:
            continue
        lines.append(
            f"| {r['label']} | {_pct(r['precision'])} ({r['valid']}/{r['judged']}) | "
            f"{_count(r['concept'])} · {_count(r['service'])} | "
            f"{_count(r['top10'])} · {_count(r['top20'])} | "
            f"{_pct(r['recall'])} ({r['found']}/{r['required']}) | {len(r['dropped'])} | "
            f"{r['cost']:.4f} | {'igen' if r['volume'] else '—'} |")
    return lines


def evidence_table(evidence: Mapping[str, Sequence[Evidence]]) -> list[str]:
    def flag(value):
        return "—" if value is None else ("marad" if value else "kiesik")

    def vol(value):
        return "—" if value is None else str(value)

    lines = [("| oldal | tétel | típus | verdikt | volumen HU | volumen EN | tudásbázis | "
              "szerkezet | blokkok | title / heading | Luna | Sol |"),
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for pid, items in evidence.items():
        for e in items:
            lines.append(
                f"| {pid} | {e.canonical} | {e.type} | {e.verdict or 'megítéletlen'} | "
                f"{vol(e.volume_hu)} | {vol(e.volume_en)} | {e.knowledge or '—'} | "
                f"{e.structure or '—'} | {e.blocks} | {'igen' if e.prominent else 'nem'} | "
                f"{flag(e.luna)} | {flag(e.sol)} |")
    return lines


def volume_signal(evidence: Mapping[str, Sequence[Evidence]], members: Sequence[str]
                  ) -> list[str]:
    items = [e for pid in members for e in evidence.get(pid, [])]
    lines = [("| küszöb | valid volumennel | descriptive volumennel | wrong_name / wrong_type "
              "volumennel | csak a volumen tartja meg: valid | … descriptive | … egyéb |"),
             "|---|---|---|---|---|---|---|"]
    for threshold in THRESHOLDS:
        having = [e for e in items if e.volume >= threshold]
        alone = [e for e in having if e.structure is None and not e.knowledge]
        lines.append(
            f"| {'> 0' if threshold == 1 else f'≥ {threshold}'} | "
            f"{sum(e.verdict == 'valid' for e in having)}/"
            f"{sum(e.verdict == 'valid' for e in items)} | "
            f"{sum(e.verdict == 'descriptive' for e in having)}/"
            f"{sum(e.verdict == 'descriptive' for e in items)} | "
            f"{sum(e.verdict in ('wrong_name', 'wrong_type') for e in having)}/"
            f"{sum(e.verdict in ('wrong_name', 'wrong_type') for e in items)} | "
            f"{sum(e.verdict == 'valid' for e in alone)} | "
            f"{sum(e.verdict == 'descriptive' for e in alone)} | "
            f"{sum(e.verdict not in ('valid', 'descriptive') for e in alone)} |")
    return lines


def targets(results: Sequence[dict], group: str) -> list[str]:
    rows = [r for r in results if r["group"] == group and r["precision"] is not None
            and r["recall"] is not None]
    met = [r for r in rows if r["precision"] >= TARGET and r["recall"] >= TARGET]
    if met:
        return ["- **Teljesül** (precizitás ≥ 90 és recall ≥ 90): " + ", ".join(
            f"{r['label']} ({_pct(r['precision'])} / {_pct(r['recall'])})" for r in met)]

    def harmonic(r):
        p, q = r["precision"], r["recall"]
        return 2 * p * q / (p + q) if p + q else 0.0

    best = sorted(rows, key=harmonic, reverse=True)[:2]
    lines = [("- **Egyik kombináció sem teljesíti mindkét célt.** A legjobb két kompromisszum "
              "(a precizitás és a recall harmonikus közepe szerint):")]
    lines += [f"  - {r['label']}: precizitás {_pct(r['precision'])}, recall {_pct(r['recall'])}, "
              f"{len(r['dropped'])} kiejtett referenciatétel" for r in best]
    high = [r for r in rows if r["recall"] >= TARGET]
    if high:
        r = max(high, key=lambda r: r["precision"])
        lines.append(f"  - a legjobb precizitás ≥ 90 recallal: {r['label']} "
                     f"({_pct(r['precision'])} / {_pct(r['recall'])})")
    return lines


def write_report(args, model: str, pages: Sequence[Mapping], records: Mapping,
                 rows: Mapping[str, list[Row]], evidence: Mapping[str, list[Evidence]],
                 volumes: Mapping[str, dict], verdicts: Mapping) -> Path:
    costs = {}
    for page in pages:
        pid = page["page_id"]
        record = records[pid] or {}
        sol = ge.read_record(args.data_dir, pid, model, args.sol) or {}
        extraction_ids = record.get("call_ids") or [i for i in [record.get("call_id")] if i]
        sol_ids = [i for i in [sol.get("verify_call_id")] if i]
        costs[pid] = (call_costs(args.data_dir, extraction_ids),
                      call_costs(args.data_dir, sol_ids))
    results = combo_results(pages, records, evidence, verdicts, costs)
    dfs_cost = sum(entry.get("cost") or 0.0 for entry in volumes.values())
    lines = [f"# Bizonyíték-tábla és döntési kombinációk: `{model}`, `{args.source}`", "",
             ("Tárolt kimeneteken, új kinyerés nélkül. A concept- és service-precizitás a "
              "verdiktfájlból, a végső recall a referencialistából. A kombinációk szabályai a "
              "`tests/acceptance/evidence.py` leírásában."), "",
             "## Keresési volumen (DataForSEO Google Ads, normál sor)", ""]
    for market, entry in volumes.items():
        results_ = entry.get("results") or {}
        with_data = sum(v is not None for v in results_.values())
        positive = sum(bool(v) for v in results_.values())
        lines.append(f"- {market}: {len(entry['keywords'])} kulcsszó, feladat "
                     f"`{entry['task_id']}`, költség {entry.get('cost')} USD; adat "
                     f"{with_data}, ebből > 0: {positive}")
    lines += [f"- összesen {dfs_cost:.4f} USD", ""]
    for title, members in GROUPS:
        lines += [f"## Döntési kombinációk — {title}", "", *combo_table(results, title), "",
                  "**Célok (precizitás ≥ 90, végső recall ≥ 90):**", "",
                  *targets(results, title), ""]
    lines += ["## Kiejtett referenciatételek kombinációnként (fő sor)", ""]
    for r in results:
        if r["group"] == GROUPS[0][0] and r["dropped"]:
            lines.append(f"- **{r['label']}** ({len(r['dropped'])}): " + ", ".join(r["dropped"]))
    lines += ["", "## A volumen mint jel (fő sor)", "",
              ("Tételenként a HU és az EN volumen közül a nagyobb. „Csak a volumen tartja meg”: "
               "a volumen eléri a küszöböt, de sem szerkezeti helye, sem tudásbázis-egyezése "
               "nincs (az ismétlődéstől függetlenül)."), "",
              *volume_signal(evidence, ge.MAIN), "",
              "A kombinációk volumen nélküli sorai a fenti táblázatban: „volumen nélkül”.", "",
              "## Bizonyíték-tábla (concept és service)", "", *evidence_table(evidence), ""]
    for page in pages:
        lines += [f"## Entitás-áttekintő: {page['page_id']}", "",
                  *markdown_table([overview_record(r) for r in rows[page["page_id"]]],
                                  OVERVIEW_FIELDS), ""]
    out = args.out / "evidence" / f"{model}-evidence.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
