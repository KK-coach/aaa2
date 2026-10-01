"""M3/2 mérés: a megállapítások (aaa2/functions/findings.py) kézi verdiktje és a valós
megállapítások aránya (M3 spec, 8. pont: cél ≥ 90%).

    python -m tests.acceptance.m3_findings template --site kk-coach=data/m3/kk-coach.duckdb ...
    python -m tests.acceptance.m3_findings report --site kk-coach=data/m3/kk-coach.duckdb ...

- `template`: a site-ok megállapításai kulccsal (típus | entitás | oldal, csoport vagy nyelv) a
  verdiktfájlba (`tests/acceptance/m3/findings_verdicts.json`); a meglévő verdikt megmarad, az
  új tétel verdiktje üres.
- `report`: site-onként és típusonként a valós (`valid`) megállapítások aránya; a verdikt
  nélküli és a már nem létező tételek külön sorban. Verdikt: `valid` (valós probléma) vagy
  `invalid` (nem az), rövid indoklással.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import duckdb

VERDICTS_FILE = Path(__file__).parent / "m3" / "findings_verdicts.json"
TARGET = 0.9


def finding_keys(db: Path) -> dict[str, str]:
    """kulcs → összefoglaló a site megállapításaira."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        found = {}
        for kind, summary, evidence, name in con.execute(
                "SELECT f.type, f.summary, f.evidence, e.name FROM findings f "
                "LEFT JOIN entities e USING (entity_id) ORDER BY f.finding_id").fetchall():
            data = json.loads(evidence)
            where = data.get("url") or data.get("group") or data.get("lang") or ""
            found[" | ".join(filter(None, (kind, name or "", where)))] = summary
        return found
    finally:
        con.close()


def template(sites: dict[str, Path], path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "status": "", "sites": {}}
    added = 0
    for site, db in sites.items():
        known = data["sites"].setdefault(site, {})
        for key, summary in finding_keys(db).items():
            if key not in known:
                known[key] = {"summary": summary, "verdict": "", "note": ""}
                added += 1
            else:
                known[key]["summary"] = summary
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return added


def report(sites: dict[str, Path], path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = [f"verdiktek: {data.get('status') or '—'}"]
    total: Counter = Counter()
    for site, db in sites.items():
        known = data["sites"].get(site, {})
        keys = finding_keys(db)
        counts: Counter = Counter()
        by_type: dict[str, Counter] = {}
        for key in keys:
            verdict = known.get(key, {}).get("verdict") or "nincs verdikt"
            counts[verdict] += 1
            by_type.setdefault(key.split(" | ", 1)[0], Counter())[verdict] += 1
        judged = counts["valid"] + counts["invalid"]
        share = f"{100 * counts['valid'] / judged:.1f}%" if judged else "—"
        lines.append(
            f"{site}: {len(keys)} megállapítás, valós {counts['valid']}/{judged} ({share}), "
            f"verdikt nélkül {counts['nincs verdikt']}, már nem létező verdikt "
            f"{len(set(known) - set(keys))}; "
            + "; ".join(f"{kind} {c['valid']}/{c['valid'] + c['invalid']}"
                        for kind, c in sorted(by_type.items())))
        total.update(counts)
    judged = total["valid"] + total["invalid"]
    if judged:
        share = total["valid"] / judged
        lines.append(f"összesen: valós {total['valid']}/{judged} ({100 * share:.1f}%), cél "
                     f"{100 * TARGET:.0f}%: {'teljesül' if share >= TARGET else 'nem teljesül'}; "
                     f"verdikt nélkül {total['nincs verdikt']}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["template", "report"])
    parser.add_argument("--site", action="append", default=[], help="név=adatbázis")
    parser.add_argument("--verdicts", type=Path, default=VERDICTS_FILE)
    args = parser.parse_args()
    sites = {name: Path(db) for name, db in (item.split("=", 1) for item in args.site)}
    if not sites:
        raise SystemExit("--site név=adatbázis kell")
    if args.command == "template":
        print(f"{args.verdicts}: {template(sites, args.verdicts)} új tétel")
    else:
        print("\n".join(report(sites, args.verdicts)))


if __name__ == "__main__":
    main()
