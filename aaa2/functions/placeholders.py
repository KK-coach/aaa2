"""Kitöltetlen sablon-tartalom keresése egy oldal mezőiben, LLM nélkül.

Mezők: a látható szöveg területenként (a kódblokkok nélkül, `engine.textfields`), a title, a
meta description, a H1, a képek `alt`-ja, a linkek címe és horgonya, és az URL-útvonal.

Minták (`PATTERNS`):

- `placeholder`: szögletes zárójeles helykitöltő – nagybetűs, több szavas vagy aláhúzásos token
  (`[IDE_JÖN_A_LINK]`, `[COMPANY NAME]`), vagy `[PLACEHOLDER]`;
- `lorem_ipsum`: latin töltőszöveg. Folyó szövegben `LOREM_WINDOW` egymást követő szóból
  legalább `LOREM_MIN_WORDS` erős töltőszó, köztük legalább `LOREM_MIN_DISTINCT` különböző.
  Rövid mezőben (title, H1, meta description, alt, horgony) és az URL egy szakaszában
  legalább `SHORT_MIN_DISTINCT` különböző töltőszó, köztük legalább egy erős, és a szavak
  legalább `SHORT_MIN_SHARE` része töltőszó. Az élő nyelvekkel ütköző rövid szavak (`WEAK`: in,
  est, id, ut, et, qui, ea, …) csak erős töltőszó mellett számítanak; egyetlen szó sehol nem
  elég;
- `cms_default`: alapértelmezett CMS-tartalom („Hello world!”, „Sample Page”, „Mintaoldal”,
  „Just another WordPress site”);
- `text_slot`: a szöveg helyét jelölő kifejezés („ide jön”, „szöveg helye”, „your text here”).

A `{{…}}` sablon-szintaxis és a TODO-jellegű jelölés nem minta: a mért készleteken csak
kódpéldában fordult elő."""
from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import unquote, urlsplit

from aaa2.engine.textfields import PageTexts

_UPPER = "A-ZÁÉÍÓÖŐÚÜŰ"
PATTERNS = {"placeholder": "helykitöltő szögletes zárójelben", "lorem_ipsum": "lorem ipsum",
            "cms_default": "alapértelmezett CMS-tartalom",
            "text_slot": "a szöveg helyét jelölő kifejezés"}
_REGEX = {
    "placeholder": re.compile(
        rf"\[(?:[{_UPPER}][{_UPPER}0-9]*(?:[_ ][{_UPPER}0-9]+)+|PLACEHOLDER)\]"),
    "cms_default": re.compile(
        r"hello world\s*!|\bsample page\b|\bmintaoldal\b|just another wordpress site",
        re.IGNORECASE),
    "text_slot": re.compile(r"\bide jön\b|\bszöveg helye\b|\byour text here\b", re.IGNORECASE),
}
# erős töltőszavak: a lorem ipsum jellegzetes latin szavai
STRONG = frozenset([
    "lorem", "ipsum", "dolor", "amet", "consectetur", "adipiscing", "adipisicing", "eiusmod",
    "incididunt", "dolore", "aliqua", "veniam", "quis", "nostrud", "nostrum", "exercitation",
    "exercitationem", "ullamco", "laboris", "aliquip", "consequat", "aute", "irure",
    "reprehenderit", "voluptate", "cillum", "fugiat", "pariatur", "excepteur", "occaecat",
    "cupidatat", "proident", "officia", "deserunt", "mollit", "laborum", "perspiciatis",
    "voluptatem", "accusantium", "doloremque", "laudantium", "porro", "quisquam", "culpa",
    "duis", "minim", "nisi", "velit", "neque", "finibus", "bonorum",
])
# élő nyelvekkel ütköző szavak: csak erős töltőszó mellett számítanak
WEAK = frozenset([
    "in", "est", "id", "ut", "et", "qui", "ea", "sed", "sit", "ad", "non", "unde", "elit",
    "magna", "esse", "sint", "anim", "sunt", "nulla", "labore", "tempor", "commodo", "error",
    "natus", "iste", "omnis", "enim",
])
_WORD = re.compile(r"[a-zA-ZÀ-ž]+")
LOREM_WINDOW, LOREM_MIN_WORDS, LOREM_MIN_DISTINCT = 12, 5, 3
SHORT_MIN_DISTINCT, SHORT_MIN_SHARE = 2, 0.5
SNIPPET_CHARS = 120
_CONTEXT = 40
FIELD_LABELS = {"text": "látható szöveg", "title": "title", "description": "meta description",
                "h1": "H1", "alt": "img alt", "href": "link href", "anchor": "link horgony",
                "url": "URL-útvonal"}


def _snippet(text: str) -> str:
    return " ".join(text.split())[:SNIPPET_CHARS]


def lorem_in_text(text: str) -> str | None:
    """Folyó szöveg: az első ablak, amelyben megvan a töltőszavak küszöbe; különben None."""
    words = _WORD.findall(text)
    low = [word.lower() for word in words]
    for start in range(max(1, len(low) - LOREM_WINDOW + 1)):
        hits = [word for word in low[start:start + LOREM_WINDOW] if word in STRONG]
        if len(hits) >= LOREM_MIN_WORDS and len(set(hits)) >= LOREM_MIN_DISTINCT:
            return " ".join(words[start:start + LOREM_WINDOW])
    return None


def lorem_in_short(text: str) -> bool:
    """Rövid mező vagy URL-szakasz: megvan-e a töltőszavak küszöbe."""
    low = [word.lower() for word in _WORD.findall(text)]
    strong = {word for word in low if word in STRONG}
    if not strong:
        return False
    fillers = [word for word in low if word in STRONG or word in WEAK]
    return len(set(fillers)) >= SHORT_MIN_DISTINCT and len(fillers) >= SHORT_MIN_SHARE * len(low)


def _lorem_in_path(path: str) -> str | None:
    """Az útvonal első szakasza, amelyben megvan a töltőszavak küszöbe."""
    for segment in unquote(path).split("/"):
        if segment and lorem_in_short(re.sub(r"[-_+.,]", " ", segment)):
            return segment
    return None


def _regex_hits(text: str, kinds: Iterable[str] = ("placeholder", "cms_default", "text_slot")
                ) -> list[tuple[str, str]]:
    found = []
    for kind in kinds:
        for match in _REGEX[kind].finditer(text):
            start, end = max(0, match.start() - _CONTEXT), match.end() + _CONTEXT
            found.append((kind, _snippet(text[start:end])))
    return found


def page_hits(url: str, title: str | None, description: str | None, h1: str | None,
              texts: PageTexts | None) -> list[dict]:
    """Az oldal találatai: {`pattern`, `field`, `area`, `snippet`} (a részlet legfeljebb
    `SNIPPET_CHARS` karakter), ismétlés nélkül, a mezők rögzített sorrendjében. `area`: a link
    pozíciója (nav, body, footer, aside) a látható szövegnél, az alt-nál és a linknél; None a
    fej-mezőknél és az URL-nél."""
    found: list[tuple[str, str, str | None, str]] = []

    def short(field: str, area: str | None, text: str | None) -> None:
        if not text:
            return
        found.extend((kind, field, area, snippet) for kind, snippet in _regex_hits(text))
        if lorem_in_short(text):
            found.append(("lorem_ipsum", field, area, _snippet(text)))

    short("title", None, title)
    short("description", None, description)
    short("h1", None, h1)
    parts = urlsplit(url)
    path = unquote(parts.path + (f"?{parts.query}" if parts.query else ""))
    found.extend((kind, "url", None, _snippet(path))
                 for kind, _ in _regex_hits(path, ("placeholder",)))
    segment = _lorem_in_path(parts.path)
    if segment:
        found.append(("lorem_ipsum", "url", None, _snippet(segment)))
    if texts is not None:
        for area, text in texts.areas.items():
            found.extend((kind, "text", area, snippet) for kind, snippet in _regex_hits(text))
            window = lorem_in_text(text)
            if window:
                found.append(("lorem_ipsum", "text", area, _snippet(window)))
        for area, alt in texts.alts:
            short("alt", area, alt)
        for area, href, anchor in texts.links:
            found.extend((kind, "href", area, _snippet(href))
                         for kind, _ in _regex_hits(href, ("placeholder",)))
            if _lorem_in_path(urlsplit(href).path):
                found.append(("lorem_ipsum", "href", area, _snippet(href)))
            short("anchor", area, anchor)
    seen: set[tuple] = set()
    hits = []
    for item in found:
        if item not in seen:
            seen.add(item)
            hits.append(dict(zip(("pattern", "field", "area", "snippet"), item, strict=True)))
    return hits
