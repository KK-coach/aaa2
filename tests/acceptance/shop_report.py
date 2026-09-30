"""A webshop site-szintű összeállítása (M2/7 B, 9a): márkák, termékcsaládok a márkájukkal,
termékek a családjukkal és a kategóriájukkal, kategóriák, demó-jelölések, és a kapcsolat
nélküli márkák sorsa (alias lett vagy `orphan`).

    python -m tests.acceptance.shop_report data/m27/duex2.duckdb --out data/m27/shop

Kimenet: `<out>/brands.csv`, `families.csv`, `products.csv`, `categories.csv`, `demo.csv`,
`orphan_brands.csv`, az összevont `assembly.csv` (szintenként egy sor) és `summary.md`
(darabszámok). Csak olvas.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import duckdb


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[tuple]:
    return con.execute(sql, params or []).fetchall()



def relations(con: duckdb.DuckDBPyConnection, kind: str) -> list[tuple]:
    """Az adott típusú kapcsolatok, csak a mindkét végén létező entitások között."""
    return rows(con, "SELECT from_id, to_id FROM entity_relations WHERE type = ? AND from_id IN "
                     "(SELECT entity_id FROM entities) AND to_id IN (SELECT entity_id FROM "
                     "entities)", [kind])


def collect(db: Path) -> dict[str, list[dict]]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        brand_of = relations(con, "brand_of")
        part_of = dict(relations(con, "part_of"))
        in_category = dict(relations(con, "in_category"))
        names = dict(rows(con, "SELECT entity_id, name FROM entities"))
        brand_of_target = {to: frm for frm, to in brand_of}
        urls = dict(rows(con, "SELECT e.entity_id, p.url FROM entities e JOIN pages p "
                              "ON p.page_id = e.anchor_page_id"))
        lines = {eid for (eid,) in rows(con, "SELECT entity_id FROM entities WHERE type = "
                                             "'product' AND subtype = 'line'")}

        def root(eid: int | None) -> int | None:
            """A család láncának legfelső tagja (a szülőcsaládok mentén)."""
            seen = set()
            while eid in part_of and part_of[eid] in lines and eid not in seen:
                seen.add(eid)
                eid = part_of[eid]
            return eid

        def brand_of_entity(eid: int | None) -> int | None:
            return brand_of_target.get(root(eid)) if eid is not None else None

        brands = [{"márka": name, "aliasok": "; ".join(aliases or []),
                   "családok": sum(1 for f in lines if brand_of_entity(f) == eid),
                   "felső szintű családok": sum(1 for f, t in brand_of if f == eid
                                                and t in lines),
                   "közvetlen termékek": sum(1 for f, t in brand_of if f == eid and t in urls)}
                  for eid, name, aliases in rows(
                      con, "SELECT entity_id, name, aliases FROM entities WHERE type = 'brand' "
                           "AND entity_id IN (SELECT from_id FROM entity_relations "
                           "WHERE type = 'brand_of') ORDER BY name")]
        families = [{"termékcsalád": name, "szülőcsalád": names.get(part_of.get(eid), "")
                     if part_of.get(eid) in lines else "",
                     "márka": names.get(brand_of_entity(eid), ""),
                     "aliasok": "; ".join(aliases or []),
                     "alcsaládok": sum(1 for f, t in part_of.items() if t == eid and f in lines),
                     "termékek": sum(1 for f, t in part_of.items() if t == eid and f in urls)}
                    for eid, name, aliases in rows(con, "SELECT entity_id, name, aliases FROM "
                                                        "entities WHERE type = 'product' AND "
                                                        "subtype = 'line' ORDER BY name")]
        products = []
        for eid, name, attributes in rows(
                con, "SELECT entity_id, name, attributes FROM entities WHERE type = 'product' "
                     "AND subtype = 'variant' ORDER BY name"):
            family = part_of.get(eid)
            brand = brand_of_entity(family) if family else brand_of_target.get(eid)
            products.append({
                "termék": name, "termékcsalád": names.get(family, ""),
                "márka": names.get(brand, ""), "kategória": names.get(in_category.get(eid), ""),
                "tulajdonságok": json.dumps(json.loads(attributes), ensure_ascii=False)
                if attributes else "", "url": urls.get(eid, "")})
        categories = [{"kategória": name, "url": urls.get(eid, ""),
                       "termékek": sum(1 for t in in_category.values() if t == eid)}
                      for eid, name in rows(con, "SELECT entity_id, name FROM entities "
                                                 "WHERE type = 'concept' AND subtype = 'category' "
                                                 "ORDER BY name")]
        demo = [{"entitás": name, "típus": kind, "altípus": subtype or "",
                 "említés": mentions,
                 "oldalak": "; ".join(sorted(set(pages or [])))}
                for name, kind, subtype, mentions, pages in rows(
                    con, "SELECT e.name, e.type, e.subtype, count(pe.mention_id), "
                         "list(DISTINCT p.url) FROM entities e "
                         "LEFT JOIN page_entities pe USING (entity_id) "
                         "LEFT JOIN pages p ON p.page_id = pe.page_id "
                         "WHERE list_contains(coalesce(e.flags, []), 'demo') "
                         "GROUP BY e.entity_id, e.name, e.type, e.subtype ORDER BY e.name")]
        last = rows(con, "SELECT skipped FROM entity_runs WHERE method = 'site' "
                         "ORDER BY run_id DESC LIMIT 1")
        candidates = ((json.loads(last[0][0] or "{}").get("shop") or {}).get("orphans") or {}
                      if last else {})
        orphan_brands = [{"márka": name, "sors": "orphan",
                          "cél": "; ".join(candidates.get(name) or []),
                          "szabály": "több jelölt" if len(candidates.get(name) or []) > 1
                          else ""}
                         for (name,) in rows(con, "SELECT name FROM entities WHERE list_contains("
                                                  "coalesce(flags, []), 'orphan') ORDER BY name")]
        orphan_brands += [{"márka": removed, "sors": "alias", "cél": kept, "szabály": rule}
                          for removed, kept, rule in rows(
                              con, "SELECT removed_name, arg_max(e.name, m.merge_id), "
                                   "arg_max(m.rule, m.merge_id) FROM merge_log m JOIN entities "
                                   "e ON e.entity_id = m.kept_id WHERE m.rule LIKE "
                                   "'orphan_brand%' GROUP BY removed_name ORDER BY removed_name")]
    finally:
        con.close()
    return {"brands": brands, "families": families, "products": products,
            "categories": categories, "demo": demo, "orphan_brands": orphan_brands}


def assembly(data: dict[str, list[dict]]) -> list[dict]:
    """Az összeállítás egy táblában: szint, név, márka, termékcsalád, kategória, termékszám,
    tulajdonságok, URL, megjegyzés."""
    out = []

    def row(level, name, **fields):
        out.append({"szint": level, "név": name, "márka": fields.get("brand", ""),
                    "termékcsalád": fields.get("family", ""),
                    "kategória": fields.get("category", ""),
                    "termékek": fields.get("count", ""),
                    "tulajdonságok": fields.get("attributes", ""), "url": fields.get("url", ""),
                    "megjegyzés": fields.get("note", "")})

    for b in data["brands"]:
        row("márka", b["márka"], count=b["közvetlen termékek"],
            note=f"családok: {b['családok']} (felső szinten {b['felső szintű családok']}); "
                 f"aliasok: {b['aliasok']}")
    for f in data["families"]:
        row("termékcsalád", f["termékcsalád"], brand=f["márka"], family=f["szülőcsalád"],
            count=f["termékek"], note="; ".join(filter(None, [
                f"alcsaládok: {f['alcsaládok']}" if f["alcsaládok"] else "",
                f"aliasok: {f['aliasok']}" if f["aliasok"] else ""])))
    for p in data["products"]:
        row("termék", p["termék"], brand=p["márka"], family=p["termékcsalád"],
            category=p["kategória"], attributes=p["tulajdonságok"], url=p["url"])
    for c in data["categories"]:
        row("kategória", c["kategória"], count=c["termékek"], url=c["url"])
    for o in data["orphan_brands"]:
        row("kapcsolat nélküli márka", o["márka"],
            note=(f"orphan; jelöltek: {o['cél']}" if o["sors"] == "orphan" and o["cél"]
                  else f"{o['sors']}" + (f" → {o['cél']} ({o['szabály']})" if o["cél"] else "")))
    for d in data["demo"]:
        row("demó", d["entitás"], note=f"{d['típus']}/{d['altípus']}; {d['oldalak']}")
    return out


def write(data: dict[str, list[dict]], out: Path) -> str:
    out.mkdir(parents=True, exist_ok=True)
    for name, items in {**data, "assembly": assembly(data)}.items():
        with (out / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            if items:
                writer = csv.DictWriter(handle, fieldnames=list(items[0]))
                writer.writeheader()
                writer.writerows(items)
    labels = {"brands": "Márkák", "families": "Termékcsaládok", "products": "Termékek",
              "categories": "Kategóriák", "demo": "Demó-jelölések",
              "orphan_brands": "Kapcsolat nélküli márkák"}
    lines = ["# Webshop site-szintű összeállítás", ""]
    lines += [f"- {labels[name]}: {len(items)}" for name, items in data.items()]
    summary = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(summary, encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("db", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data/m27/shop"))
    args = parser.parse_args(argv)
    print(write(collect(args.db), args.out))


if __name__ == "__main__":
    main()
