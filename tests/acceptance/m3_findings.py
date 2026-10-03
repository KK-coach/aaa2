"""M3/2 mérés: a megállapítások (aaa2/functions/findings.py) kézi verdiktje és a valós
megállapítások aránya (M3 spec, 8. pont: cél ≥ 90%).

    python -m tests.acceptance.m3_findings template --site kk-coach=data/m3/kk-coach.duckdb ...
    python -m tests.acceptance.m3_findings report --site kk-coach=data/m3/kk-coach.duckdb ...
    python -m tests.acceptance.m3_findings variants --site ... [--out data/m3/out/x.csv]
    python -m tests.acceptance.m3_findings prune --site kk-coach=data/m3/kk-coach.duckdb ...

- `template`: a site-ok megállapításai kulccsal (típus | entitás | oldal, csoport vagy nyelv) a
  verdiktfájlba (`tests/acceptance/m3/findings_verdicts.json`); a meglévő verdikt megmarad, az
  új tétel verdiktje üres.
- `report`: site-onként és típusonként a valós (`valid`) megállapítások aránya; a verdikt
  nélküli és a már nem létező tételek külön sorban. Verdikt: `valid` (valós probléma) vagy
  `invalid` (nem az), rövid indoklással.
- `prune`: a megadott site-ok már nem létező megállapításainak verdiktje törlődik a
  verdiktfájlból (a szabály változása után megszűnt tételek).
- `variants`: a lefedetlen téma és a hiányzó oldal jelöltjei négy szabályváltozat szerint,
  verdikt nélkül (döntés-előkészítés): `régi` (kontextus: az indexelhető oldalak legalább
  60%-án szerepel, vagy a neve a site nevének része; kiemelés legalább 3 oldalcsoportban), `kontextus` (az élő szabály: az
  említések többsége sablon- vagy chrome-helyen, vagy a név a site nevében), `küszöb` (a régi
  kontextus, de `SMALL_SITE_GROUPS`-nál kevesebb oldalcsoportú site-on 2 csoport elég a
  kiemeléshez), `mindkettő`, és `élő` (a mostani szabály: az új kontextus, a kis site
  küszöbe, a H1-ben vagy title-ben megnevezett és az egyszavas köznévi fogalom kizárása).
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import duckdb

from aaa2.functions.findings import (
    MIN_STRUCTURAL,
    SMALL_SITE_GROUPS,
    SMALL_SITE_STRUCTURAL,
    _Site,
    exclusion,
    is_context,
    uncovered_candidates,
)

VERDICTS_FILE = Path(__file__).parent / "m3" / "findings_verdicts.json"
TARGET = 0.9
OLD_CONTEXT_SHARE = 0.6
VARIANTS = ("régi", "kontextus", "küszöb", "mindkettő", "élő")


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


def prune(sites: dict[str, Path], path: Path) -> dict[str, int]:
    """A megadott site-ok már nem létező megállapításainak verdiktje törölve: site → darab."""
    data = json.loads(path.read_text(encoding="utf-8"))
    removed = {}
    for site, db in sites.items():
        known, keys = data["sites"].get(site, {}), finding_keys(db)
        gone = [key for key in known if key not in keys]
        for key in gone:
            del known[key]
        removed[site] = len(gone)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return removed


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


def old_rule(candidate: dict) -> bool:
    """A korábbi szabály szerint megmarad-e: az indexelhető oldalak kevesebb mint 60%-án
    szerepel, és a neve nem a site nevének része (azt a „lefedett” feltétel zárta ki)."""
    return candidate["page_share"] < OLD_CONTEXT_SHARE and not candidate["in_site_name"]


def variants(sites: dict[str, Path], out: Path) -> list[str]:
    rows, lines = [], []
    for name, db in sites.items():
        con = duckdb.connect(str(db), read_only=True)
        try:
            site = _Site(con)
            groups = len({p["group"] for p in site.nodes()})
            low = SMALL_SITE_STRUCTURAL if groups < SMALL_SITE_GROUPS else MIN_STRUCTURAL
            strict = {c["entity_id"]: c for c in uncovered_candidates(
                site, min_structural=MIN_STRUCTURAL)}
            loose = {c["entity_id"]: c for c in uncovered_candidates(site, min_structural=low)}
            sets = {
                "régi": {e for e, c in strict.items() if old_rule(c)},
                "kontextus": {e for e, c in strict.items() if not is_context(c)},
                "küszöb": {e for e, c in loose.items() if old_rule(c)},
                "mindkettő": {e for e, c in loose.items() if not is_context(c)},
                "élő": {c["entity_id"] for c in uncovered_candidates(site)
                        if exclusion(c) is None}}
            lines.append(f"{name}: {groups} oldalcsoport, kiemelési küszöb a 2. ponttal {low}; "
                         + ", ".join(f"{k} {len(v)}" for k, v in sets.items()))
            for variant in VARIANTS[1:]:
                plus = sorted(site.name(e) for e in sets[variant] - sets["régi"])
                minus = sorted(site.name(e) for e in sets["régi"] - sets[variant])
                lines.append(f"  {variant}: + {', '.join(plus) or '—'}; − "
                             f"{', '.join(minus) or '—'}")
            for entity_id, c in sorted(loose.items(), key=lambda item: site.rank[item[0]]):
                rows.append({
                    "site": name, "entitás": site.name(entity_id), "típus": site.kind(entity_id),
                    "fajta": "hiányzó oldal" if c["family"] else "lefedetlen téma",
                    **{variant: "igen" if entity_id in sets[variant] else "nem"
                       for variant in VARIANTS},
                    "oldalak": c["weight"]["pages"], "említések": c["weight"]["mentions"],
                    "oldalcsoportok": c["page_groups"], "kiemelve (csoport)": c["structural"],
                    "oldalarány": round(c["page_share"], 2),
                    "sablon- és chrome-arány": round(c["template_share"], 2),
                    "a site nevében": "igen" if c["in_site_name"] else "nem",
                    "kizárás (élő)": exclusion(c) or "",
                    "megnevező oldal": c["headline"] or ""})
        finally:
            con.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["site"])
        writer.writeheader()
        writer.writerows(rows)
    return [*lines, str(out)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["template", "report", "variants", "prune"])
    parser.add_argument("--out", type=Path, default=Path("data/m3/out/uncovered-variants.csv"))
    parser.add_argument("--site", action="append", default=[], help="név=adatbázis")
    parser.add_argument("--verdicts", type=Path, default=VERDICTS_FILE)
    args = parser.parse_args()
    sites = {name: Path(db) for name, db in (item.split("=", 1) for item in args.site)}
    if not sites:
        raise SystemExit("--site név=adatbázis kell")
    if args.command == "variants":
        print("\n".join(variants(sites, args.out)))
    elif args.command == "prune":
        print(f"{args.verdicts}: törölve {prune(sites, args.verdicts)}")
    elif args.command == "template":
        print(f"{args.verdicts}: {template(sites, args.verdicts)} új tétel")
    else:
        print("\n".join(report(sites, args.verdicts)))


if __name__ == "__main__":
    main()
