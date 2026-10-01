"""A típusdefiníciók és a site-leíró mondat a blokkos kinyerésnek (`entities.blocks`,
`entities.extract`), és a szövegnormalizálás.

- `TYPE_DEFINITIONS`: típusonként egy mondat; a kinyerő prompt része (`blocks.BLOCK_PROMPT`).
- `site_line`: a domain és a kezdőoldal title-je, a kinyerés bemenetének első bekezdése.
- `normalize_text`: whitespace egy szóközzé, kisbetűsítve.

A szövegük a bemenet-hash része (`extract.input_fingerprint`): változásuk minden oldalt újra
kinyeretne.
"""
from __future__ import annotations

import duckdb

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


def site_line(con: duckdb.DuckDBPyConnection) -> str | None:
    """Egy mondat a site-ról: a domain és a kezdőoldal title-je (`site.home_urls` első eleme; ha
    az oszlop NULL, a seed URL), ha van."""
    row = con.execute("SELECT domain, home_urls, seed_url FROM site ORDER BY ALL").fetchone()
    if row is None:
        return None
    domain, homes, seed = row
    home = seed if homes is None else (homes[0] if homes else None)
    title = con.execute("SELECT title FROM pages WHERE url = ? ORDER BY ALL", [home]).fetchone() if home \
        else None
    title = " ".join((title[0] or "").split()) if title else ""
    return (f"This page belongs to the website {domain}, whose home page is titled "
            f"“{title}”." if title else f"This page belongs to the website {domain}.")


def normalize_text(text: str | None) -> str:
    """Whitespace egy szóközzé, kisbetűsítve."""
    return " ".join((text or "").split()).casefold()
