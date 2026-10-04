"""A heading-fa szerkezeti megállapításainak független ellenőrzése a renderelt DOM-ból (nem a
blokkokból és nem a `functions/headings.py` kódjával).

    python -m tests.acceptance.heading_verify --site kk-coach=<adatbázis> ...
        [--out jelentés.md] [--json eredmény.json]

Az adatbázis egy lefutott `aaa findings` végállapota (csak olvasva; pl. a
`tests.acceptance.determinism --keep` mappájából). Megállapításonként és oldalanként a DOM-ból
újraszámolt tény:

- a látható, fő tartalombeli headingek sorrendje (rejtett, cookie-, dialógus- és chrome-elem
  nélkül; a `main` / `article` saját fejléce és lábléce tartalom);
- headingenként az, ami utána a következő ilyen headingig áll: a szöveg szavai (a rejtett elem
  szövege és a sorból kimaradó heading szövege is), és a tartalmi elemek (kép, videó, beágyazás,
  űrlap, űrlapelem, táblázat, elemet tartalmazó link).

Szabályok: `missing_h1` (a DOM-ban sehol nincs H1), `multiple_h1` (a fő tartalomban legalább
két H1), `missing_h2` (a fő tartalomban nincs H2), `empty_section` (a megnevezett heading után
azonos vagy magasabb szintű heading jön, és a kettő között nincs szöveg és nincs tartalmi
elem), `skipped_level` (a megnevezett heading több mint eggyel mélyebb az előtte álló, nála
magasabb szintű headingnél; az összevont tételnél a szintminta is egyezik),
`paragraph_heading` (az üres szakasz feltétele, és a heading 12 szónál hosszabb). A
`h1_outside_content` maga DOM-vizsgálat, itt nincs mihez mérni.

Korlát: a chrome és a rejtett elem felismerése ugyanazokra a jelekre épül, mint az eszközé; a
több H1 „indokolatlan” minősítését nem ellenőrzi. Az eredmény megállapításonként `igaz` (minden
oldalán megerősítve) vagy `eltér` az okkal; a `--json` a verdiktfájl kulcsaival adja vissza."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import duckdb
import zstandard
from selectolax.parser import HTMLParser

COOKIE = re.compile(r"cookie|consent|gdpr")
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
NO_TEXT = {"script", "style", "noscript", "iframe", "template", "head", "title"}
ELEMENTS = {"img", "picture", "svg", "video", "audio", "canvas", "object", "embed", "iframe",
            "form", "input", "select", "textarea", "button", "table"}
STRUCTURE = ("missing_h1", "h1_outside_content", "multiple_h1", "empty_section",
             "skipped_level", "missing_h2", "paragraph_heading")
PARAGRAPH_WORDS = 12


def key(text: str | None) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def place(node) -> str:
    """content / chrome / hidden az ősök alapján."""
    own_header = chrome = False
    while node is not None and node.tag not in ("html", "-undef"):
        a = node.attributes
        marks = f"{a.get('class') or ''} {a.get('id') or ''}".lower()
        style = (a.get("style") or "").lower().replace(" ", "")
        role = (a.get("role") or "").lower()
        if ("hidden" in a or (a.get("aria-hidden") or "").lower() == "true"
                or "display:none" in style or "visibility:hidden" in style
                or COOKIE.search(marks) or role in ("dialog", "alertdialog")
                or (a.get("aria-modal") or "").lower() == "true"
                or (node.tag == "dialog" and "open" not in a)):
            return "hidden"
        classes = set((a.get("class") or "").split())
        if node.tag in ("header", "footer") and role not in ("banner", "contentinfo") \
                and not classes & {"site-header", "site-footer", "sidebar"}:
            own_header = True
        elif node.tag in ("nav", "aside", "header", "footer") \
                or role in ("navigation", "banner", "contentinfo", "complementary") \
                or classes & {"site-header", "site-footer", "sidebar"}:
            chrome = True
        if own_header and (node.tag in ("main", "article") or role == "main"):
            own_header = False
        node = node.parent
    return "chrome" if chrome or own_header else "content"


def under(node, tags) -> bool:
    while node is not None and node.tag not in ("html", "-undef"):
        if node.tag in tags:
            return True
        node = node.parent
    return False


def dom_sequence(html: str) -> tuple[list[list], int]:
    """A látható content-headingek sorrendben: [szint, szövegkulcs, az utána álló szöveg szavai,
    az utána álló tartalmi elemek]; és a DOM összes H1-ének száma (bárhol)."""
    tree = HTMLParser(html)
    all_h1 = sum(1 for h in tree.css("h1") if key(h.text(deep=True)))
    items: list[list] = []
    if tree.body is None:
        return items, all_h1
    listed: set[int] = set()
    for node in tree.body.traverse(include_text=True):
        if node.tag in HEADINGS:
            if place(node) == "content" and key(node.text(deep=True)):
                items.append([int(node.tag[1]), key(node.text(deep=True)), 0, set()])
                listed.add(node.mem_id)
            continue
        if not items:
            continue
        probe, in_listed = node.parent, False
        while probe is not None and probe.tag not in ("html", "-undef"):
            if probe.mem_id in listed:
                in_listed = True
                break
            probe = probe.parent
        if in_listed:                          # a heading saját tartalma (link, ikon) nem számít
            continue
        if node.tag == "-text":
            if under(node.parent, NO_TEXT):
                continue
            items[-1][2] += len((node.text(deep=False) or "").split())
        elif node.tag in ELEMENTS:
            if node.tag == "input" and (node.attributes.get("type") or "").lower() == "hidden":
                continue
            items[-1][3].add(node.tag)
        elif node.tag == "a" and any(child.tag != "br"
                                     for child in node.iter(include_text=False)):
            items[-1][3].add("a")
    return items, all_h1


def check(kind: str, page: dict, sequence: list[list], all_h1: int,
          pattern: list[str] | None) -> tuple[bool, str]:
    levels = [item[0] for item in sequence]
    if kind == "missing_h1":
        return all_h1 == 0, f"a DOM-ban {all_h1} H1"
    if kind == "multiple_h1":
        return levels.count(1) >= 2, f"a DOM fő tartalmában {levels.count(1)} H1"
    if kind == "missing_h2":
        return 2 not in levels, f"a DOM fő tartalmában {levels.count(2)} H2"
    if kind == "h1_outside_content":
        return True, "DOM-vizsgálatból jön"
    wrong, seen = [], set()
    for label in page["headings"]:
        level, text = int(label[1]), key(label.split(": ", 1)[1])
        positions = [i for i, item in enumerate(sequence)
                     if item[0] == level and (item[1] == text or text in item[1])]
        if not positions:
            wrong.append(f"{label}: nincs a DOM-sorban")
            continue
        ok = False
        for i in positions:
            if kind in ("empty_section", "paragraph_heading"):
                words = len(label.split(": ", 1)[1].split())
                ok = ok or (i + 1 < len(sequence) and sequence[i + 1][0] <= level
                            and sequence[i][2] == 0 and not sequence[i][3]
                            and (words > PARAGRAPH_WORDS) == (kind == "paragraph_heading"))
                if kind == "empty_section":
                    seen.add(f"H{level}")
            else:
                parent = next((item[0] for item in reversed(sequence[:i]) if item[0] < level),
                              None)
                if parent is not None and level > parent + 1:
                    ok = True
                    seen.add(f"H{parent}→H{level}")
        if not ok:
            wrong.append(f"{label}: a DOM szerint nem áll")
    if pattern is not None and not seen <= set(pattern):
        wrong.append(f"a szintminta eltér: {sorted(seen)} nincs benne ebben: {pattern}")
    return not wrong, "; ".join(wrong) or "egyezik"


def verify(db: Path) -> tuple[list[str], dict[str, bool], Counter]:
    """(a jelentés sorai, verdiktkulcs → megerősítve, (típus, megerősítve) → darab)."""
    con = duckdb.connect(str(db), read_only=True)
    decompressor = zstandard.ZstdDecompressor()
    lines, results, counts = [], {}, Counter()
    try:
        for finding_id, kind, severity, summary, evidence in con.execute(
                "SELECT finding_id, type, severity, summary, evidence FROM findings WHERE "
                "list_contains(?, type) ORDER BY finding_id", [list(STRUCTURE)]).fetchall():
            data = json.loads(evidence)
            pages = data.get("pages") or [data]
            checked = []
            for page in pages:
                blob = con.execute("SELECT rendered_html FROM pages WHERE url = ?",
                                   [page["url"]]).fetchone()[0]
                sequence, all_h1 = dom_sequence(
                    decompressor.decompress(blob).decode("utf-8", "replace"))
                checked.append((page["url"], *check(kind, page, sequence, all_h1,
                                                    data.get("pattern"))))
            real = all(ok for _, ok, _ in checked)
            counts[(kind, real)] += 1
            results[" | ".join((kind, data.get("url") or data.get("group") or ""))] = real
            lines.append(f"- [{'igaz' if real else 'ELTÉR'}] #{finding_id} {kind} ({severity}): "
                         f"{summary} ({len(pages)} oldal)")
            lines += [f"  - {url}: {note}" for url, ok, note in checked
                      if not ok or kind in ("multiple_h1", "missing_h1")]
    finally:
        con.close()
    return lines, results, counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--site", action="append", default=[], help="név=adatbázis")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if not args.site:
        raise SystemExit("--site név=adatbázis kell")
    report, verdicts, total = [], {}, Counter()
    for name, db in (item.split("=", 1) for item in args.site):
        lines, results, counts = verify(Path(db))
        verdicts[name] = results
        total.update(counts)
        report += [f"## {name}", "", *lines, "", "összesítés: " + (", ".join(
            f"{kind} {'igaz' if real else 'eltér'} {n}"
            for (kind, real), n in sorted(counts.items())) or "nincs szerkezeti megállapítás"),
            ""]
        good = sum(n for (_, real), n in counts.items() if real)
        print(f"{name}: {good}/{sum(counts.values())} megerősítve; " + ", ".join(
            f"{kind} {'igaz' if real else 'eltér'} {n}"
            for (kind, real), n in sorted(counts.items())))
    good = sum(n for (_, real), n in total.items() if real)
    print(f"összesen: {good}/{sum(total.values())} megállapítást erősít meg a DOM")
    if args.out:
        args.out.write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    if args.json:
        args.json.write_text(json.dumps(verdicts, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
