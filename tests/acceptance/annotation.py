"""Annotálósablon a három valódi fejlesztési oldalhoz: a blokkok sorszámmal és szöveggel,
modellkimenet nélkül; a referencialistát Krisztián és a reviewer tölti ki.

    python -m tests.acceptance.annotation [--data-dir data/compare] [--out DIR]

A blokkok a rögzített készlet renderelt DOM-jából, ugyanazzal a parserrel és sorszámokkal, mint
a `blocks` tábla (`entities.dom.parse_blocks`); csak a content régió, mert az LLM-bemenet is csak
azt kapja. Kimenet oldalanként (`tests/acceptance/dev_pages/`):

- `<oldal>.json`: a mesterséges oldalak formátuma (`page_id`, `set` = development-real, `url`,
  `lang`, `site_description`, `blocks`: `id` = `b<sorszám>`, `kind`, `heading_path`, `text`,
  táblázatsornál `cells`), és egy üres `gold` (`primary_entities`, `entities`, `optional`,
  `negatives`), a tétel formája ugyanaz, mint a mesterséges oldalakon;
- `<oldal>.md`: ugyanez olvasható formában, blokkonként egy sorral.

Ha a célfájlban már van referencialista, a generátor megtartja a fájl többi mezőjét, és a
tételek blokk-hivatkozásait az új blokkokhoz igazítja (`realign`): a régi és az új blokkok
szövege (a címkelista „ · ” elválasztója nélkül) sorrend szerint párosítva; ami eltűnt blokkra
mutat, kimarad; minden változás a kimeneten.

A generátor nem ír adatbázisba, és nem hív hálózatot.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import duckdb
import zstandard

from aaa2.db.connect import DATA_DIR
from aaa2.entities.blocks import surface_spans
from aaa2.entities.dom import parse_blocks
from aaa2.entities.llm import site_line

OUT_DIR = Path(__file__).parent / "dev_pages"
PAGES = (
    ("kk_coach_meres_hu", "kk-coach-crawl", "https://kk.coach/hu/megoldasok/meres/"),
    ("materia_etlap_hu", "materia-crawl", "https://materia-tm.com/hu/etlap/"),
    ("ngx_accordion_en", "ngx-bootstrap-crawl",
     "https://valor-software.com/ngx-bootstrap/components/accordion"),
)
EMPTY_GOLD = {"primary_entities": [], "entities": [], "optional": [], "negatives": []}
LOCKED_DIR = Path(__file__).parent / "locked_pages"
LOCKED_FILE = Path(__file__).parent / "locked_pages.json"
LOCKED_SITES = {"kk-coach-crawl": ("kk", "kk_coach_meres_hu"),
                "ngx-bootstrap-crawl": ("ngx", "ngx_accordion_en")}
CONCEPT_SCOPE = ("kötelező fogalom: a title-ben vagy egy headingben áll, vagy az oldal központi "
                 "témája; a többi fogalom opcionális. Kötelező a megnevezett entitás (tech, org, "
                 "person, product, place) és a site saját ajánlata (service).")


def template(page_id: str, db: Path, url: str) -> dict:
    """Egy oldal sablonja a rögzített készletből."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        row = con.execute("SELECT title, lang, rendered_html FROM pages WHERE url = ?",
                          [url]).fetchone()
        if row is None:
            raise SystemExit(f"{db.name}: nincs ilyen oldal: {url}")
        site = site_line(con) or ""
    finally:
        con.close()
    title, lang, blob = row
    html = zstandard.ZstdDecompressor().decompress(blob).decode("utf-8", "replace")
    blocks = []
    for block in parse_blocks(html, title):
        if block.region != "content":
            continue
        entry = {"id": f"b{block.ordinal}", "kind": block.kind,
                 "heading_path": block.heading_path, "text": block.text}
        if block.cells is not None:
            entry["cells"] = block.cells
        blocks.append(entry)
    return {"page_id": page_id, "set": "development-real", "url": url, "lang": lang,
            "site_description": site, "blocks": blocks, "gold": EMPTY_GOLD}


def block_mapping(old: list[dict], new: list[dict]) -> dict[str, str]:
    """Régi blokk-azonosító → új: a szövegek („ · ” és „ | ” nélkül) sorrend szerinti
    párosítása; a nem egyező szakaszban a régi blokk ahhoz az új blokkhoz kerül, amelynek a
    szövege tartalmazza (sorrendben, pl. rácscellák egy táblázatsorban)."""
    def plain(block):
        return " ".join(block["text"].replace(" · ", " ").replace(" | ", " ").split())
    olds, news = [plain(b) for b in old], [plain(b) for b in new]
    matcher = difflib.SequenceMatcher(a=olds, b=news, autojunk=False)
    mapping: dict[str, str] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            mapping.update((old[i]["id"], new[j]["id"])
                           for i, j in zip(range(i1, i2), range(j1, j2), strict=True))
            continue
        j = j1
        for i in range(i1, i2):
            k = next((k for k in range(j, j2) if olds[i] and olds[i] in news[k]), None)
            if k is not None:
                mapping[old[i]["id"]] = new[k]["id"]
                j = k
    return mapping


def realign(gold: dict, old: list[dict], new: list[dict]) -> tuple[dict, list[str]]:
    """A tételek szöveg szerinti alakjai az új blokk-azonosítókkal; ami eltűnt blokkra mutat,
    vagy az új blokkban nem áll, kimarad. Visszaad: az új referencialista és a változások."""
    mapping = block_mapping(old, new)
    blocks = {b["id"]: b for b in new}
    changes: list[str] = []
    out = {key: value for key, value in gold.items()}
    for section in ("entities", "optional"):
        items = []
        for item in gold.get(section, []):
            forms = []
            for form in item.get("surface_forms", []):
                target = mapping.get(form["block"])
                if target is None:
                    changes.append(f"{section} {item['canonical']}: {form['block']} "
                                   f"„{form['text']}” kimarad (a blokk nincs meg)")
                    continue
                if not surface_spans(form["text"], blocks[target]):
                    changes.append(f"{section} {item['canonical']}: {form['block']} → {target} "
                                   f"„{form['text']}” kimarad (nem áll az új blokkban)")
                    continue
                if target != form["block"]:
                    changes.append(f"{section} {item['canonical']}: {form['block']} → {target}")
                forms.append({**form, "block": target})
            if not forms and item.get("surface_forms"):
                changes.append(f"{section} {item['canonical']}: nem maradt szöveg szerinti alak")
            items.append({**item, "surface_forms": forms})
        out[section] = items
    return out, changes


def markdown(page: dict) -> str:
    lines = [f"# {page['page_id']}", "", f"- URL: {page['url']}", f"- nyelv: {page['lang']}",
             f"- site: {page['site_description']}",
             f"- blokkok (content régió): {len(page['blocks'])}", "",
             ("Blokkonként: azonosító, típus, heading-útvonal, szöveg (táblázatsornál a cellák "
              "az oszlopfejléccel)."), ""]
    for block in page["blocks"]:
        path = " › ".join(block["heading_path"])
        text = block["text"]
        if block.get("cells"):
            text = "; ".join(f"{c['header']}: {c['value']}" if c.get("header") else c["value"]
                             for c in block["cells"])
        if block["kind"] == "code":
            lines.append(f"- **{block['id']}** `code` ({path})")
            lines += ["", "  ```", *(f"  {line}" for line in text.splitlines()), "  ```", ""]
            continue
        lines.append(f"- **{block['id']}** `{block['kind']}`"
                     + (f" ({path})" if path and block["kind"] != "heading" else "")
                     + f": {text}")
    return "\n".join(lines) + "\n"


def locked_page_id(prefix: str, url: str) -> str:
    """`locked_<site>_<az útvonal utolsó része>[_<fül>]`, kisbetűvel, egész szavakkal,
    legfeljebb 40 jellel."""
    parts = urlsplit(url)
    slug = [s for s in parts.path.split("/") if s][-1] if parts.path.strip("/") else "home"
    tab = dict(parse_qsl(parts.query)).get("tab")
    kept: list[str] = []
    for word in re.findall(r"[a-z0-9]+", f"{slug} {tab or ''}".lower()):
        if kept and len("_".join([*kept, word])) > 40:
            break
        kept.append(word)
    return f"locked_{prefix}_" + "_".join(kept)


def locked_sources(path: Path = LOCKED_FILE) -> list[tuple[str, str, str, str]]:
    """A zárolt lista kk.coach és ngx oldalai: (page_id, adatbázis, URL, fejlesztési oldal)."""
    sites = json.loads(path.read_text(encoding="utf-8"))["sites"]
    return [(locked_page_id(prefix, url), db, url, dev)
            for db, (prefix, dev) in LOCKED_SITES.items() for url in sites[db]["urls"]]


def locked_template(page_id: str, db: Path, url: str, dev_page: Path) -> dict:
    """A zárolt oldal sablonja: a fejlesztési oldal formája, a site-jának szabályaival és
    szókészletével, üres referencialistával."""
    page = template(page_id, db, url)
    dev = json.loads(dev_page.read_text(encoding="utf-8"))
    return {**page, "set": "locked", "gold_status": "sablon, annotálatlan",
            "annotation_notes": [], "rules": {**dev["rules"], "concept_scope": CONCEPT_SCOPE},
            "subtype_glossary": dev["subtype_glossary"],
            "subtype_vocabulary": dev["subtype_vocabulary"]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--locked", action="store_true",
                        help="a zárolt lista kk.coach és ngx oldalai (tests/acceptance/"
                             "locked_pages/); meglévő sablont nem ír felül")
    args = parser.parse_args(argv)
    if args.locked:
        out = args.out or LOCKED_DIR
        out.mkdir(parents=True, exist_ok=True)
        for page_id, name, url, dev in locked_sources():
            target = out / f"{page_id}.json"
            if target.exists():
                print(f"{page_id}: megvan, marad")
                continue
            page = locked_template(page_id, args.data_dir / f"{name}.duckdb", url,
                                   OUT_DIR / f"{dev}.json")
            target.write_text(json.dumps(page, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
            (out / f"{page_id}.md").write_text(markdown(page), encoding="utf-8")
            print(f"{page_id}: {len(page['blocks'])} blokk")
        return
    args.out = args.out or OUT_DIR
    args.out.mkdir(parents=True, exist_ok=True)
    for page_id, name, url in PAGES:
        page = template(page_id, args.data_dir / f"{name}.duckdb", url)
        target = args.out / f"{page_id}.json"
        changes: list[str] = []
        if target.exists():
            previous = json.loads(target.read_text(encoding="utf-8"))
            if any(previous.get("gold", {}).get(k) for k in EMPTY_GOLD):
                gold, changes = realign(previous["gold"], previous["blocks"], page["blocks"])
                page = {**previous, "blocks": page["blocks"], "gold": gold}
        target.write_text(json.dumps(page, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        (args.out / f"{page_id}.md").write_text(markdown(page), encoding="utf-8")
        print(f"{page_id}: {len(page['blocks'])} blokk, {len(changes)} változás a "
              "referencialistában")
        for change in changes:
            print(f"  {change}")


if __name__ == "__main__":
    main()
