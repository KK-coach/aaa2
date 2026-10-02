"""Stabilitásmérés (M2/7, A rész 3. pont): ugyanaz a site két teljes pipeline-futás után,
site-szinten, típusonként összevetve.

    python -m tests.acceptance.stability BASE.duckdb FRESH.duckdb --out data/m27/a3

A két adatbázis ugyanazzal a kóddal futott (a szabálykör, a site-kör és a tudásbázis mindkettőn
újra); a különbség csak az LLM-kinyerés (a `--fresh` futás minden oldalt újra kinyert). Az
entitások a normalizált név szerint párosulnak (`site.normal_key`, a nevek és az aliasok bármelyike
egyezhet). Csoportok:

- megnevezett entitások: tech, org, person, product, place, brand, event, work (a demó-jelöltek
  nélkül);
- ajánlatok: service, fő ajánlat és csomag (tier core, package, work_mode);
- a legfontosabb 20 fogalom: concept, az említésszám szerint (a sablon-említések nélkül).

Kimenet: `<out>/stability.md` (összesítő) és `<out>/stability.csv` (tételenként: csoport, név,
mindkét futásban / csak az egyikben, említésszámok).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import duckdb

from aaa2.resolver.names import normal_key

NAMED = ("tech", "org", "person", "product", "place", "brand", "event", "work")
OFFER_TIERS = ("core", "package", "work_mode")
TOP_CONCEPTS = 20


def entities(db: Path) -> dict[str, list[dict]]:
    """Csoportonként az entitások: név, kulcsok, említésszám."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT e.entity_id, e.name, e.type, e.tier, e.aliases, coalesce(e.flags, []), "
            "(SELECT count(*) FROM page_entities pe WHERE pe.entity_id = e.entity_id "
            "AND NOT list_contains(coalesce(pe.flags, []), 'template')) "
            "FROM entities e WHERE e.entity_id IN (SELECT entity_id FROM page_entities) "
            "OR e.anchor_page_id IS NOT NULL").fetchall()
    finally:
        con.close()
    groups: dict[str, list[dict]] = {"named": [], "offers": [], "concepts": []}
    for entity_id, name, kind, tier, aliases, flags, mentions in rows:
        item = {"id": entity_id, "name": name, "mentions": mentions,
                "keys": {normal_key(f) for f in [name, *(aliases or [])]} - {""}}
        if kind in NAMED and "demo" not in flags:
            groups["named"].append(item)
        elif kind == "service" and tier in OFFER_TIERS:
            groups["offers"].append(item)
        elif kind == "concept" and mentions:
            groups["concepts"].append(item)
    groups["concepts"] = sorted(groups["concepts"], key=lambda i: (-i["mentions"], i["name"])
                                )[:TOP_CONCEPTS]
    return groups


def compare(base: list[dict], fresh: list[dict]) -> list[dict]:
    """Tételenként: a név, melyik futásban van (mindkettő / csak az alap / csak a friss), és az
    említésszámok; a párosítás a kulcsok metszete."""
    rows, used = [], set()
    for item in base:
        match = next((f for f in fresh if f["id"] not in used and f["keys"] & item["keys"]), None)
        if match is not None:
            used.add(match["id"])
        rows.append({"name": item["name"], "where": "mindkettő" if match else "csak az alap",
                     "fresh_name": match["name"] if match else "",
                     "base_mentions": item["mentions"],
                     "fresh_mentions": match["mentions"] if match else 0})
    rows += [{"name": f["name"], "where": "csak a friss", "fresh_name": f["name"],
              "base_mentions": 0, "fresh_mentions": f["mentions"]}
             for f in fresh if f["id"] not in used]
    return rows


LABELS = {"named": "megnevezett entitások", "offers": "ajánlatok (core, package, work_mode)",
          "concepts": f"a legfontosabb {TOP_CONCEPTS} fogalom (említésszám szerint)"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("base", type=Path)
    parser.add_argument("fresh", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data/m27/a3"))
    args = parser.parse_args(argv)
    base, fresh = entities(args.base), entities(args.fresh)
    args.out.mkdir(parents=True, exist_ok=True)
    lines = ["# Stabilitásmérés", "", f"- alap: `{args.base}`", f"- friss: `{args.fresh}`", "",
             "| csoport | alap | friss | mindkettő | csak az alap | csak a friss | Jaccard |",
             "|---|---|---|---|---|---|---|"]
    with (args.out / "stability.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["csoport", "név (alap)", "név (friss)", "hol", "említés (alap)",
                         "említés (friss)"])
        for group in ("named", "offers", "concepts"):
            rows = compare(base[group], fresh[group])
            both = sum(r["where"] == "mindkettő" for r in rows)
            only_base = sum(r["where"] == "csak az alap" for r in rows)
            only_fresh = sum(r["where"] == "csak a friss" for r in rows)
            jaccard = both / len(rows) if rows else 1.0
            lines.append(f"| {LABELS[group]} | {len(base[group])} | {len(fresh[group])} | "
                         f"{both} | {only_base} | {only_fresh} | {jaccard:.2f} |")
            for r in rows:
                writer.writerow([group, r["name"] if r["where"] != "csak a friss" else "",
                                 r["fresh_name"], r["where"], r["base_mentions"],
                                 r["fresh_mentions"]])
    (args.out / "stability.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
