"""Mérés (csak mérés, a szabály nincs bekapcsolva): cég és termék egy néven.

    python -m tests.acceptance.org_tool_probe --site kk-coach=data/m3/kk-coach.duckdb ... \
        [--out data/m3/out/org-tool-probe.csv]

A vizsgált szabály: az a szervezet (org, nem a site saját szervezete) tech típust kapna, amelyet
a site eszközként használ, vagyis

- vannak a nevével kezdődő tech entitások (a „termékei”, pl. „DataForSEO SERP API”), és
- azokban a blokkokban, ahol említik, a vele együtt említett megnevezett entitások (tech, org,
  brand; a saját termékei nélkül) többsége tech (`PEER_SHARE`).

A kimenet minden olyan szervezet, amelynek van „terméke”: a régi és az új típus, a termékek, a
társak megoszlása és az indok; a `változna` oszlop mutatja, melyiket érintené a szabály.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

import duckdb

from aaa2.entities.rules import alias_key

PEER_TYPES = ("tech", "org", "brand")
PEER_SHARE = 0.5


def probe(db: Path) -> list[dict]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        entities = {row[0]: row for row in con.execute(
            "SELECT entity_id, name, type, subtype, role FROM entities "
            "WHERE entity_id IN (SELECT entity_id FROM page_entities)").fetchall()}
        tech = [(e, alias_key(row[1])) for e, row in entities.items() if row[2] == "tech"]
        blocks: dict[int, set[int]] = {}
        for block_id, entity_id in con.execute(
                "SELECT block_id, entity_id FROM page_entities WHERE block_id IS NOT NULL"
        ).fetchall():
            blocks.setdefault(block_id, set()).add(entity_id)
        pages = dict(con.execute("SELECT entity_id, count(DISTINCT page_id) FROM page_entities "
                                 "GROUP BY 1").fetchall())
        rows = []
        for entity_id, (_, name, kind, subtype, role) in sorted(entities.items(),
                                                                key=lambda item: item[1][1]):
            if kind != "org" or role == "brand":
                continue
            key = alias_key(name)
            products = sorted(e for e, other in tech if other.startswith(key + " "))
            if not products:
                continue
            peers: Counter = Counter()
            names: Counter = Counter()
            for members in blocks.values():
                if entity_id in members:
                    for other in members - {entity_id, *products}:
                        if other in entities and entities[other][2] in PEER_TYPES:
                            peers[entities[other][2]] += 1
                            names[(entities[other][1], entities[other][2])] += 1
            total = sum(peers.values())
            share = peers["tech"] / total if total else 0.0
            changes = share > PEER_SHARE
            rows.append({
                "név": name, "régi típus": "/".join(filter(None, (kind, subtype))),
                "új típus": "tech" if changes else "/".join(filter(None, (kind, subtype))),
                "változna": "igen" if changes else "nem", "oldalak": pages.get(entity_id, 0),
                "termékei": "; ".join(entities[p][1] for p in products),
                "tech társ": peers["tech"], "org és brand társ": peers["org"] + peers["brand"],
                "tech arány": round(share, 2),
                "leggyakoribb társak": "; ".join(f"{n} ({t})" for (n, t), _ in
                                                 names.most_common(6)),
                "indok": (f"{len(products)} termék a nevével; az együtt említett megnevezett "
                          f"entitások {100 * share:.0f}%-a tech" if total else
                          f"{len(products)} termék a nevével; nincs együtt említett társ")})
        return rows
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", action="append", default=[], help="név=adatbázis")
    parser.add_argument("--out", type=Path, default=Path("data/m3/out/org-tool-probe.csv"))
    args = parser.parse_args()
    rows = []
    for item in args.site:
        site, db = item.split("=", 1)
        found = probe(Path(db))
        rows += [{"site": site, **row} for row in found]
        print(f"{site}: {len(found)} szervezet saját nevű tech entitással, ebből változna "
              f"{sum(1 for r in found if r['változna'] == 'igen')}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as handle:
        columns = ["site", "név", "régi típus", "új típus", "változna", "oldalak", "termékei",
                   "tech társ", "org és brand társ", "tech arány", "leggyakoribb társak",
                   "indok"]
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(args.out)


if __name__ == "__main__":
    main()
