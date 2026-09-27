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

A generátor nem ír adatbázisba, és nem hív hálózatot.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import zstandard

from aaa2.db.connect import DATA_DIR
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for page_id, name, url in PAGES:
        page = template(page_id, args.data_dir / f"{name}.duckdb", url)
        (args.out / f"{page_id}.json").write_text(
            json.dumps(page, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (args.out / f"{page_id}.md").write_text(markdown(page), encoding="utf-8")
        print(f"{page_id}: {len(page['blocks'])} blokk")


if __name__ == "__main__":
    main()
