"""Blokkmodell: a renderelt DOM látható szövegblokkjai sorszámmal, típussal, régióval és
heading-útvonallal; a `blocks` tábla forrása (M2 spec A2). A forrás közvetlenül a renderelt DOM
(a bemeneti teljesség mérése alapján: a `main_content` lapos szöveg, blokkhatár, heading-útvonal
és táblázatszerkezet nélkül).

- Zajszűrés: a `main_content` kinyerésével azonos (script, style, noscript, iframe, template,
  inline `display:none` / `visibility:hidden`, cookie- / consent-szelektorok); rejtett továbbá a
  `hidden`, az `aria-hidden="true"`, a cookie / consent / gdpr osztályú vagy azonosítójú elem és a
  dialógus (`<dialog>` `open` nélkül, `role="dialog"` / `alertdialog`, `aria-modal="true"`).
- 0. blokk: a title (`pages.title`), ha van.
- Minden blokkszintű elem új blokkot kezd; a soron belüli elemek (a, span, strong, code …) a
  szülő blokkjához tartoznak. A szöveg a szövegcsomópontok szóközzel összefűzve, szóközök
  összevonva. Címkelista: ha egy tároló (nem heading, bekezdés vagy listaelem) közvetlen
  gyerekei csak szöveget hordozó soron belüli elemek, saját szöveg nélkül, a szövegük közé
  „ · ” kerül („GA4 · GTM · Datastream”).
- Rejtett ismétlés: a nem aktív fül (`tab-pane` osztály, `active` / `show` nélkül) nem ad
  blokkot, ha minden szövegblokkja szó szerint megvan a fülön kívüli tartalomban is (a
  példák ismétlése egy rejtett fülön). A saját tartalmú nem aktív fül (API-referencia, a
  példák kódja) marad.
- Típus: heading (h1–h6, a szintjével), paragraph (p), list_item (li, dt, dd), table_row (tr;
  a nem üres cellák a `cells`-ben az oszlopfejléccel, ha a táblázatnak van csupa-th fejlécsora;
  a szöveg a cellák ` | `-vel; div-rácsos táblázat is, lásd lent), code (pre; soronként `<li>`-be tördelt kódnál a sorok új sorral,
  különben a pre szövege, a sortörésekkel), card (legalább két azonos címkéjű és osztályú
  testvér-tároló, mindegyik legfeljebb `CARD_MAX_WORDS` szavas, és van benne heading vagy
  strong / b cím; a benne lévő nem heading blokkok), other (minden más, pl. div közvetlen
  szövege).
- Div-rácsos táblázat (CSS grid sorburkoló nélkül, a cellák egy tároló lapos gyerekei): a
  tároló legalább `GRID_MIN_CELLS` látható gyereke mind div / section cella, heading, lista,
  táblázat és pre nélkül, legfeljebb `GRID_MAX_CELL_WORDS` szóval, a tárolónak nincs saját
  szövege; van olyan 2–`GRID_MAX_COLUMNS` oszlopszám, amellyel a cellák legalább 3 sorba
  rendeződnek, az első sor (fejléc) cellái legfeljebb `GRID_HEADER_WORDS` szavasak és nem
  számosak, a többi sorban oszloponként azonos, hogy a cella számos-e (a szavak legalább fele
  tartalmaz számjegyet), és van számos és nem számos oszlop is. A legkisebb ilyen oszlopszám adja
  a sorokat: a fejlécsor és az adatsorok `table_row` blokkok, a cellák a fejléc szerinti
  oszlopnévvel; egy cella több bekezdése „ · ”-vel.
- Régió: chrome, ha header, nav, footer, aside vagy sidebar elem, navigation, banner,
  contentinfo, complementary ARIA-szerepű elem, vagy `site-header`, `site-footer`, `sidebar`
  osztályú elem alatt áll; egyébként content.
- heading-útvonal: a blokk előtti headingek szintjük szerinti lánca; a headingé a saját
  szövegével együtt.
- anchor: a blokkban álló linkek szövege (`engine.parse.anchor_text`), a determinisztikus kör
  anchor-említéseihez.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import duckdb
import zstandard
from selectolax.parser import HTMLParser, Node

from aaa2.engine.parse import anchor_text

# Ugyanazok, mint az `engine.parse` zajszűrőjéé; tests/test_boundaries.py őrzi.
NOISE_SELECTOR = "script, style, noscript, iframe, template"
COOKIE_SELECTORS = (
    "#cookie-banner", ".cookie-notice", '[class*="cookie"]', '[id*="cookie"]',
    '[class*="consent"]', '[id*="consent"]', '[class*="gdpr"]',
)

KINDS = ("title", "heading", "paragraph", "list_item", "table_row", "code", "card", "other")
INLINE = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "dfn", "em", "font", "i", "img",
    "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var",
    "wbr", "svg", "path", "label", "button", "input", "select", "option", "picture", "source",
})
CELLS = frozenset({"td", "th"})
SKIP = frozenset({"script", "style", "noscript", "iframe", "template", "head"})
CHROME = frozenset({"header", "nav", "footer", "aside", "sidebar"})
CHROME_CLASSES = frozenset({"site-header", "site-footer", "sidebar"})
CHROME_ROLES = frozenset({"navigation", "banner", "contentinfo", "complementary"})
CARD_MAX_WORDS = 120
GRID_MIN_CELLS = 6
GRID_MAX_COLUMNS = 6
GRID_MAX_CELL_WORDS = 40
GRID_HEADER_WORDS = 3
GRID_BLOCKERS = "h1, h2, h3, h4, h5, h6, ul, ol, table, pre"
KIND_OF = {**{f"h{i}": "heading" for i in range(1, 7)}, "p": "paragraph", "li": "list_item",
           "dt": "list_item", "dd": "list_item"}
COOKIE = re.compile(r"cookie|consent|gdpr")
WHITESPACE = re.compile(r"\s+")


@dataclass
class ParsedBlock:
    ordinal: int
    kind: str
    region: str
    text: str
    heading_path: list[str]
    level: int | None = None
    cells: list[dict] | None = None
    anchors: list[str] = field(default_factory=list)
    pane: int | None = None                  # a nem aktív fül, amelyben áll (`mem_id`)


def content_tree(html: str) -> HTMLParser:
    """A fa a `main_content` kinyerésének zajszűrésével."""
    tree = HTMLParser(html or "")
    for node in tree.css(NOISE_SELECTOR):
        node.decompose()
    for node in tree.css("[style]"):
        style = (node.attributes.get("style") or "").lower().replace(" ", "")
        if "display:none" in style or "visibility:hidden" in style:
            node.decompose()
    for selector in COOKIE_SELECTORS:
        for node in tree.css(selector):
            node.decompose()
    return tree


def collapse(text: str) -> str:
    return WHITESPACE.sub(" ", text).strip()


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


def _inactive_tab(node: Node) -> bool:
    classes = set((node.attributes.get("class") or "").split())
    return "tab-pane" in classes and not classes & {"active", "show"}


def _chips(node: Node) -> bool:
    """Címkelista-tároló: nem heading, bekezdés vagy listaelem, nincs saját szövege és
    blokkszintű gyereke, és legalább két szöveget hordozó soron belüli gyereke van."""
    if node.tag in KIND_OF:
        return False
    carriers = 0
    child = node.child
    while child is not None:
        name = child.tag
        if name == "-text":
            if (child.text(deep=False) or "").strip():
                return False
        elif name in SKIP or name == "-comment" or _hidden(child):
            pass
        elif name not in INLINE:
            return False
        elif collapse(child.text(separator=" ") or ""):
            carriers += 1
        child = child.next
    return carriers >= 2


def _chrome(node: Node) -> bool:
    role = (node.attributes.get("role") or "").lower()
    classes = set((node.attributes.get("class") or "").split())
    return node.tag in CHROME or role in CHROME_ROLES or bool(classes & CHROME_CLASSES)


def _signature(node: Node) -> tuple[str, str]:
    return node.tag, " ".join(sorted((node.attributes.get("class") or "").split()))


def card_containers(tree: HTMLParser) -> set[int]:
    """A kártya-tárolók `mem_id`-jei (lásd a modul leírását)."""
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
                    m.css_first("h1, h2, h3, h4, h5, h6, strong, b") is not None
                    and len(m.text(separator=" ").split()) <= CARD_MAX_WORDS
                    for m in members):
                cards.update(m.mem_id for m in members)
    return cards


def _elements(node: Node) -> list[Node] | None:
    """A látható elem-gyerekek; None, ha a csomópontnak saját (nem whitespace) szövege van."""
    out: list[Node] = []
    child = node.child
    while child is not None:
        name = child.tag
        if name == "-text":
            if (child.text(deep=False) or "").strip():
                return None
        elif name not in SKIP and name != "-comment" and not _hidden(child):
            out.append(child)
        child = child.next
    return out


def _cell_text(cell: Node) -> str:
    return collapse(cell.text(separator=" ") or "")


def _digits(text: str) -> bool:
    """Számos cella: a szavak legalább fele tartalmaz számjegyet."""
    words = text.split()
    return bool(words) and 2 * sum(any(ch.isdigit() for ch in w) for w in words) >= len(words)


def grid_containers(tree: HTMLParser) -> dict[int, int]:
    """A div-rácsos táblázatok: tároló `mem_id` → oszlopszám (lásd a modul leírását)."""
    grids: dict[int, int] = {}
    for parent in tree.css("body *"):
        cells = _elements(parent)
        if not cells or len(cells) < GRID_MIN_CELLS or any(
                c.tag not in ("div", "section") or c.css_first(GRID_BLOCKERS) is not None
                for c in cells):
            continue
        texts = [_cell_text(c) for c in cells]
        if any(not t or len(t.split()) > GRID_MAX_CELL_WORDS for t in texts):
            continue
        for width in range(2, GRID_MAX_COLUMNS + 1):
            if len(cells) % width or len(cells) // width < 3:
                continue
            header, data = texts[:width], texts[width:]
            if any(_digits(t) or len(t.split()) > GRID_HEADER_WORDS for t in header):
                continue
            rows = [data[i:i + width] for i in range(0, len(data), width)]
            shapes = [[_digits(t) for t in r] for r in rows]
            if all(s == shapes[0] for s in shapes) and any(shapes[0]) and not all(shapes[0]):
                grids[parent.mem_id] = width
                break
    return grids


def _inline(node: Node, parts: list[str], anchors: list[str]) -> None:
    """A csomópont szövege soron belüliként (minden leszármazott), a rejtett elemek nélkül."""
    child = node.child
    while child is not None:
        name = child.tag
        if name == "-text":
            parts.append((child.text(deep=False) or "").strip())
        elif name in SKIP or name == "-comment" or _hidden(child):
            pass
        else:
            if name == "a" and "href" in child.attributes and (text := anchor_text(child)):
                anchors.append(text)
            _inline(child, parts, anchors)
        child = child.next


def _table(node: Node) -> Node | None:
    parent = node.parent
    while parent is not None and parent.tag != "table":
        parent = parent.parent
    return parent


def parse_blocks(html: str, title: str | None = None) -> list[ParsedBlock]:
    """A látható szövegblokkok dokumentum-sorrendben; a 0. a title, ha van. A rejtett
    ismétlést hordozó nem aktív fülek nélkül (két menet ugyanazon a fán)."""
    tree = content_tree(html)
    first = _parse(tree, title, frozenset())
    visible = {b.text for b in first if b.pane is None}
    panes: dict[int, list[str]] = {}
    for block in first:
        if block.pane is not None:
            panes.setdefault(block.pane, []).append(block.text)
    repeated = frozenset(pane for pane, texts in panes.items()
                         if all(text in visible for text in texts))
    return _parse(tree, title, repeated) if repeated else first


def _parse(tree: HTMLParser, title: str | None, skip_panes: frozenset[int]
           ) -> list[ParsedBlock]:
    blocks: list[ParsedBlock] = []
    if title and collapse(title):
        blocks.append(ParsedBlock(0, "title", "content", collapse(title), []))
    body = tree.body
    if body is None:
        return blocks
    cards = card_containers(tree)
    grids = grid_containers(tree)
    stack: list[tuple[int, str]] = []
    headers: dict[int, list[str]] = {}
    ordinal = 0
    pane: int | None = None

    def emit(kind: str, region: str, text: str, anchors: list[str], level: int | None = None,
             cells: list[dict] | None = None) -> None:
        nonlocal ordinal
        if not text:
            return
        if kind == "heading":
            while stack and stack[-1][0] >= (level or 6):
                stack.pop()
            stack.append((level or 6, text))
        ordinal += 1
        blocks.append(ParsedBlock(ordinal, kind, region, text, [t for _, t in stack], level,
                                  cells, list(anchors), pane))

    def row(tr: Node, region: str) -> None:
        values: list[tuple[str, str]] = []
        anchors: list[str] = []
        child = tr.child
        while child is not None:
            if child.tag in CELLS and not _hidden(child):
                parts: list[str] = []
                _inline(child, parts, anchors)
                values.append((child.tag, collapse(" ".join(p for p in parts if p))))
            child = child.next
        table = _table(tr)
        key = table.mem_id if table is not None else tr.mem_id
        if values and all(tag == "th" for tag, _ in values) and key not in headers:
            headers[key] = [value for _, value in values]
            cells = [{"header": None, "value": value} for _, value in values if value]
        else:
            names = headers.get(key, [])
            cells = [{"header": names[i] if i < len(names) and names[i] else None, "value": value}
                     for i, (_, value) in enumerate(values) if value]
        emit("table_row", region, " | ".join(c["value"] for c in cells), anchors, cells=cells)

    def grid(node: Node, region: str, width: int) -> None:
        cells = _elements(node) or []
        names: list[str] = []
        for start in range(0, len(cells), width):
            values: list[str] = []
            anchors: list[str] = []
            for cell in cells[start:start + width]:
                pieces = []
                for part in _elements(cell) or [cell]:
                    own: list[str] = []
                    _inline(part, own, anchors)
                    if text := collapse(" ".join(o for o in own if o)):
                        pieces.append(text)
                values.append(" · ".join(pieces))
            if not names:
                names = values
                row_cells = [{"header": None, "value": v} for v in values if v]
            else:
                row_cells = [{"header": names[i] or None, "value": v}
                             for i, v in enumerate(values) if v]
            emit("table_row", region, " | ".join(c["value"] for c in row_cells), anchors,
                 cells=row_cells)

    def code(pre: Node, region: str) -> None:
        lines = pre.css("li")
        if lines:
            texts = []
            for line in lines:
                parts: list[str] = []
                _inline(line, parts, [])
                texts.append(collapse(" ".join(p for p in parts if p)))
            text = "\n".join(texts).strip("\n")
        else:
            text = (pre.text(deep=True) or "").strip("\n").rstrip()
        emit("code", region, text, [])

    def walk(node: Node, kind: str, region: str, in_card: bool, level: int | None) -> None:
        nonlocal pane
        parts: list[str] = []
        anchors: list[str] = []
        chips = _chips(node)

        def flush() -> None:
            emit(kind, region, collapse(" ".join(p for p in parts if p)), anchors, level)
            parts.clear()
            anchors.clear()

        child = node.child
        while child is not None:
            name = child.tag
            if name == "-text":
                parts.append((child.text(deep=False) or "").strip())
            elif name in SKIP or name == "-comment" or _hidden(child):
                pass
            elif name in INLINE:
                if name == "a" and "href" in child.attributes and (text := anchor_text(child)):
                    anchors.append(text)
                if chips:
                    own: list[str] = []
                    _inline(child, own, anchors)
                    if any(own) and any(parts):
                        parts.append("·")
                    parts.extend(own)
                else:
                    _inline(child, parts, anchors)
            elif child.mem_id in skip_panes:
                pass
            else:
                flush()
                child_region = "chrome" if region == "chrome" or _chrome(child) else "content"
                child_card = in_card or child.mem_id in cards
                if name == "tr":
                    row(child, child_region)
                elif child.mem_id in grids:
                    grid(child, child_region, grids[child.mem_id])
                elif name == "pre":
                    code(child, child_region)
                else:
                    child_kind = KIND_OF.get(name, "other")
                    if child_card and child_kind != "heading":
                        child_kind = "card"
                    child_level = int(name[1]) if child_kind == "heading" else None
                    outer = pane
                    if pane is None and _inactive_tab(child):
                        pane = child.mem_id
                    walk(child, child_kind, child_region, child_card, child_level)
                    pane = outer
            child = child.next
        flush()

    walk(body, "other", "content", False, None)
    return blocks


# ---------------------------------------------------------------------------
# a blocks tábla
# ---------------------------------------------------------------------------


def build_blocks(con: duckdb.DuckDBPyConnection, page_ids: Sequence[int] | None = None) -> int:
    """A `blocks` sorai azoknak a sikeres (2xx, hiba nélküli, renderelt DOM-mal bíró) oldalaknak,
    amelyeknek még nincs blokkjuk; `page_ids`: csak ezek közül. Visszaad: a feldolgozott oldalak
    száma. A meglévő blokkok nem változnak (az említések a `block_id`-jükre hivatkoznak)."""
    params: list = []
    only = ""
    if page_ids is not None:
        only = " AND list_contains(?, page_id)"
        params.append(list(page_ids))
    pages = con.execute(
        "SELECT page_id, title, rendered_html FROM pages "
        "WHERE status BETWEEN 200 AND 299 AND error IS NULL AND rendered_html IS NOT NULL "
        "AND page_id NOT IN (SELECT DISTINCT page_id FROM blocks)" + only + " ORDER BY page_id",
        params,
    ).fetchall()
    decompressor = zstandard.ZstdDecompressor()
    for page_id, title, blob in pages:
        html = decompressor.decompress(blob).decode("utf-8", "replace")
        rows = [[page_id, b.ordinal, b.kind, b.region, b.level, b.heading_path, b.text,
                 json.dumps(b.cells, ensure_ascii=False) if b.cells is not None else None]
                for b in parse_blocks(html, title)]
        if rows:
            con.executemany(
                "INSERT INTO blocks (page_id, ordinal, kind, region, level, heading_path, text, "
                "cells) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(pages)


def page_blocks(con: duckdb.DuckDBPyConnection, page_id: int, region: str | None = None
                ) -> list[dict]:
    """Egy oldal blokkjai sorszám szerint, szótárként (`id`: `b<sorszám>`, ahogy az LLM-bemenet
    hivatkozik rájuk); `region`: csak ez a régió."""
    rows = con.execute(
        "SELECT block_id, ordinal, kind, region, level, heading_path, text, cells FROM blocks "
        "WHERE page_id = ?" + (" AND region = ?" if region else "") + " ORDER BY ordinal",
        [page_id, *([region] if region else [])],
    ).fetchall()
    return [{"block_id": block_id, "id": f"b{ordinal}", "ordinal": ordinal, "kind": kind,
             "region": block_region, "level": level, "heading_path": list(path or []),
             "text": text, "cells": json.loads(cells) if cells else None}
            for block_id, ordinal, kind, block_region, level, path, text, cells in rows]
