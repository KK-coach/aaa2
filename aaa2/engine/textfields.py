"""Egy oldal szövegmezői a renderelt DOM-ból a tartalmi ellenőrzésekhez: a látható szöveg
területenként, a képek `alt` szövege és a linkek (eredeti cím, horgony), a területükkel.

A terület a `parse.link_position` besorolása (nav, body, footer, aside). Kimarad a `script`, a
`style`, a `noscript` és a `template`, valamint a kódblokkok (`pre`, `code`) szövege: a kódpélda
sablon-szintaxisa és megjegyzései nem az oldal tartalma. A link címe a href abszolút alakja
(a `<base href>` szerint), töredék nélkül, a százalékkódolás feloldva; a csak töredékből álló és
az üres href nem link."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urljoin

from selectolax.parser import HTMLParser

from aaa2.engine import parse

_SKIPPED = frozenset({"script", "style", "noscript", "template", "pre", "code"})


@dataclass(frozen=True)
class PageTexts:
    """`areas`: terület → a látható szöveg; `alts`: (terület, alt) képenként; `links`: (terület,
    cím, horgony) linkenként, a DOM sorrendjében."""

    areas: dict[str, str]
    alts: tuple[tuple[str, str], ...] = ()
    links: tuple[tuple[str, str, str], ...] = ()


def _skipped(node) -> bool:
    return node.tag in _SKIPPED or any(parent.tag in _SKIPPED
                                       for parent in parse._ancestors(node))


def page_texts(html: str, base_url: str) -> PageTexts:
    """Az oldal szövegmezői (lásd a modul leírását)."""
    tree = HTMLParser(html)
    base = parse._base_url(tree, base_url)
    parts: dict[str, list[str]] = {}
    cache: dict[int, str | None] = {}
    body = tree.body
    if body is not None:
        for node in body.traverse(include_text=True):
            if node.tag != "-text":
                continue
            text = node.text_content
            parent = node.parent
            if not text or not text.strip() or parent is None:
                continue
            if parent.mem_id not in cache:
                cache[parent.mem_id] = None if _skipped(parent) else parse.link_position(node)
            area = cache[parent.mem_id]
            if area is not None:
                parts.setdefault(area, []).append(text.strip())
    alts = tuple((parse.link_position(image), " ".join(alt.split()))
                 for image in tree.css("img[alt]")
                 if (alt := image.attributes.get("alt") or "").strip() and not _skipped(image))
    links = []
    for anchor in tree.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href or href.startswith("#") or _skipped(anchor):
            continue
        links.append((parse.link_position(anchor), unquote(urljoin(base, href).split("#", 1)[0]),
                      parse.anchor_text(anchor) or ""))
    return PageTexts(areas={area: " ".join(texts) for area, texts in parts.items()},
                     alts=alts, links=tuple(links))
