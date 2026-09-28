"""Referencialista-vázlat (gold) a `GOLD_PAGES` oldalaira, egy Markdown-fájlban.

    python -m tests.acceptance.gold_draft [--out tests/acceptance/out/llm-compare/gold-draft.md]

Oldalanként: a title, a headingek (szinttel), a teljes main content, és a modellek eddigi
kimenetének uniója (`data/compare/<készlet>.<szolgáltató>.jsonl`, `llm_compare run`) a név kulcsa
(`alias_key`: kis-nagybetű, ékezet, kötőjel) szerint csoportosítva: a névváltozatok, modellenként
a típus, és egy idézet. A fabrikált idézet (nincs szó szerint az oldalon) áthúzva. A vázlatot
kézzel egészítjük ki és véglegesítjük; a végleges lista `tests/acceptance/gold/<oldal>.json`
(név, aliasok, típus).
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import duckdb

from aaa2.db.connect import DATA_DIR
from aaa2.entities.llm import check_evidence
from aaa2.entities.rules import alias_key
from tests.acceptance.llm_compare import (
    LABELS,
    OUT_DIR,
    PROVIDERS,
    _cell,
    _Entity,
    page_sources,
    read_jsonl,
)

GOLD_PAGES: list[tuple[str, str, str]] = [
    ("kk-coach-meres", "kk-coach-crawl", "https://kk.coach/hu/megoldasok/meres/"),
    ("materia-etlap", "materia-crawl", "https://materia-tm.com/hu/etlap/"),
    ("ngx-accordion", "ngx-bootstrap-crawl",
     "https://valor-software.com/ngx-bootstrap/components/accordion"),
]


def page_section(data_dir: Path, slug: str, name: str, url: str,
                 providers=PROVIDERS) -> list[str]:
    con = duckdb.connect(str(data_dir / f"{name}.duckdb"), read_only=True)
    try:
        page_id, title, lang, words, main = con.execute(
            "SELECT page_id, title, lang, word_count, main_content FROM pages WHERE url = ?",
            [url]).fetchone()
        headings = con.execute("SELECT level, text FROM headings WHERE page_id = ? "
                               "ORDER BY ordinal", [page_id]).fetchall()
        sources = page_sources(con, page_id)
    finally:
        con.close()
    names: dict[str, list[str]] = defaultdict(list)
    found: dict[str, dict[str, tuple[str, str, bool]]] = defaultdict(dict)
    counts = {}
    for provider in providers:
        record = next((r for r in read_jsonl(data_dir / f"{name}.{provider}.jsonl")
                       if r["page_id"] == page_id and r["entities"] is not None), None)
        counts[provider] = None if record is None else len(record["entities"])
        for entity in (record or {}).get("entities") or []:
            key = alias_key(entity["name"])
            if entity["name"] not in names[key]:
                names[key].append(entity["name"])
            fabricated = check_evidence(_Entity(entity), sources) == "fabricated"
            found[key].setdefault(provider, (entity["type"], entity["evidence"], fabricated))
    present = [p for p in providers if counts[p] is not None]
    lines = [f"## {slug}: {url}", "",
             f"Nyelv: {lang}, {words} szó. Modellek: "
             + ", ".join(f"{LABELS[p]} {counts[p]} sor" if counts[p] is not None
                         else f"{LABELS[p]} nincs kimenet" for p in providers) + ".", "",
             "### Title", "", f"{title}", "", "### Headingek", ""]
    lines += [f"- H{level}: {text}" for level, text in headings if text] or ["- (nincs)"]
    lines += ["", f"### A modellek uniója ({len(found)} kulcs)", "",
              "Rendezés: hány modell adta, azon belül név szerint. „—”: a modell nem adta.", "",
              "| név (változatok) | " + " | ".join(LABELS[p] for p in present) + " | idézet |",
              "|---|" + "---|" * len(present) + "---|"]
    for key in sorted(found, key=lambda k: (-len(found[k]), names[k][0].casefold())):
        cells, quote = [], ""
        for provider in present:
            hit = found[key].get(provider)
            if hit is None:
                cells.append("—")
                continue
            kind, evidence, fabricated = hit
            cells.append(f"~~{kind}~~" if fabricated else kind)
            quote = quote or (f"~~{_cell(evidence)}~~" if fabricated else _cell(evidence))
        lines.append(f"| {_cell(' / '.join(names[key]))} | " + " | ".join(cells)
                     + f" | {quote} |")
    lines += ["", "### Kiegészítés (a modellek egyike sem adta)", "", "- ", "",
              "### A teljes main content", "", "```text", main or "", "```", ""]
    return lines


def draft_markdown(data_dir: Path) -> str:
    lines = ["# Referencialista-vázlat (gold), 3 oldal", "",
             ("Oldalanként a title, a headingek, a teljes main content, és a modellek eddigi "
              "kimenetének uniója (a 30 oldalas mérés, prompt v1). A fabrikált idézet áthúzva. "
              "Kiegészítés után a végleges lista `tests/acceptance/gold/<oldal>.json`: név, "
              "aliasok, típus."), ""]
    for slug, name, url in GOLD_PAGES:
        lines += page_section(data_dir, slug, name, url)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=OUT_DIR / "gold-draft.md")
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(draft_markdown(args.data_dir), encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
