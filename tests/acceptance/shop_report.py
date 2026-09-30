"""A webshop site-szintű összeállítása (M2/7 B, 9a): márkák, termékcsaládok a márkájukkal,
termékek a családjukkal és a kategóriájukkal, kategóriák, demó-jelölések.

    python -m tests.acceptance.shop_report data/m27/duex2.duckdb --out data/m27/shop

Kimenet: `<out>/brands.csv`, `families.csv`, `products.csv`, `categories.csv`, `demo.csv` és
`summary.md` (darabszámok és listák). Csak olvas.
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
        brands = [{"márka": name, "aliasok": "; ".join(aliases or []),
                   "családok": sum(1 for f, t in brand_of if f == eid and t not in part_of
                                   and t not in urls),
                   "közvetlen termékek": sum(1 for f, t in brand_of if f == eid and t in urls)}
                  for eid, name, aliases in rows(
                      con, "SELECT entity_id, name, aliases FROM entities WHERE type = 'brand' "
                           "AND entity_id IN (SELECT from_id FROM entity_relations "
                           "WHERE type = 'brand_of') ORDER BY name")]
        families = [{"termékcsalád": name, "márka": names.get(brand_of_target.get(eid), ""),
                     "termékek": sum(1 for t in part_of.values() if t == eid)}
                    for eid, name in rows(con, "SELECT entity_id, name FROM entities "
                                               "WHERE type = 'product' AND subtype = 'line' "
                                               "ORDER BY name")]
        products = []
        for eid, name, attributes in rows(
                con, "SELECT entity_id, name, attributes FROM entities WHERE type = 'product' "
                     "AND subtype = 'variant' ORDER BY name"):
            family = part_of.get(eid)
            brand = brand_of_target.get(family) if family else brand_of_target.get(eid)
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
    finally:
        con.close()
    return {"brands": brands, "families": families, "products": products,
            "categories": categories, "demo": demo}


def write(data: dict[str, list[dict]], out: Path) -> str:
    out.mkdir(parents=True, exist_ok=True)
    for name, items in data.items():
        with (out / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            if items:
                writer = csv.DictWriter(handle, fieldnames=list(items[0]))
                writer.writeheader()
                writer.writerows(items)
    labels = {"brands": "Márkák", "families": "Termékcsaládok", "products": "Termékek",
              "categories": "Kategóriák", "demo": "Demó-jelölések"}
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
