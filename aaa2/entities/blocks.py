"""Blokkformátumú LLM-kinyerés (prompt v2): a bemenet egy site-leíró sor, utána az oldal blokkjai
azonosítóval és heading-útvonallal; a kimenet említésenként blokk-azonosító, a szöveg szerinti
alak, a kanonikus név, típus, altípus és egy rövid leírás, továbbá a `primary_entities` lista.

- A blokkok most a mesterséges tesztoldalak JSON-jából jönnek (id, kind, heading_path, text;
  táblázatnál cellák oszlopfejléccel); a valódi oldalak blokk-parsere külön feladat.
- A prompt állandó; ismert entitás (a site vagy más kör találata) nem kerül bele.
- Ellenőrzés: a `surface_form` (whitespace-normalizálva, kis-nagybetű-érzéketlenül) szerepel-e a
  megadott blokk szövegében; ami nem, az kitalált (`check_surface`).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from aaa2.entities.llm import TYPE_DEFINITIONS, normalize_text
from aaa2.llm.schemas import BlockEntity

HEADING_SEPARATOR = " › "

BLOCK_PROMPT = (
    "Extract every entity the page is about or mentions: named things, offered products or "
    "services, and definable professional concepts. A single mention is enough. Generic nouns "
    "on their own are not entities.\n"
    "The input starts with one line describing the website; it is not page text. Then come the "
    "page blocks: each starts with its id in square brackets and its heading path, followed by "
    "the block text.\n"
    "Return:\n"
    "- primary_entities: the canonical names of the entities the page is mainly about (the list "
    "may be empty);\n"
    "- entities: one item for each block in which an entity is mentioned, with:\n"
    "  - block_id: the id of that block;\n"
    "  - surface_form: the mention exactly as written in that block, inflected form included;\n"
    "  - canonical_name: the entity's base name, uninflected and in full form, not translated;\n"
    "  - type: one of the types defined below;\n"
    "  - subtype: a more specific kind (for example software, company, method, dish, city), or "
    "null;\n"
    "  - description: what the entity is in this text, at most 10 words.\n"
    "A compound name yields a separate entity only if the text also refers to that part on its "
    "own. The titles of this site's own pages are not works. Return nothing that is not on the "
    "page.\n\n"
    "Types:\n" + "\n".join(f"- {kind}: {text}" for kind, text in TYPE_DEFINITIONS.items())
)


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


def block_input(site_description: str, blocks: Sequence[Mapping]) -> str:
    """A hívás bemenete: a site-leíró sor, utána blokkonként `[id] heading-útvonal` és a
    szöveg, üres sorral elválasztva."""
    parts = [site_description.strip()]
    for block in blocks:
        path = HEADING_SEPARATOR.join(block.get("heading_path") or [])
        parts.append(f"[{block['id']}] {path}".rstrip() + "\n" + block_text(block))
    return "\n\n".join(parts)


def check_surface(entity: BlockEntity, blocks: Mapping[str, Mapping]) -> bool:
    """A `surface_form` szerepel-e a megadott blokk szövegében (whitespace, kis-nagybetű
    nélkül; táblázatsornál a cellákban vagy a sor szövegében); ismeretlen blokknál nem."""
    block = blocks.get(entity.block_id)
    surface = normalize_text(entity.surface_form)
    return bool(block) and bool(surface) and surface in _searchable(block)
