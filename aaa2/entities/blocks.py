"""Blokkformátumú LLM-kinyerés (prompt v2): a bemenet egy site-leíró sor, utána az oldal blokkjai
a dokumentum sorrendjében (a headingek is saját blokként), azonosítóval; a kimenet
említésenként blokk-azonosító, a szöveg szerinti alak, a kanonikus név, típus, altípus (zárt
lista) és egy rövid leírás, továbbá a `primary_entities` lista.

- A blokkok most a mesterséges tesztoldalak JSON-jából jönnek (id, kind, heading_path, text;
  táblázatnál cellák oszlopfejléccel); a valódi oldalak blokk-parsere külön feladat. A
  `heading_path` a blokkban marad (a kód használja), a bemenetbe nem kerül.
- A prompt állandó; ismert entitás (a site vagy más kör találata) nem kerül bele, és a példái
  nem a tesztoldalakról valók.
- Ellenőrzés: a `surface_form` (whitespace-normalizálva, kis-nagybetű-érzéketlenül) szóhatárral
  szerepel-e a megadott blokk szövegében; ami nem, az kitalált (`check_surface`).
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from aaa2.entities.llm import TYPE_DEFINITIONS, normalize_text
from aaa2.llm.schemas import SUBTYPE_GLOSSARY, BlockEntity

_BLOCK_HEADER = {
    False: "each starts with its id in square brackets on its own line, followed by the block "
           "text",
    True: "each starts with its id and its kind in square brackets on its own line (for "
          "example [b3 heading], [b7 code], [b9 cta]), followed by the block text; block_id is "
          "the id alone",
}


def _subtype_lines() -> str:
    lines = []
    for kind, values in SUBTYPE_GLOSSARY.items():
        explained = "; ".join(f"{value} ({text})" for value, text in values.items())
        lines.append(f"- {kind}: {explained or 'none (null)'}")
    return "\n".join(lines)


def block_prompt(with_kind: bool = False) -> str:
    """A blokkos prompt; `with_kind`: a bemenet a blokktípust is megadja az azonosító mellett."""
    return (
        "Extract every entity the page is about or mentions: named things, offered products or "
        "services, and definable professional concepts. A single mention is enough. Generic "
        "nouns on their own are not entities.\n"
        "The input starts with one line describing the website; it is not page text. Then come "
        "the page blocks in document order, headings included: " + _BLOCK_HEADER[with_kind]
        + ".\n\n"
        "Rules:\n"
        "- List each entity separately, under its shortest full name. In a possessive "
        "construction list both the owner and the thing owned, if each is an entity: "
        "“a Stripe fizetési oldala” → Stripe.\n"
        "- A general noun does not stick to a name: “Shopify-integráció” → "
        "Shopify; “Parmigiano Reggiano-krém” → Parmigiano Reggiano.\n"
        "- A brand inside an official product name does not yield a separate entity: "
        "“Adobe Photoshop” is one entity.\n"
        "- surface_form is the full word form exactly as it stands in the block, with its "
        "suffixes (“Shopify-integrációval”).\n"
        "- Button, menu and call-to-action text on its own is not an entity; a name inside it "
        "is.\n"
        "- Identifiers declared in example code (classes, variables, selectors) are not "
        "entities; packages, libraries and APIs that the code imports or uses are.\n"
        "- For a product named after its origin, the product is the entity: “pármai "
        "sonka” is a product; the place is an entity only where the text refers to the "
        "place itself.\n"
        "- The titles of this site's own pages are not works.\n"
        "- Return nothing that is not on the page.\n\n"
        "Return:\n"
        "- primary_entities: the canonical names of the entities the page is mainly about (the "
        "list may be empty). The site's own name is a primary entity only if the page is about "
        "the organization itself;\n"
        "- entities: one item for each block in which an entity is mentioned, with:\n"
        "  - block_id: the id of that block;\n"
        "  - surface_form: as defined above;\n"
        "  - canonical_name: the entity's base name, uninflected and in full form, not "
        "translated;\n"
        "  - type: one of the types defined below;\n"
        "  - subtype: one value from the subtype list of that type (null for a person);\n"
        "  - description: what the entity is in this text, at most 10 words.\n\n"
        "Types:\n" + "\n".join(f"- {kind}: {text}" for kind, text in TYPE_DEFINITIONS.items())
        + "\n\nSubtypes by type, each with its meaning:\n" + _subtype_lines()
    )


BLOCK_PROMPT = block_prompt()
BLOCK_PROMPT_WITH_KIND = block_prompt(with_kind=True)


def block_text(block: Mapping) -> str:
    """A blokk szövege a bemenethez; táblázatsornál a cellák oszlopfejléccel
    („fejléc: érték”, `; `-vel elválasztva)."""
    cells = block.get("cells")
    if not cells:
        return block.get("text") or ""
    return "; ".join(f"{cell['header']}: {cell['value']}" if cell.get("header")
                     else cell["value"] for cell in cells)


def _searchable(block: Mapping) -> str:
    return normalize_text(f"{block_text(block)}\n{block.get('text') or ''}")


def block_input(site_description: str, blocks: Sequence[Mapping],
                with_kind: bool = False) -> str:
    """A hívás bemenete: a site-leíró sor, utána blokkonként az `[id]` (`with_kind`:
    `[id kind]`) sor és a szöveg, üres sorral elválasztva, a dokumentum sorrendjében;
    heading-útvonal nélkül."""
    parts = [site_description.strip()]
    for block in blocks:
        label = f"{block['id']} {block['kind']}" if with_kind and block.get("kind") \
            else block["id"]
        parts.append(f"[{label}]\n{block_text(block)}")
    return "\n\n".join(parts)


def check_surface(entity: BlockEntity, blocks: Mapping[str, Mapping]) -> bool:
    """A `surface_form` szerepel-e a megadott blokk szövegében szóhatárral: előtte és utána nem
    állhat betű (Unicode-betű; whitespace és kis-nagybetű nélkül; táblázatsornál a cellákban
    vagy a sor szövegében); ismeretlen blokknál nem."""
    block = blocks.get(entity.block_id)
    surface = normalize_text(entity.surface_form)
    if not block or not surface:
        return False
    pattern = rf"(?<![^\W\d_]){re.escape(surface)}(?![^\W\d_])"
    return re.search(pattern, _searchable(block)) is not None
