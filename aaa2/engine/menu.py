"""Menühierarchia a renderelt DOM-ból: melyik menüpont alatt melyik menüpont áll.

A menüterület a `header`, a `nav` és a `role="navigation"` elem, a láblécen kívül. Szülő–gyerek
viszony ott van, ahol egy elemnek pontosan két linket hordozó gyereke van: az egyik maga a
menüpont linkje (egyetlen link), a másik az almenü (benne a gyerek-menüpontok linkjei), és a
kettő más-más elem (két egyforma testvér, pl. két `li` vagy két `a`, egy szint két menüpontja,
nem szülő és gyerek). Ez a beágyazott listát (`li > a + ul`) és a listák nélküli lenyílót
(`div > a + div`) egyformán leírja; a fejléc többi része (logó, nyelvváltó, gombok) nem válik
szülővé, mert ott egy elemnek kettőnél több linket hordozó gyereke van. A gyerek a
legközelebbi ilyen szülőhöz tartozik (közvetlen szint): az unoka a gyerek alatt áll, nem a
nagyszülő alatt. Kimarad: az a lenyíló, ahol a menüpont és az almenü azonos elemű testvér
(`div > div + div`).

A link nélküli lenyíló címke (gomb, `span`) alatti menüpontoknak nincs szülő oldaluk; ezek
kimaradnak. Ugyanaz a (szülő, gyerek) pár egyszer szerepel: a mobilmenü másolata nem dupláz."""
from __future__ import annotations

from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

MENU_AREAS = "header, nav, [role=navigation]"


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


def _in_footer(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.tag == "footer":
            return True
        parent = parent.parent
    return False


def menu_pairs(html: str, base_url: str) -> list[tuple[str, str, str]]:
    """A menü szülő–gyerek párjai egy oldal DOM-jából: (a szülő menüpont címe, a gyerek
    menüpont címe, a gyerek horgonyszövege), abszolút címekkel, a DOM sorrendjében, ismétlés
    nélkül (lásd a modul leírását)."""
    tree = HTMLParser(html)
    found: dict[tuple[str, str], str] = {}
    seen: set[int] = set()
    for area in tree.css(MENU_AREAS):
        if _in_footer(area):
            continue
        for holder in [area, *area.css("*")]:
            if holder.mem_id in seen:
                continue
            seen.add(holder.mem_id)
            carrying = [(child, _links(child)) for child in _elements(holder)]
            carrying = [(child, links) for child, links in carrying if links]
            if not _parent_and_submenu(carrying):
                continue
            (_, first_links), (second, _) = carrying
            parent = urljoin(base_url, first_links[0].attributes.get("href") or "")
            for link in _direct_items(second):
                child = urljoin(base_url, link.attributes.get("href") or "")
                if child != parent:
                    found.setdefault((parent, child), link.text(strip=True))
    return [(parent, child, anchor) for (parent, child), anchor in found.items()]


def _direct_items(submenu: Node) -> list[Node]:
    """Az almenü közvetlen szintjének linkjei: amelyek nem egy mélyebb almenüben állnak (a
    mélyebb szint a saját szülőjéhez tartozik)."""
    deeper: set[int] = set()
    for holder in submenu.css("*"):
        carrying = [(child, _links(child)) for child in _elements(holder)]
        carrying = [(child, links) for child, links in carrying if links]
        if _parent_and_submenu(carrying):
            deeper.update(link.mem_id for link in carrying[1][1])
    return [link for link in _links(submenu) if link.mem_id not in deeper]
