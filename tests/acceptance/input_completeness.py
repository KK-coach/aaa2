"""Bemeneti teljesség, LLM nélkül: a renderelt DOM látható szövegblokkjai és a `main_content`
összevetése, blokktípus szerint, a rögzített készletek minden 2xx oldalán.

    python -m tests.acceptance.input_completeness [--data-dir data/compare] [--out DIR]

Blokkok (`dom_blocks`), a renderelt DOM-ból, ugyanazon a zajszűrt fán, amelyből a `main_content`
készül (`engine.parse._content_tree`: script, style, noscript, iframe, template, inline
`display:none` / `visibility:hidden` és a cookie-/consent-szelektorok nélkül):

- láthatatlan továbbá, ami `hidden` vagy `aria-hidden="true"`, aminek az osztálya vagy
  azonosítója cookie / consent / gdpr, és a dialógus (`<dialog>` `open` nélkül, `role="dialog"`
  / `alertdialog`, `aria-modal="true"`), mert csak műveletre jelenik meg; a CSS-osztállyal rejtett
  tartalom (fülek, lenyílók) a mentett DOM-ból nem látszik rejtettnek, az láthatónak számít;
- minden blokkszintű elem új blokkot kezd, a soron belüli elemek (a, span, strong, code …) a
  szülő blokkjához tartoznak; a blokk szövege a szövegcsomópontok szóközzel összefűzve,
  szóközök összevonva (a `main_content` is így készül);
- típus: heading (h1–h6), paragraph (p), list_item (li, dt, dd), table_row (tr: a nem üres
  cellák ` | `-vel, a cellán belül minden elem soron belüli), code (pre: benne minden elem soron
  belüli, a soronként `<li>`-be tördelt kód is egy blokk), card (kártya: legalább két azonos
  címkéjű és osztályú testvér-tároló, mindegyik legfeljebb 120 szavas, és van benne heading vagy
  strong/b cím; a benne lévő nem heading blokkok), other (minden más, pl. div közvetlen
  szövege);
- régió: chrome, ha header, nav, footer, aside vagy sidebar elem alatt, navigation, banner,
  contentinfo, complementary ARIA-szerepű elem alatt, vagy `site-header`, `site-footer`,
  `sidebar` osztályú elem alatt áll; egyébként content. Tágabb osztályminta nincs, mert a
  tartalomban is előfordul (`card-header`).

Lefedettség: egy blokk lefedett, ha a DOM-ban a `main_content` gyökerén belül áll. A gyökér
ugyanazzal a kiválasztással készül, mint az `engine.parse.extract_main_content` (main, article,
role=main, tartalomszelektor, olvashatóság, végül a body header / nav / footer / aside nélkül);
oldalanként ellenőrizve, hogy a gyökér szövege a tárolt `main_content`. Oldalanként:
szószám szerinti lefedettség a content régióban, típusonként; a chrome szavai a gyökéren belül
(zaj a bemenetben); a kimaradt content-blokkok listája. A zárolt tesztoldalaknál
(`locked_pages.json`) csak számok, szöveg nélkül.

Csonkolás: az LLM-bemenet (`entities.llm.page_input`) a `main_content` első 80 000 karakterét
viszi; site-onként hány oldalt érint, mennyi marad le, és a leghosszabb main content.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import zstandard
from selectolax.parser import HTMLParser, Node

from aaa2.db.connect import DATA_DIR
from aaa2.engine.parse import (
    _CONTENT_SELECTORS,
    MIN_MAIN_CONTENT_WORDS,
    _content_tree,
    _readability_score,
    _text,
)
from aaa2.entities.llm import MAX_INPUT_CHARS

SITES = ("kk-coach-crawl", "materia-crawl", "ngx-bootstrap-crawl")
OUT_DIR = Path(__file__).parent / "out" / "input-completeness"
LOCKED = Path(__file__).parent / "locked_pages.json"
KINDS = ("heading", "paragraph", "list_item", "table_row", "code", "card", "other")

INLINE = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "dfn", "em", "font", "i", "img",
    "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var",
    "wbr", "svg", "path", "label", "button", "input", "select", "option", "picture", "source",
})
CELLS = frozenset({"td", "th"})
CARD_MAX_WORDS = 120
SKIP = frozenset({"script", "style", "noscript", "iframe", "template", "head"})
CHROME = frozenset({"header", "nav", "footer", "aside", "sidebar"})
CHROME_CLASSES = frozenset({"site-header", "site-footer", "sidebar"})
CHROME_ROLES = frozenset({"navigation", "banner", "contentinfo", "complementary"})
FALLBACK_DROPPED = frozenset({"header", "nav", "footer", "aside"})
KIND_OF = {**{f"h{i}": "heading" for i in range(1, 7)}, "p": "paragraph", "li": "list_item",
           "dt": "list_item", "dd": "list_item", "tr": "table_row", "pre": "code"}
COOKIE = re.compile(r"cookie|consent|gdpr")
WHITESPACE = re.compile(r"\s+")


@dataclass
class Block:
    kind: str
    region: str
    text: str
    tag: str
    in_root: bool = False

    @property
    def words(self) -> int:
        return len(self.text.split())


def _hidden(node: Node) -> bool:
    attrs = node.attributes
    if "hidden" in attrs or (attrs.get("aria-hidden") or "").lower() == "true":
        return True
    if node.tag == "dialog" and "open" not in attrs:
        return True
    if (attrs.get("role") or "").lower() in ("dialog", "alertdialog"):
        return True
    if (attrs.get("aria-modal") or "").lower() == "true":
        return True
    marks = f"{attrs.get('class') or ''} {attrs.get('id') or ''}".lower()
    return bool(COOKIE.search(marks))


def _signature(node: Node) -> tuple[str, str]:
    return node.tag, " ".join(sorted((node.attributes.get("class") or "").split()))


def _has_title(node: Node) -> bool:
    return node.css_first("h1, h2, h3, h4, h5, h6, strong, b") is not None


def card_containers(tree: HTMLParser) -> set[int]:
    """A kártyák: legalább két azonos címkéjű és osztályú (osztállyal bíró) testvér-tároló,
    mindegyik legfeljebb `CARD_MAX_WORDS` szavas, és van benne heading vagy strong/b cím.
    Visszaad: a tárolók `mem_id`-jei."""
    cards: set[int] = set()
    for parent in tree.css("body *"):
        groups: dict[tuple[str, str], list[Node]] = {}
        child = parent.child
        while child is not None:
            if child.tag in ("div", "section", "article", "li") \
                    and child.attributes.get("class"):
                groups.setdefault(_signature(child), []).append(child)
            child = child.next
        for members in groups.values():
            if len(members) >= 2 and all(
                    _has_title(m) and len(m.text(separator=" ").split()) <= CARD_MAX_WORDS
                    for m in members):
                cards.update(m.mem_id for m in members)
    return cards


def main_root(tree: HTMLParser) -> tuple[Node | None, str]:
    """A `main_content` gyökere és a módszer, az `extract_main_content` kiválasztása szerint
    (a zajszűrt fán). `fallback_body`-nál a gyökér a body; a header / nav / footer / aside
    alatti rész nem tartozik bele."""
    for method, selector in (("semantic_main", "main"), ("semantic_article", "article")):
        candidates = tree.css(selector)
        if candidates:
            node = max(candidates, key=lambda n: len(_text(n)))
            if len(_text(node).split()) > MIN_MAIN_CONTENT_WORDS:
                return node, method
    for node in tree.css('[role="main"]'):
        if len(_text(node).split()) > MIN_MAIN_CONTENT_WORDS:
            return node, "role_main"
    for selector in _CONTENT_SELECTORS:
        node = tree.css_first(selector)
        if node is not None and len(_text(node).split()) > MIN_MAIN_CONTENT_WORDS:
            return node, "content_selector"
    best, best_score = None, -1.0
    for node in tree.css("div, section"):
        text = _text(node)
        if len(text.split()) <= MIN_MAIN_CONTENT_WORDS:
            continue
        score = _readability_score(node, text)
        if score > best_score:
            best, best_score = node, score
    if best is not None:
        return best, "readability"
    return tree.body, "fallback_body"


def root_text(root: Node | None, method: str) -> str:
    """A gyökér szövege úgy, ahogy az `extract_main_content` adja."""
    if root is None:
        return ""
    if method != "fallback_body":
        return _text(root)
    copy = HTMLParser(root.html or "")
    for node in copy.css(", ".join(sorted(FALLBACK_DROPPED))):
        node.decompose()
    return _text(copy.body)


def dom_blocks(html: str) -> tuple[list[Block], str, str]:
    """A renderelt DOM látható szövegblokkjai dokumentum-sorrendben (lásd a modul leírását), a
    `main_content` gyökerén belüliek megjelölve; mellé a módszer és a gyökér szövege."""
    tree = _content_tree(html)
    body = tree.body
    if body is None:
        return [], "fallback_body", ""
    root, method = main_root(tree)
    root_id = root.mem_id if root is not None else None
    cards = card_containers(tree)
    blocks: list[Block] = []

    def flush(parts: list[str], tag: str, kind: str, region: str, in_root: bool) -> None:
        text = WHITESPACE.sub(" ", " ".join(p for p in parts if p)).strip()
        if text:
            blocks.append(Block(kind, region, text, tag, in_root))

    def walk(node: Node, tag: str, kind: str, region: str, in_card: bool, in_root: bool,
             parts: list[str]) -> None:
        child = node.child
        while child is not None:
            name = child.tag
            if name == "-text":
                parts.append((child.text(deep=False) or "").strip())
            elif name in SKIP or name == "-comment" or _hidden(child):
                pass
            elif kind == "table_row" and name in CELLS:
                cell: list[str] = []
                walk(child, tag, "table_cell", region, in_card, in_root, cell)
                if text := " ".join(p for p in cell if p):
                    parts += [text, "|"]
            elif name in INLINE or kind in ("table_row", "table_cell", "code"):
                walk(child, tag, kind, region, in_card, in_root, parts)
            else:
                flush(parts, tag, kind, region, in_root)
                parts.clear()
                role = (child.attributes.get("role") or "").lower()
                classes = set((child.attributes.get("class") or "").split())
                chrome = name in CHROME or role in CHROME_ROLES or bool(classes & CHROME_CLASSES)
                child_region = "chrome" if chrome or region == "chrome" else "content"
                child_root = in_root or child.mem_id == root_id
                if method == "fallback_body" and name in FALLBACK_DROPPED:
                    child_root = False
                child_card = in_card or child.mem_id in cards
                child_kind = KIND_OF.get(name, "other")
                if child_card and child_kind != "heading":
                    child_kind = "card"
                inner: list[str] = []
                walk(child, name, child_kind, child_region, child_card, child_root, inner)
                if child_kind == "table_row":
                    while inner and inner[-1] == "|":
                        inner.pop()
                flush(inner, name, child_kind, child_region, child_root)
            child = child.next

    top: list[str] = []
    body_root = method == "fallback_body"
    walk(body, "body", "other", "content", False, body_root, top)
    flush(top, "body", "other", "content", body_root)
    return blocks, method, root_text(root, method)


def normalize(text: str) -> str:
    return WHITESPACE.sub(" ", text).strip()


@dataclass
class PageReport:
    site: str
    url: str
    locked: bool
    method: str
    main_chars: int
    truncated_chars: int
    extractor_matches: bool
    words: Counter = field(default_factory=Counter)          # (régió, típus, lefedett) → szó
    blocks: Counter = field(default_factory=Counter)         # (régió, típus, lefedett) → blokk
    missing: list[Block] = field(default_factory=list)       # content régió, nem lefedett

    def coverage(self, region: str | None = "content", kind: str | None = None
                 ) -> tuple[int, int]:
        """(lefedett szó, összes szó) a régióban (None: mind), típusra szűrve (None: mind)."""
        def match(key):
            return (region is None or key[0] == region) and (kind is None or key[1] == kind)
        total = sum(n for key, n in self.words.items() if match(key))
        hit = sum(n for key, n in self.words.items() if match(key) and key[2])
        return hit, total


def page_report(site: str, url: str, html: str, main_content: str, method: str,
                locked: bool) -> PageReport:
    blocks, root_method, text = dom_blocks(html)
    report = PageReport(site, url, locked, method, len(main_content),
                        max(0, len(main_content) - MAX_INPUT_CHARS),
                        root_method == method and normalize(text) == normalize(main_content))
    for block in blocks:
        report.words[(block.region, block.kind, block.in_root)] += block.words
        report.blocks[(block.region, block.kind, block.in_root)] += 1
        if not block.in_root and block.region == "content":
            report.missing.append(block)
    return report


def read_pages(db: Path) -> Iterator[tuple[str, str, str, str]]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT url, rendered_html, main_content, main_content_method FROM pages "
            "WHERE status BETWEEN 200 AND 299 AND rendered_html IS NOT NULL ORDER BY url"
        ).fetchall()
    finally:
        con.close()
    unzip = zstandard.ZstdDecompressor()
    for url, compressed, main_content, method in rows:
        yield url, unzip.decompress(compressed).decode("utf-8"), main_content or "", method


def locked_urls(path: Path = LOCKED) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {url for site in data["sites"].values() for url in site["urls"]}


# ---------------------------------------------------------------------------
# jelentés
# ---------------------------------------------------------------------------


def _pct(hit: int, total: int) -> str:
    return f"{hit / total * 100:.1f}% ({hit}/{total})" if total else "—"


def summary_markdown(reports: list[PageReport]) -> list[str]:
    lines = ["## Összesítés site-onként", "",
             ("Szószám szerinti lefedettség a content régióban, típusonként: a `main_content` "
              "gyökerén belüli szó / összes látható szó. Chrome a gyökérben: a header, nav, "
              "footer, aside stb. szavai, amelyek a `main_content`-be is bekerülnek (zaj)."), "",
             ("| site | oldal | content | " + " | ".join(KINDS) + " | chrome a gyökérben | "
              "csonkolt oldal (> 80 000 kar.) | levágott karakter | leghosszabb main content | "
              "gyökér = tárolt |"),
             "|---|---|---|" + "---|" * len(KINDS) + "---|---|---|---|---|"]
    for site in SITES:
        pages = [r for r in reports if r.site == site]
        if not pages:
            continue

        def total(region, kind=None, pages=pages):
            hit = sum(r.coverage(region, kind)[0] for r in pages)
            return hit, sum(r.coverage(region, kind)[1] for r in pages)

        truncated = [r for r in pages if r.truncated_chars]
        lines.append(
            f"| {site} | {len(pages)} | {_pct(*total('content'))} | "
            + " | ".join(_pct(*total("content", k)) for k in KINDS)
            + f" | {_pct(*total('chrome'))} | {len(truncated)} | "
            f"{sum(r.truncated_chars for r in truncated)} | "
            f"{max(r.main_chars for r in pages)} | "
            f"{sum(r.extractor_matches for r in pages)}/{len(pages)} |")
    return lines + [""]


def page_markdown(report: PageReport, limit: int = 40) -> list[str]:
    hit, total = report.coverage()
    chrome_hit, _ = report.coverage("chrome")
    lines = [f"### {report.url}", "",
             f"- módszer: `{report.method}`; main content {report.main_chars} karakter"
             + (f", csonkolva {report.truncated_chars} karakter" if report.truncated_chars else "")
             + ("" if report.extractor_matches else "; a gyökér szövege eltér a tárolttól"),
             f"- lefedettség (content): {_pct(hit, total)}; típusonként: "
             + ", ".join(f"{k} {_pct(*report.coverage('content', k))}" for k in KINDS
                         if report.coverage("content", k)[1]),
             f"- chrome a gyökérben: {chrome_hit} szó"]
    missing_words = sum(b.words for b in report.missing)
    if report.locked:
        lines.append(f"- zárolt tesztoldal: {len(report.missing)} kimaradt blokk, "
                     f"{missing_words} szó (szöveg nélkül)")
        return lines + [""]
    lines.append(f"- kimaradt blokkok (content): {len(report.missing)}, {missing_words} szó")
    for block in report.missing[:limit]:
        text = block.text if len(block.text) <= 160 else block.text[:157] + "…"
        lines.append(f"  - {block.kind} `<{block.tag}>`: {text}")
    if len(report.missing) > limit:
        lines.append(f"  - … és még {len(report.missing) - limit}")
    return lines + [""]


def report_markdown(reports: list[PageReport]) -> str:
    lines = ["# Bemeneti teljesség: renderelt DOM és main content", "",
             ("LLM nélkül, a rögzített készletek minden 2xx oldalán. A blokkolás és a lefedettség "
              "szabálya a `tests/acceptance/input_completeness.py` leírásában."), "",
             *summary_markdown(reports), "## Oldalanként", ""]
    for site in SITES:
        lines += [f"## {site}", ""]
        for report in (r for r in reports if r.site == site):
            lines += page_markdown(report)
    return "\n".join(lines) + "\n"


def measure(data_dir: Path) -> list[PageReport]:
    locked = locked_urls()
    reports = []
    for site in SITES:
        for url, html, main_content, method in read_pages(data_dir / f"{site}.duckdb"):
            reports.append(page_report(site, url, html, main_content, method, url in locked))
    return reports


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR / "compare")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    reports = measure(args.data_dir)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / "report.md"
    out.write_text(report_markdown(reports), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
