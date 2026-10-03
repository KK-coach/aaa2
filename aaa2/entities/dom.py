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
  osztályú elem alatt áll; egyébként content. Kivétel a fő tartalom saját fejléce: a `main`,
  `article` vagy `role="main"` elemen belüli `header` (ha a szerepe vagy az osztálya nem jelöli
  chrome-nak) content, pl. a cikk címsora és bevezetője; a benne álló `nav` chrome marad, és a
  `footer` a fő tartalmon belül is chrome.
- Beágyazott heading: a soron belüli elembe (link, span …) vagy táblázatcellába ágyazott
  heading is heading-blokkot ad, és a szövege csak ott szerepel.
  - Soron belüli burkoló (`<a><h2>…</h2><p>…</p></a>`, `<span><h3>…</h3></span>`): ha a
    burkolóban látható, nem üres heading áll, a burkoló nem olvad a szülő blokk szövegébe,
    hanem tárolóként járjuk be: a heading heading-blokk, a burkoló többi szövege a saját
    blokkja(i) a dokumentum sorrendjében. A link anchor-szövege a burkolóból elsőként kiadott
    blokké.
  - Táblázatcella (`<td><h2>…</h2>…</td>`): a cella headingje heading-blokk a sor blokkja
    előtt, a cella értékéből a heading szövege kimarad (a sor a maradék cellaszöveggel jön;
    üresen nem ad blokkot).
- heading-útvonal: a blokk előtti headingek szintjük szerinti lánca; a headingé a saját
  szövegével együtt.
- anchor: a blokkban álló linkek szövege (`engine.parse.anchor_text`), a determinisztikus kör
  anchor-említéseihez.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import duckdb
import zstandard
from selectolax.parser import HTMLParser, Node

from aaa2.engine import queries as crawl
from aaa2.engine.parse import anchor_text
from aaa2.entities import store

# Ugyanazok, mint az `engine.parse` zajszűrőjéé; tests/test_boundaries.py őrzi.
NOISE_SELECTOR = "script, style, noscript, iframe, template"
COOKIE_SELECTORS = (
    "#cookie-banner", ".cookie-notice", '[class*="cookie"]', '[id*="cookie"]',
    '[class*="consent"]', '[id*="consent"]', '[class*="gdpr"]',
)

INLINE = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "dfn", "em", "font", "i", "img",
    "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var",
    "wbr", "svg", "path", "label", "button", "input", "select", "option", "picture", "source",
})
# Szöveg szintű elemek: a határukon a szöveg folyik tovább (szó közepén is), elválasztó nélkül.
INLINE_TAGS = INLINE - {"br", "img", "svg", "path", "label", "button", "input", "select",
                        "option", "picture", "source"}
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
HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# Szöveg nélküli tartalom két heading között (`heading_gaps`).
GAP_ELEMENTS = frozenset({"img", "picture", "svg", "video", "audio", "canvas", "object", "embed",
                          "iframe", "form", "input", "select", "textarea", "button", "table"})
GAP_NO_TEXT = frozenset({"script", "style", "noscript", "template", "iframe"})
WHITESPACE = re.compile(r"\s+")
# Soron belüli elem, amelynek a teljes szövege technikai azonosító (számcsoportok „|”-vel,
# pl. a menü AJAX-paramétere: `316001|709257`); a stíluslap rejti, nem látható szöveg.
TECHNICAL_ID = re.compile(r"\s*\d+(?:\|\d+)+\s*")
# Zárt, rálebbenő felületi panel (lenyíló, hamburger- és oldalsó menü, bejelentkezési doboz)
# osztálya: navigáció és űrlap, nem tartalom (chrome). A Bootstrap `dropdown-menu` nem ilyen:
# a dokumentációs példákban tartalom.
OVERLAY_CLASS = re.compile(r"__dropdown|dropdown--|dropdown[-_]content|(?:^|[-_])hamburger|"
                           r"(?:^|[-_])off-?canvas|^responsive[-_]menu")
TAB_TABLE_MIN_LINES = 2


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


def heading_scan(html: str) -> tuple[list[tuple[str, str]], set[int]]:
    """A renderelt DOM headingjei közvetlenül a DOM-ból (a blokkmodelltől függetlenül, amely a
    soron belüli elembe, pl. linkbe ágyazott headinget nem adja ki): (a H1-ek (hol, szöveg)
    párjai a DOM sorrendjében, a fő tartalom látható headingjeinek szintjei).

    Hol: `content` (a fő tartalomban), `cookie` (cookie-, consent- vagy gdpr-jelölésű elemben,
    ahogy a zajszűrés felismeri), `dialog` (popup vagy modális ablak: `<dialog>`,
    `role="dialog"` / `alertdialog`, `aria-modal="true"`), `chrome` (fejléc, menü, lábléc,
    oldalsáv: a blokkok chrome-régiója, kivéve a fő tartalom saját fejlécét és láblécét: a
    `main`, `article` vagy `role="main"` elemen belüli `header` / `footer` a tartalom része,
    pl. a cikk címsora). A cookie- és a dialógus-elem H1-e akkor is számít, ha az elem épp
    rejtett (a DOM-ban ott van); az egyéb okból rejtett és az üres heading nem."""
    tree = HTMLParser(html or "")
    for node in tree.css(NOISE_SELECTOR):
        node.decompose()
    found: list[tuple[str, str]] = []
    levels: set[int] = set()
    for heading in tree.css("h1, h2, h3, h4, h5, h6"):
        text = collapse(heading.text(deep=True) or "")
        if not text:
            continue
        cookie = dialog = chrome = invisible = own_header = False
        node = heading
        while node is not None and node.tag not in ("html", "-undef"):
            attrs = node.attributes
            marks = f"{attrs.get('class') or ''} {attrs.get('id') or ''}".lower()
            style = (attrs.get("style") or "").lower().replace(" ", "")
            cookie = cookie or bool(COOKIE.search(marks))
            dialog = dialog or node.tag == "dialog" \
                or (attrs.get("role") or "").lower() in ("dialog", "alertdialog") \
                or (attrs.get("aria-modal") or "").lower() == "true"
            if node.tag in ("header", "footer") and not _chrome_marked(node):
                own_header = True              # a fő tartalmon belül nem chrome (lásd lent)
            elif _chrome(node):
                chrome = True
            if own_header and (node.tag in ("main", "article")
                               or (attrs.get("role") or "").lower() == "main"):
                own_header = False
            invisible = invisible or "hidden" in attrs \
                or (attrs.get("aria-hidden") or "").lower() == "true" \
                or "display:none" in style or "visibility:hidden" in style
            node = node.parent
        chrome = chrome or own_header          # a `main` / `article` nélküli header az oldalé
        where = "cookie" if cookie else "dialog" if dialog else None if invisible \
            else "chrome" if chrome else "content"
        level = int(heading.tag[1])
        if where == "content":
            levels.add(level)
        if level == 1 and where is not None:
            found.append((where, text))
    return found, levels


def heading_gaps(html: str) -> list[tuple[int, str, int, tuple[str, ...]]]:
    """A renderelt DOM minden nem üres headingje a dokumentum sorrendjében, azzal, ami utána a
    következő headingig áll: (szint, szöveg, a szöveg szavainak száma, a tartalmi elemek
    címkéi rendezve). A szövegbe a rejtett elem szövege is beleszámít (lenyíló panel, fül: a
    DOM-ban ott a tartalom); kimarad a `script`, `style`, `noscript`, `template` és az `iframe`
    belseje. Tartalmi elem (`GAP_ELEMENTS`): kép, videó, beágyazás, űrlap és űrlapelem,
    táblázat, valamint a link, amelyben elem áll (`a`: linkbe ágyazott kép, kártya)."""
    tree = HTMLParser(html or "")
    found: list[list] = []
    if tree.body is None:
        return []
    inside: Node | None = None                 # a heading, amelynek a részfájában járunk
    for node in tree.body.traverse(include_text=True):
        if inside is not None:
            probe = node.parent
            while probe is not None and probe.mem_id != inside.mem_id:
                probe = probe.parent
            if probe is not None:
                continue
            inside = None
        if node.tag in HEADING_TAGS:
            text = collapse(node.text(deep=True) or "")
            if text:
                found.append([int(node.tag[1]), text, 0, set()])
                inside = node
            continue
        if not found:
            continue
        if node.tag == "-text":
            probe = node.parent
            while probe is not None and probe.tag not in GAP_NO_TEXT:
                probe = probe.parent
            if probe is None:
                found[-1][2] += len((node.text(deep=False) or "").split())
        elif node.tag in GAP_ELEMENTS:
            if node.tag == "input" and (node.attributes.get("type") or "").lower() == "hidden":
                continue
            found[-1][3].add(node.tag)
        elif node.tag == "a" and any(child.tag not in ("-text", "-comment", "br")
                                     for child in node.iter(include_text=False)):
            found[-1][3].add("a")
    return [(level, text, words, tuple(sorted(elements)))
            for level, text, words, elements in found]


def outside_h1(html: str) -> list[tuple[str, str]]:
    """A renderelt DOM H1-ei, amelyek nem a fő tartalomban állnak: (hol, szöveg) párok a DOM
    sorrendjében (`heading_scan`; hol: `cookie`, `dialog` vagy `chrome`)."""
    return [(where, text) for where, text in heading_scan(html)[0] if where != "content"]


def collapse(text: str) -> str:
    """A whitespace egy szóközzé, a lágy kötőjel (U+00AD) és a nulla szélességű szóköz
    törölve."""
    return WHITESPACE.sub(" ", text.replace("\u00ad", "").replace("\u200b", "")).strip()


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
    if COOKIE.search(marks):
        return True
    return (node.tag in INLINE and node.tag != "a"
            and bool(TECHNICAL_ID.fullmatch(node.text(deep=True) or "")))


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
    return (node.tag in CHROME or role in CHROME_ROLES or bool(classes & CHROME_CLASSES)
            or any(OVERLAY_CLASS.search(c.lower()) for c in classes))


def _chrome_marked(node: Node) -> bool:
    """Chrome a szerepe vagy az osztálya szerint (nem csak a címkéje miatt)."""
    role = (node.attributes.get("role") or "").lower()
    classes = set((node.attributes.get("class") or "").split())
    return (role in CHROME_ROLES or bool(classes & CHROME_CLASSES)
            or any(OVERLAY_CLASS.search(c.lower()) for c in classes))


def _tab_rows(node: Node) -> list[list[str]] | None:
    """Tabulátorral tagolt táblázat egy szövegtárolóban (táblázatkezelőből bemásolt műszaki
    adatok): a `<br>`-rel tört sorok közül legalább `TAB_TABLE_MIN_LINES` tartalmaz
    tabulátort; soronként a tabulátorok mentén a cellák, az üresen maradt utolsó cellát a
    következő, tabulátor nélküli sor tölti ki. Ha a tárolónak blokkszintű gyereke van, vagy nincs
    elég tabulátoros sor: None."""
    lines, current = [], []
    child = node.child
    while child is not None:
        name = child.tag
        if name == "-text":
            current.append(child.text(deep=False) or "")
        elif name == "br":
            lines.append("".join(current))
            current = []
        elif name in SKIP or name == "-comment" or _hidden(child):
            pass
        elif name in INLINE:
            current.append(child.text(deep=True) or "")
        else:
            return None
        child = child.next
    lines.append("".join(current))
    # a forráskód behúzása (sortörés utáni szóköz és tabulátor) nem cellahatár
    lines = [" ".join(piece.lstrip(" \t\r") for piece in line.split("\n")).strip(" \r")
             for line in lines]
    if sum("\t" in line for line in lines) < TAB_TABLE_MIN_LINES:
        return None
    rows: list[list[str]] = []
    for line in lines:
        if not line.strip():
            continue
        if "\t" in line:
            rows.append([collapse(cell) for cell in line.strip("\r\n ").split("\t")])
        elif rows and rows[-1][-1] == "":
            rows[-1][-1] = collapse(line)
        else:
            rows.append([collapse(line)])
    return rows if len(rows) >= TAB_TABLE_MIN_LINES else None


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
    parts: list[str] = []
    _inline(cell, parts, [])
    return inline_text(parts)


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
    """A csomópont szövege soron belüliként (minden leszármazott), a rejtett elemek nélkül. A
    szövegdarabok a saját whitespace-ükkel kerülnek a listába; soron belüli elem (`INLINE_TAGS`,
    pl. `<strong>`, `<wbr>`) határán nincs elválasztó (a szó közepén kezdődő kiemelés nem
    töri a szót), más elem és a `<br>` előtt és után szóköz; két közvetlenül egymást követő
    link két címke, köztük szóköz. A darabok `"".join`-nal fűzendők (`inline_text`)."""
    child = node.child
    after_link = False
    while child is not None:
        name = child.tag
        if name == "-text":
            parts.append(child.text(deep=False) or "")
            after_link = False
        elif name in SKIP or name == "-comment" or _hidden(child):
            pass
        else:
            if name == "a" and "href" in child.attributes and (text := anchor_text(child)):
                anchors.append(text)
            inline = name in INLINE_TAGS and not (name == "a" and after_link)
            if not inline:
                parts.append(" ")
            _inline(child, parts, anchors)
            if not inline:
                parts.append(" ")
            after_link = name == "a"
        child = child.next


def inline_text(parts: list[str]) -> str:
    """Az `_inline` darabjai egy szöveggé, a whitespace összevonva."""
    return collapse("".join(parts))


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
                values.append((child.tag, inline_text(parts)))
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
                    if text := inline_text(own):
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
                texts.append(inline_text(parts))
            text = "\n".join(texts).strip("\n")
        else:
            text = (pre.text(deep=True) or "").strip("\n").rstrip()
        emit("code", region, text, [])

    def walk(node: Node, kind: str, region: str, in_card: bool, level: int | None) -> None:
        nonlocal pane
        if tab_rows := _tab_rows(node):
            names = tab_rows[0]
            emit("table_row", region, " | ".join(n for n in names if n), [],
                 cells=[{"header": None, "value": n} for n in names if n])
            for values in tab_rows[1:]:
                cells = [{"header": names[i] if i < len(names) and names[i] else None,
                          "value": value} for i, value in enumerate(values) if value]
                emit("table_row", region, " | ".join(c["value"] for c in cells), [],
                     cells=cells)
            return
        parts: list[str] = []
        anchors: list[str] = []
        chips = _chips(node)

        def flush() -> None:
            emit(kind, region, inline_text(parts), anchors, level)
            parts.clear()
            anchors.clear()

        child = node.child
        after_link = False
        while child is not None:
            name = child.tag
            if name == "-text":
                parts.append(child.text(deep=False) or "")
                after_link = False
            elif name in SKIP or name == "-comment" or _hidden(child):
                pass
            elif name in INLINE:
                if name == "a" and "href" in child.attributes and (text := anchor_text(child)):
                    anchors.append(text)
                if chips:
                    own: list[str] = []
                    _inline(child, own, anchors)
                    if "".join(own).strip() and "".join(parts).strip():
                        parts.append(" · ")
                    parts.extend(own)
                else:
                    joined = name in INLINE_TAGS and not (name == "a" and after_link)
                    gap = [] if joined else [" "]
                    parts.extend(gap)
                    _inline(child, parts, anchors)
                    parts.extend(gap)
                after_link = name == "a"
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
    száma. A meglévő blokkok nem változnak (az említések a `block_id`-jükre hivatkoznak), kivéve
    ha az oldal azóta megváltozott (`blocks_built`): az M1-es stabil hash-e más (a crawl újra
    lekérte, és a nyers HTML változott), vagy a renderelt tartalma más (`content_hash`: a title,
    a H1 és a fő tartalom; a JavaScripttel betöltött tartalom a nyers hash változása nélkül is
    változhat). Ilyenkor az oldal említései, bizonyítékai és blokkjai törlődnek, és a blokkok
    újraépülnek. A `blocks_built` előtti blokkok a jelenlegi hash-ekkel kerülnek a táblába; a
    tartalom-hash nélküli (024 előtti) sor a jelenlegit kapja, újraépítés nélkül."""
    wanted = None if page_ids is None else set(page_ids)
    crawled = crawl.pages(con)
    hashes = {page.page_id: page.raw_html_hash for page in crawled}
    contents = {page.page_id: content_hash(page.title, page.h1, page.main_content)
                for page in crawled}
    unrecorded = [page_id for (page_id,) in con.execute(
        "SELECT DISTINCT page_id FROM blocks WHERE page_id NOT IN (SELECT page_id FROM "
        "blocks_built) ORDER BY page_id").fetchall() if page_id in hashes]
    if unrecorded:
        con.executemany("INSERT INTO blocks_built VALUES (?, ?, current_timestamp, ?)",
                        [(page_id, hashes[page_id], contents[page_id]) for page_id in unrecorded])
    legacy = [(contents[page_id], page_id) for (page_id,) in con.execute(
        "SELECT page_id FROM blocks_built WHERE content_hash IS NULL ORDER BY page_id"
    ).fetchall() if page_id in contents]
    if legacy:
        con.executemany("UPDATE blocks_built SET content_hash = ? WHERE page_id = ?", legacy)
    for page_id, built_hash, built_content in con.execute(
            "SELECT page_id, raw_html_hash, content_hash FROM blocks_built ORDER BY page_id"
    ).fetchall():
        changed = (hashes.get(page_id) is not None and built_hash != hashes[page_id]) \
            or (page_id in contents and built_content != contents[page_id])
        if changed and (wanted is None or page_id in wanted):
            drop_page_blocks(con, page_id)
    with_blocks = {page_id for (page_id,) in con.execute(
        "SELECT DISTINCT page_id FROM blocks").fetchall()}
    pages = [(page.page_id, *crawl.rendered(con, page.page_id)) for page in crawled
             if page.renderable and page.page_id not in with_blocks
             and (wanted is None or page.page_id in wanted)]
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
        con.execute("INSERT OR REPLACE INTO blocks_built VALUES (?, ?, current_timestamp, ?)",
                    [page_id, hashes[page_id], contents[page_id]])
    return len(pages)


def content_hash(title: str | None, h1: str | None, main_content: str | None) -> str:
    """Az oldal renderelt tartalmának hash-e: a title, a H1 és a fő tartalom (ahogy a crawl a
    renderelt DOM-ból kinyerte)."""
    text = "\x1f".join(part or "" for part in (title, h1, main_content))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def drop_page_blocks(con: duckdb.DuckDBPyConnection, page_id: int) -> None:
    """Egy oldal blokkjai és a rájuk épülő sorok (említések a forrásaikkal, bizonyítékok)
    törlődnek; a következő `build_blocks` újraépíti őket."""
    store.delete_mention_sources_in_drop_page_blocks(con, page_id)
    store.delete_page_entities_in_drop_page_blocks(con, page_id)
    store.delete_soft_checks_in_drop_page_blocks(con, page_id)
    con.execute("DELETE FROM blocks WHERE page_id = ?", [page_id])
    con.execute("DELETE FROM blocks_built WHERE page_id = ?", [page_id])


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
