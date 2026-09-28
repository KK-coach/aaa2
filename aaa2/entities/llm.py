"""Az oldalszintű (v1) LLM-prompt és a hozzá tartozó ellenőrzések, a mérőeszközöknek
(`tests/acceptance/llm_compare.py`, `gold_compare.py`); a típusdefiníciók és a site-leíró
mondat a blokkos körnek (`entities.extract`) is.

- `PROMPT` / `page_input`: a title, a headingek és a main content (`MAX_INPUT_CHARS`-nál
  levágva), címke nélkül, üres sorral elválasztva; az első bekezdés a site-leíró mondat
  (`site_line`: a domain és a kezdőoldal title-je).
- `check_evidence`: az evidence (a szerkezeti címke nélkül, `strip_label`) szó szerint
  (whitespace-normalizálva, kis-nagybetű-érzéketlenül) benne van-e a forrásokban; ha nincs,
  fabrikált. `name_in_evidence`: csak mérés.
"""
from __future__ import annotations

import re

import duckdb

from aaa2.entities.rules import alias_key
from aaa2.llm.schemas import ExtractedEntity

MAX_INPUT_CHARS = 80_000

TYPE_DEFINITIONS = {
    "brand": "a brand or trade name that is not itself a company",
    "product": ("goods: a product or product line, a food, a dish, a drink, a wine, an "
                "ingredient"),
    "service": "a service offered to customers",
    "work": "a creative work: article, book, report, course, case study, publication",
    "event": "an event held at a given time: conference, festival, workshop, webinar",
    "person": "a named individual",
    "org": "a company, institution, association or team; a company is org, not brand",
    "place": "a geographic place or venue: country, city, district, street, building",
    "tech": ("software, a platform, a digital tool or service tool, a programming library or "
             "framework, a technical standard"),
    "concept": "an idea, method, discipline or topic",
}
PROMPT = (
    "List every entity the web page below is about or mentions: named things and concepts. "
    "Use only the page text. The first paragraph of the input names the website the page "
    "belongs to; it is not page text.\n"
    "Return:\n"
    "- primary_entity: the entity the page is mainly about, as written on the page (it may be "
    "a common noun); list it among the entities too;\n"
    "- entities, each with:\n"
    "  - name: the canonical name as written on the page, in its full form, not translated;\n"
    "  - type: one of the types defined below;\n"
    "  - description: what this entity is in this text, in one short sentence;\n"
    "  - evidence: a quote copied verbatim from the page that contains the name;\n"
    "  - context: the paragraph or list item that contains the evidence, copied from the page.\n"
    "A keyword or search phrase is not an entity. The titles of this site's own pages are not "
    "works. Return nothing that is not on the page.\n\n"
    "Types:\n" + "\n".join(f"- {kind}: {text}" for kind, text in TYPE_DEFINITIONS.items())
)


def page_input(title: str | None, headings: list[tuple[int, str]],
               main_content: str | None, site: str | None = None) -> tuple[str, bool]:
    """A hívás bemenete: a site-ról szóló mondat (`site_line`), a title, a headingek és a main
    content, címke nélkül, üres sorral elválasztva (a modell ne lásson szerkezeti tokent, amit az
    idézetbe másolhat); és hogy le kellett-e vágni a main contentet."""
    text = main_content or ""
    parts = [*([site] if site else []), title or "",
             *(heading for _, heading in headings if heading), text[:MAX_INPUT_CHARS]]
    return "\n\n".join(parts), len(text) > MAX_INPUT_CHARS


def site_line(con: duckdb.DuckDBPyConnection) -> str | None:
    """Egy mondat a site-ról: a domain és a kezdőoldal title-je (`site.home_urls` első eleme; ha
    az oszlop NULL, a seed URL), ha van."""
    row = con.execute("SELECT domain, home_urls, seed_url FROM site").fetchone()
    if row is None:
        return None
    domain, homes, seed = row
    home = seed if homes is None else (homes[0] if homes else None)
    title = con.execute("SELECT title FROM pages WHERE url = ?", [home]).fetchone() if home \
        else None
    title = " ".join((title[0] or "").split()) if title else ""
    return (f"This page belongs to the website {domain}, whose home page is titled "
            f"“{title}”." if title else f"This page belongs to the website {domain}.")


# A korábbi bemenet-formátum szerkezeti címkéi (a felvett kimenetekben előfordulnak).
LABEL_PREFIX = re.compile(r"^\s*(?:(?:TITLE|HEADINGS|TEXT|H[1-6])\s*:\s*)+", re.IGNORECASE)
LABEL_SUFFIX = re.compile(r"(?:\s+(?:TITLE|HEADINGS|TEXT|H[1-6])\s*:)+\s*$", re.IGNORECASE)


def strip_label(text: str) -> str:
    """A szerkezeti címke (`TITLE:`, `H1:`…`H6:`, `HEADINGS:`, `TEXT:`) nélkül az elejéről és a
    végéről; a közepén álló címke két forrássor összefűzése, az marad."""
    return LABEL_SUFFIX.sub("", LABEL_PREFIX.sub("", text or ""))


def normalize_text(text: str | None) -> str:
    """Whitespace egy szóközzé, kisbetűsítve."""
    return " ".join((text or "").split()).casefold()


def check_evidence(entity: ExtractedEntity, sources: list[str]) -> str | None:
    """`fabricated`, ha az evidence (a címke nélkül) nincs benne szó szerint egyik (már
    normalizált) forrásban sem; különben None. Hosszkorlát nincs."""
    evidence = normalize_text(strip_label(entity.evidence))
    if not evidence or not any(evidence in source for source in sources):
        return "fabricated"
    return None


def name_in_evidence(entity: ExtractedEntity) -> bool:
    """Az evidence tartalmazza-e a nevet (whitespace, kis-nagybetű és ékezet nélkül); csak mérés,
    nem szűrő."""
    return alias_key(entity.name) in alias_key(strip_label(entity.evidence))
