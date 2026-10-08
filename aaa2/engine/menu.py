"""Menük a renderelt DOM-ból: melyik menüterületen melyik menüpont áll, és melyik alatt.

Három terület, mindegyik külön fa:

- **fejléc** (`header`): a `header`, a `nav`, a `role="navigation"` és a `role="banner"` elem,
  ha nem a láblécben, nem az oldalsávban és nem a fő tartalomban áll;
- **lábléc** (`footer`): a `footer` és a `role="contentinfo"` elem;
- **oldalsáv** (`sidebar`): az `aside` és a `role="complementary"` elem, valamint a „sidebar”
  vagy „aside” osztály- / azonosító-szavú elem (a `body` és a `html` kivételével), a láblécen
  kívül (a webshopok kategóriafája és a dokumentációs site-ok menüje jellemzően itt áll).

Nem menü, ezért egyik területbe sem kerül (a területen belül az egész részfa kimarad):

- a **morzsa**: az elem osztálya, azonosítója vagy `aria-label`-je a „breadcrumb” vagy a
  „morzsa” szót tartalmazza, vagy az `itemtype`-ja `BreadcrumbList`;
- a **lapozó**: az elem osztályának vagy azonosítójának egy szava „pagination”, „page-numbers”
  vagy „pager” kezdetű, az `aria-label`-je „pagination”-t tartalmaz, vagy az elem egy `nav`,
  amelyben `rel="next"` / `rel="prev"` link áll; a `rel="next"` / `rel="prev"` link maga sem
  menüpont;
- a **tartalomkártya**: a menüterületen belüli `article` elem (hírajánló, termékkártya) linkjei;
- a **fő tartalmon belüli navigáció**: a `main`, a `role="main"` vagy az `article` elemen
  belüli `header`, `nav` és `role="navigation"` (alnavigáció, szűrő, a cikk fejléce) nem
  fejléc-menü.

Szülő–gyerek viszony ott van, ahol egy elemnek pontosan két linket hordozó gyereke van: az
egyik maga a menüpont linkje (egyetlen link), a másik az almenü (benne a gyerek-menüpontok
linkjei), és a kettő más-más elem (két egyforma testvér, pl. két `li` vagy két `a`, egy szint
két menüpontja, nem szülő és gyerek). Ez a beágyazott listát (`li > a + ul`) és a listák nélküli
lenyílót (`div > a + div`) egyformán leírja. A gyerek a legközelebbi ilyen szülőhöz tartozik
(közvetlen szint). Kimarad: az a lenyíló, ahol a menüpont és az almenü azonos elemű testvér
(`div > div + div`).

Kiegészítő jel a címek hierarchiája: ha egy almenü egy szintjén álló két menüpont közül az egyik
címe a másiké alatt áll (`/seo/technikai/` a `/seo/` alatt), akkor a mélyebb cím szülője az a
menüpont, nem az almenü szülője (a lapos almenü, ahol az alszintet csak a megjelenés jelzi).
Több ilyen testvér közül a leghosszabb című a szülő. A felső szinten ez a szabály nem fut, és
kezdőoldal nem lehet így szülő (a `/hu/` minden magyar menüpont címének előtagja).

A kezdőoldalra mutató link (logó, nyelvi kezdőoldal), a logóként jelölt link és a szöveg
nélküli link (se szövege, se `aria-label`-je, `title`-je vagy képének `alt`-ja) nem almenü
szülője. A szülő lehet link
nélküli címke is (`href="#"` vagy `javascript:`): ilyenkor a szülő a címke szövege, oldal
nélkül. A `tel:`, `mailto:`, `javascript:` és a puszta `#` link nem menüpont. Ha ugyanaz a cím
egy területen felső szinten és egy szülő alatt is áll (asztali menü és lapos mobil másolat), a
szülő alatti előfordulás számít; ugyanaz a cím területenként egyszer szerepel.

A link jelölése (`marker`): `logo`, ha a link vagy valamelyik közeli őse „logo” vagy „brand”
osztályú / azonosítójú, vagy a linknek nincs szövege és képet tartalmaz; `home_icon`, ha a
linknek nincs szövege, nem logó, és az osztálya, `aria-label`-je vagy `title`-je a kezdőoldalt
nevezi meg („home”, „főoldal”, „kezdőlap”)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

AREAS = ("header", "footer", "sidebar")
_ROOTS = {"footer": "footer, [role=contentinfo]",
          "sidebar": "aside, [role=complementary]",
          "header": "header, nav, [role=navigation], [role=banner]"}
_CONTENT = frozenset({"main", "article"})
_CRUMB_WORDS = ("breadcrumb", "morzsa")
_PAGER_STARTS = ("pagination", "page-numbers", "pager")
_LOGO_WORDS = ("logo", "brand")
_HOME_WORDS = ("home", "fooldal", "főoldal", "kezdolap", "kezdőlap")
_IMAGES = "img, svg, picture"
_LABEL = "(címke: {})"
_SIDEBAR_TOKENS = frozenset({"sidebar", "aside"})
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class MenuEntry:
    """Egy menüpont egy oldal egy menüterületén. `url`: a cél abszolút címe töredék nélkül
    (None: link nélküli szülő-címke); `parent`: a szülő menüpont címe vagy címkéje (None: felső
    szint); `parent_is_label`: a szülő link nélküli címke; `marker`: `logo` / `home_icon` /
    None."""

    area: str
    url: str | None
    anchor: str
    parent: str | None = None
    parent_is_label: bool = False
    marker: str | None = None


def label_key(text: str) -> str:
    """A link nélküli szülő-címke azonosítója a menüfában."""
    return _LABEL.format(text)


def _elements(node: Node) -> list[Node]:
    found = []
    child = node.child
    while child is not None:
        if child.tag and not child.tag.startswith("-"):
            found.append(child)
        child = child.next
    return found


def _links(node: Node) -> list[Node]:
    if node.tag == "a":
        return [node] if node.attributes.get("href") else []
    return node.css("a[href]")


def _parent_and_submenu(carrying: list[tuple[Node, list[Node]]]) -> bool:
    """A két linket hordozó gyerek menüpont és almenü-e: az első egyetlen linket hordoz, a
    második más elem."""
    return len(carrying) == 2 and len(carrying[0][1]) == 1 \
        and carrying[0][0].tag != carrying[1][0].tag


def _ancestors(node: Node) -> list[Node]:
    found = []
    parent = node.parent
    while parent is not None:
        found.append(parent)
        parent = parent.parent
    return found


def _words(node: Node, *names: str) -> str:
    return " ".join((node.attributes.get(name) or "") for name in names).lower()


def is_breadcrumb(node: Node) -> bool:
    """Morzsa-elem-e (lásd a modul leírását)."""
    return any(word in _words(node, "class", "id", "aria-label") for word in _CRUMB_WORDS) \
        or "breadcrumblist" in _words(node, "itemtype")


def _is_pager(node: Node) -> bool:
    tokens = _words(node, "class", "id").split()
    if any(token.startswith(_PAGER_STARTS) for token in tokens) \
            or "pagination" in _words(node, "aria-label"):
        return True
    return node.tag == "nav" and any(_rel(link) for link in node.css("a[rel]"))


def _rel(link: Node) -> bool:
    return bool({"next", "prev"} & set((link.attributes.get("rel") or "").lower().split()))


def _not_menu(node: Node) -> bool:
    return is_breadcrumb(node) or _is_pager(node) or node.tag == "article"


def _absolute(base_url: str, raw: str) -> str:
    """A link abszolút címe töredék és alapértelmezett port nélkül."""
    url = urljoin(base_url, raw).split("#", 1)[0]
    parts = urlsplit(url)
    default = {"https": ":443", "http": ":80"}.get(parts.scheme)
    if default and parts.netloc.endswith(default):
        url = parts._replace(netloc=parts.netloc[:-len(default)]).geturl()
    return url


def _in_content(node: Node) -> bool:
    return any(parent.tag in _CONTENT or (parent.attributes.get("role") or "") == "main"
               for parent in _ancestors(node))


def url_key(url: str) -> str:
    """A cím a menüpontok összevetéséhez: töredék és záró perjel nélkül."""
    parts = urlsplit(url.split("#", 1)[0])
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/') or '/'}" \
        + (f"?{parts.query}" if parts.query else "")


def _sibling_above(url: str, level: set[str]) -> str | None:
    """Az a menüpont ugyanarról a szintről, amelynek a címe alatt a `url` áll (ugyanaz a host, az
    útvonala a `url` útvonalának valódi, `/`-határra eső előtagja); több közül a leghosszabb."""
    own = urlsplit(url)
    own_path = own.path if own.path.endswith("/") else own.path + "/"
    above = []
    for other in level:
        parts = urlsplit(other)
        folder = parts.path if parts.path.endswith("/") else parts.path + "/"
        if other != url and parts.netloc == own.netloc and folder != "/" \
                and own_path.startswith(folder) and own_path != folder:
            above.append((len(folder), other))
    return max(above)[1] if above else None


def _anchor(link: Node) -> str:
    text = " ".join(link.text(strip=True, separator=" ").split())
    if text:
        return text
    for name in ("aria-label", "title"):
        value = " ".join((link.attributes.get(name) or "").split())
        if value:
            return value
    image = link.css_first("img[alt]")
    return " ".join((image.attributes.get("alt") or "").split()) if image is not None else ""


def _marker(link: Node) -> str | None:
    text = link.text(strip=True)
    named_home = any(word in _words(link, "class", "aria-label", "title")
                     for word in _HOME_WORDS)
    chain = [link, *_ancestors(link)[:3]]
    if any(word in _words(node, "class", "id") for node in chain for word in _LOGO_WORDS) \
            or (not text and not named_home and link.css_first(_IMAGES) is not None):
        return "logo"
    return "home_icon" if not text and named_home else None


def _area_roots(tree: HTMLParser) -> list[tuple[str, Node]]:
    """A menüterületek gyökerei egymásba ágyazás nélkül: a lábléc és az oldalsáv a benne álló
    `nav`-ot is viszi, a fejléc a többi."""
    taken: set[int] = set()
    found = []
    for area in ("footer", "sidebar", "header"):
        nodes = tree.css(_ROOTS[area])
        if area == "sidebar":
            nodes += [node for node in tree.css("[class], [id]")
                      if node.tag not in ("body", "html", "aside")
                      and _SIDEBAR_TOKENS & set(_TOKEN_SPLIT.split(_words(node, "class", "id")))]
        for node in nodes:
            above = _ancestors(node)
            if node.mem_id in taken or any(parent.mem_id in taken for parent in above):
                continue
            if area == "sidebar" and any(parent.tag == "footer" for parent in above):
                continue
            taken.add(node.mem_id)              # a kizárt elem részfája sem lesz menü
            if _not_menu(node) or (area == "header" and _in_content(node)):
                continue
            found.append((area, node))
    return found


def menu_entries(html: str, base_url: str, homes: frozenset[str] | set[str] = frozenset()
                 ) -> list[MenuEntry]:
    """Egy oldal menüpontjai területenként (lásd a modul leírását), területen belül a dokumentum
    sorrendjében. `homes`: a site kezdőoldalainak címe (a kezdőoldal nem almenü szülője);
    `base_url`: a relatív címek alapja, ha a dokumentumnak nincs `<base href>`-je."""
    tree = HTMLParser(html)
    base = tree.css_first("base[href]")
    href = (base.attributes.get("href") or "").strip() if base is not None else ""
    base_url = urljoin(base_url, href) if href else base_url
    home_keys = {url_key(url) for url in homes}
    found: dict[str, dict[str, dict]] = {area: {} for area in AREAS}

    def emit(area: str, link: Node, parent: str | None, as_parent: bool = False) -> str | None:
        raw = (link.attributes.get("href") or "").strip()
        url = _absolute(base_url, raw) if raw else ""
        real = bool(raw) and not raw.startswith(("#", "javascript:")) \
            and urlsplit(url).scheme in ("http", "https")
        text = _anchor(link)
        if _rel(link):
            return None
        if real:
            key = url_key(url)
            if not as_parent and key == parent:
                return None
        elif as_parent and text:
            key, url = label_key(text), None
        else:
            return None
        items = found[area]
        old = items.get(key)
        if old is None:
            items[key] = {"url": url, "anchor": text, "parent": parent,
                          "marker": _marker(link) if real else None}
        elif old["parent"] is None and parent is not None and parent != key:
            old["parent"] = parent
            old["anchor"] = old["anchor"] or text
        return key

    def walk(area: str, holder: Node, parent: str | None) -> None:
        carrying = []
        for child in _elements(holder):
            if _not_menu(child):
                continue
            links = _links(child)
            if links:
                carrying.append((child, links))
        if _parent_and_submenu(carrying):
            head = carrying[0][1][0]
            raw = (head.attributes.get("href") or "").strip()
            target = None
            if raw and not raw.startswith("#"):
                target = url_key(_absolute(base_url, raw))
            if (target is None or target not in home_keys) and _anchor(head) \
                    and _marker(head) != "logo":
                own = emit(area, head, parent, as_parent=True)
                step(area, carrying[1][0], own if own is not None else parent)
                return
        for child, _ in carrying:
            step(area, child, parent)

    def step(area: str, child: Node, parent: str | None) -> None:
        if child.tag == "a":
            emit(area, child, parent)
        else:
            walk(area, child, parent)

    for area, root in _area_roots(tree):
        step(area, root, None)
    entries = []
    for area in AREAS:
        items = found[area]
        groups: dict[str, set[str]] = {}
        for key, item in items.items():
            if item["url"] and item["parent"] is not None:
                groups.setdefault(item["parent"], set()).add(key)
        for group in groups.values():
            for key in group:
                above = _sibling_above(key, group - home_keys)
                if above:
                    items[key]["parent"] = above
        for item in items.values():
            parent = item["parent"] if item["parent"] in items else None
            entries.append(MenuEntry(
                area=area, url=item["url"], anchor=item["anchor"],
                parent=(items[parent]["url"] or parent) if parent is not None else None,
                parent_is_label=parent is not None and items[parent]["url"] is None,
                marker=item["marker"]))
    return entries


def visible_breadcrumb(html: str, base_url: str) -> list[tuple[str | None, str]] | None:
    """A látható morzsa egy oldal DOM-jából: az első morzsa-elem (`is_breadcrumb`), amelyben
    link áll; a linkjei sorrendben (cím, szöveg), a végén a link nélküli záró szöveg (az
    aktuális oldal, cím nélkül). None, ha nincs ilyen elem."""
    lowered = html.lower()
    if not any(word in lowered for word in _CRUMB_WORDS):
        return None
    tree = HTMLParser(html)
    base = tree.css_first("base[href]")
    href = (base.attributes.get("href") or "").strip() if base is not None else ""
    base_url = urljoin(base_url, href) if href else base_url
    for node in tree.css("[class], [id], [aria-label], [itemtype]"):
        if not is_breadcrumb(node):
            continue
        links = [link for link in node.css("a[href]")
                 if urlsplit(_absolute(base_url, link.attributes.get("href") or "")).scheme
                 in ("http", "https")]
        if not links:
            continue
        trail: list[tuple[str | None, str]] = [
            (_absolute(base_url, link.attributes.get("href") or ""), _anchor(link))
            for link in links]
        whole = " ".join(node.text(strip=True, separator=" ").split())
        last = " ".join(links[-1].text(strip=True, separator=" ").split())
        tail = whole.rsplit(last, 1)[-1].strip(" /›»>|·-") if last else ""
        if tail:
            trail.append((None, tail))
        return trail
    return None
