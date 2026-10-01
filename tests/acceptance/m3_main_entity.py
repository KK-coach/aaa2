"""Az oldal fő entitásának mérése (M3 spec, 8. pont): a zárolt és a fejlesztési oldalak meglévő
`primary_entities` referenciája a gráf kimenetével szemben, és a kk.coach annotálósablonja.

    python -m tests.acceptance.m3_main_entity compare --site kk.coach=data/m3/kk-coach.duckdb \\
        --site valor-software.com=data/m3/ngx-bootstrap.duckdb \\
        --site marketinglens.com=data/m3/marketinglens.duckdb \\
        --site shop.duexhungary.hu=data/m3/duex.duckdb --out data/m3/out/main-entity-compare.csv
    python -m tests.acceptance.m3_main_entity template --db data/m3/kk-coach.duckdb
    python -m tests.acceptance.m3_main_entity score --db data/m3/kk-coach.duckdb

- `compare`: oldalanként a referencia `primary_entities`-e, a kimenet fő és másodlagos entitása
  (név, típus, megbízhatóság, bizonyítékok), és az egyezés: `fő` (a fő entitás neve vagy aliasa
  a referencia első elemének kulcsa), `fő, a referencia más eleme`, `másodlagos`,
  `segédoldal: egyezik` (üres referencia és nincs fő entitás), `segédoldal: van fő entitás`,
  `nincs fő entitás`, `eltér`. A gráfot előbb az `aaa graph` építi fel. Csak olvas.
- `template`: a site összes oldal-csomópontja (URL, title, H1, oldalszerep, nyelv) kitöltendő
  mezőkkel, a kimenet nélkül, `tests/acceptance/m3/kk_main_entity_reference.json`.
- `score`: a kitöltött referencia (`main_entity`, `aliases`, `support`, a `note`-ban az
  „elfogadható alternatíva: '…'”) a kimenettel szemben: `fő` (a fő entitás neve vagy aliasa a
  referencia nevének vagy aliasának kulcsa), `alternatíva`, `segédoldal: egyezik`, `másodlagos`,
  `segédoldal: van fő entitás`, `nincs fő entitás`, `eltér`; helyes a `fő`, az `alternatíva` és
  a `segédoldal: egyezik`.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import duckdb

from aaa2.entities.pages import page_url
from aaa2.entities.rules import alias_key
from aaa2.functions.graph import evidence_text
from tests.acceptance.annotation import LOCKED_DIR

DEV_DIR = Path(__file__).parent / "dev_pages"
DEV_PAGES = ("kk_coach_meres_hu", "ngx_accordion_en")
TEMPLATE_FILE = Path(__file__).parent / "m3" / "kk_main_entity_reference.json"
ALTERNATIVE = re.compile(r"elfogadható alternatíva: '([^']+)'")
CORRECT = ("fő", "alternatíva", "segédoldal: egyezik")
TEMPLATE_RULES = (
    "Az oldal fő entitása (M3 spec, 3. pont): az az entitás, amelyről az oldal ténylegesen szól; "
    "a kanonikus nevén, ahogy az oldalon áll. `main_entity`: a név, vagy null, ha az oldal "
    "segédoldal (kapcsolat, jogi oldal, lista) vagy nem szól egy entitásról; `support`: true, ha "
    "segédoldal; `secondary`: legfeljebb két további entitás, ha az oldal összehasonlító vagy "
    "több témájú; `note`: rövid indoklás, ha a döntés nem magától értetődő.")


def reference_pages() -> list[dict]:
    """A zárolt oldalak és a két fejlesztési oldal (page_id, url, a referencia)."""
    pages = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(LOCKED_DIR.glob("locked_*.json"))]
    pages += [json.loads((DEV_DIR / f"{name}.json").read_text(encoding="utf-8"))
              for name in DEV_PAGES]
    return [{"page_id": p["page_id"], "url": p["url"],
             "primary": list(p["gold"].get("primary_entities") or [])} for p in pages]


def names_of(con: duckdb.DuckDBPyConnection, entity_id: int) -> set[str]:
    """Az entitás nevének és aliasainak kulcsai."""
    name, aliases = con.execute("SELECT name, aliases FROM entities WHERE entity_id = ?",
                                [entity_id]).fetchone()
    forms = [name, *(aliases or [])]
    forms += [a for (a,) in con.execute("SELECT alias FROM entity_aliases WHERE entity_id = ?",
                                        [entity_id]).fetchall()]
    return {alias_key(f) for f in forms if f}


def verdict(primary: list[str], main: tuple | None, secondary: list[tuple],
            con: duckdb.DuckDBPyConnection) -> str:
    keys = [alias_key(n) for n in primary]
    if not keys:
        return "segédoldal: egyezik" if main is None else "segédoldal: van fő entitás"
    if main is None:
        return "nincs fő entitás"
    mine = names_of(con, main[0])
    if keys[0] in mine:
        return "fő"
    if set(keys) & mine:
        return "fő, a referencia más eleme"
    if any(set(keys) & names_of(con, s[0]) for s in secondary):
        return "másodlagos"
    return "eltér"


def page_result(con: duckdb.DuckDBPyConnection, url: str) -> tuple:
    """A gráf kimenete az oldalra: (page_nodes-sor, fő entitás, másodlagosak); a lekérdezés és
    a záró perjel nélküli URL-lel is."""
    row = con.execute("SELECT page_id, role, support_kind, main_status FROM page_nodes "
                      "WHERE url = ?", [url]).fetchone()
    if row is None:
        row = next((r[:4] for r in con.execute(
            "SELECT page_id, role, support_kind, main_status, url FROM page_nodes").fetchall()
            if page_url(r[4]) == page_url(url)), None)
    if row is None:
        return None, None, []
    chosen = con.execute(
        "SELECT m.entity_id, m.role, m.confidence, m.evidence, e.name, e.type, e.subtype "
        "FROM page_main_entity m JOIN entities e USING (entity_id) WHERE m.page_id = ? "
        "ORDER BY m.rank", [row[0]]).fetchall()
    return (row, next((c for c in chosen if c[1] == "main"), None),
            [c for c in chosen if c[1] == "secondary"])


def reference_verdict(page: dict, main: tuple | None, secondary: list[tuple],
                      con: duckdb.DuckDBPyConnection) -> str:
    """A kitöltött referencia egy oldala a kimenettel szemben (lásd a modul leírását)."""
    alternatives = {alias_key(a) for a in ALTERNATIVE.findall(page.get("note") or "")}
    if page.get("support") or page.get("main_entity") is None:
        if main is None:
            return "segédoldal: egyezik"
        return "alternatíva" if alternatives & names_of(con, main[0]) \
            else "segédoldal: van fő entitás"
    keys = {alias_key(n) for n in [page["main_entity"], *(page.get("aliases") or [])] if n}
    if main is None:
        return "nincs fő entitás"
    mine = names_of(con, main[0])
    if keys & mine:
        return "fő"
    if alternatives & mine:
        return "alternatíva"
    if any(keys & names_of(con, s[0]) for s in secondary):
        return "másodlagos"
    return "eltér"


def score(db: Path, reference: Path, out: Path) -> Counter:
    data = json.loads(reference.read_text(encoding="utf-8"))
    rows, counts = [], Counter()
    con = duckdb.connect(str(db), read_only=True)
    try:
        for page in data["pages"]:
            row, main, secondary = page_result(con, page["url"])
            if row is None:
                raise SystemExit(f"{page['url']}: az oldal nincs a gráfban ({db})")
            result = reference_verdict(page, main, secondary, con)
            counts[result] += 1
            rows.append({
                "url": page["url"], "referencia": page.get("main_entity") or "(segédoldal)",
                "szerep": row[1], "segédoldal": row[2] or "", "állapot": row[3],
                "fő entitás": main[4] if main else "",
                "megbízhatóság": main[2] if main else "",
                "bizonyítékok": evidence_text(json.loads(main[3])) if main else "",
                "másodlagos": "; ".join(s[4] for s in secondary), "egyezés": result})
    finally:
        con.close()
    _write(out, rows)
    return counts


def _write(out: Path, rows: list[dict]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def compare(sites: dict[str, Path], out: Path) -> Counter:
    rows, counts = [], Counter()
    for page in reference_pages():
        host = urlsplit(page["url"]).hostname or ""
        db = next((path for domain, path in sites.items() if host.endswith(domain)), None)
        if db is None:
            raise SystemExit(f"{page['page_id']}: nincs adatbázis a {host} site-hoz")
        con = duckdb.connect(str(db), read_only=True)
        try:
            row, main, secondary = page_result(con, page["url"])
            if row is None:
                raise SystemExit(f"{page['page_id']}: az oldal nincs a gráfban ({db})")
            result = verdict(page["primary"], main, secondary, con)
        finally:
            con.close()
        counts[result] += 1
        rows.append({
            "oldal": page["page_id"], "url": page["url"],
            "referencia": "; ".join(page["primary"]) or "(segédoldal)",
            "szerep": row[1], "segédoldal": row[2] or "", "állapot": row[3],
            "fő entitás": main[4] if main else "",
            "típus": "/".join(filter(None, main[5:7])) if main else "",
            "megbízhatóság": main[2] if main else "",
            "bizonyítékok": evidence_text(json.loads(main[3])) if main else "",
            "másodlagos": "; ".join(s[4] for s in secondary), "egyezés": result})
    _write(out, rows)
    return counts


def template(db: Path, out: Path = TEMPLATE_FILE) -> int:
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute("SELECT url, title, h1, role, lang FROM page_nodes ORDER BY url"
                           ).fetchall()
    finally:
        con.close()
    pages = [{"url": url, "title": title, "h1": h1, "role": role, "lang": lang,
              "main_entity": None, "secondary": [], "support": None, "note": ""}
             for url, title, h1, role, lang in rows]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rules": TEMPLATE_RULES, "status": "sablon, kitöltendő",
                               "pages": pages}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    return len(pages)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["compare", "template", "score"])
    parser.add_argument("--reference", type=Path, default=TEMPLATE_FILE,
                        help="score: a kitöltött referencia")
    parser.add_argument("--site", action="append", default=[], help="compare: domain=adatbázis")
    parser.add_argument("--db", type=Path, help="template: a site-adatbázis")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "compare":
        sites = {k: Path(v) for k, v in (s.split("=", 1) for s in args.site)}
        out = args.out or Path("data/m3/out/main-entity-compare.csv")
        counts = compare(sites, out)
        print(f"{out}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        return
    if args.db is None:
        raise SystemExit(f"{args.command}: --db kell")
    if args.command == "score":
        out = args.out or Path(f"data/m3/out/{args.db.stem}-main-entity-score.csv")
        counts = score(args.db, args.reference, out)
        total = sum(counts.values())
        good = sum(counts[k] for k in CORRECT)
        print(f"{out}: helyes {good}/{total} ({100 * good / total:.1f}%); "
              + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        return
    out = args.out or TEMPLATE_FILE
    print(f"{out}: {template(args.db, out)} oldal")


if __name__ == "__main__":
    main()

