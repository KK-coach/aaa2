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
from collections import Counter
from collections.abc import Mapping, Sequence

from aaa2.entities.llm import TYPE_DEFINITIONS, normalize_text
from aaa2.llm.schemas import SUBTYPE_GLOSSARY, BlockEntity


def _subtype_lines() -> str:
    lines = []
    for kind, values in SUBTYPE_GLOSSARY.items():
        explained = "; ".join(f"{value} ({text})" for value, text in values.items())
        lines.append(f"- {kind}: {explained or 'none (null)'}")
    return "\n".join(lines)


def block_prompt() -> str:
    """A blokkos prompt (a mesterséges oldalak 3a változata, rögzítve)."""
    return (
        "Extract every entity the page is about or mentions: named things, offered products or "
        "services, and definable professional concepts. A single mention is enough. Generic "
        "nouns on their own are not entities.\n"
        "The input starts with one line describing the website; it is not page text. Then come "
        "the page blocks in document order, headings included: each starts with its id in "
        "square brackets on its own line, followed by the block text.\n\n"
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


CHUNK_MAX_BLOCKS = 80


def chunk_blocks(blocks: Sequence[Mapping], max_blocks: int = CHUNK_MAX_BLOCKS
                 ) -> list[list[Mapping]]:
    """Darabolás hosszú oldalra: ha a blokkok száma legfeljebb `max_blocks`, egy darab. Különben
    a title-blokkon kívüli blokkok szakaszokra a legfelső, legalább kétszer előforduló
    heading-mélység mentén (a heading-útvonal hossza); a célméretnél (a blokkszám egyenletesen
    elosztva a legkevesebb szükséges darabra) nagyobb szakasz a következő mélységen, ennek
    híján a célméretenként vágva. A szakaszok sorrendben a legkevesebb olyan darabba, amelyek
    egyike sem lépi át a `max_blocks`-ot (a title-lel együtt), és ezen belül a legnagyobb darab
    a lehető legkisebb. Minden darab elején a title-blokk."""
    if len(blocks) <= max_blocks:
        return [list(blocks)]
    title = [b for b in blocks if b.get("kind") == "title"]
    body = [b for b in blocks if b.get("kind") != "title"]
    size = max_blocks - len(title)
    target = -(-len(body) // -(-len(body) // size))
    sections = _sections(body, target, 0)
    return [title + [block for section in group for block in section]
            for group in _balanced(sections, size)]


def _balanced(sections: list[list[Mapping]], size: int) -> list[list[list[Mapping]]]:
    """A szakaszok a legkevesebb, legfeljebb `size` blokkos darabba, a legnagyobb darabot
    minimalizálva (sorrendtartó felosztás)."""
    lengths = [len(s) for s in sections]
    count, current = 1, 0
    for length in lengths:
        if current and current + length > size:
            count, current = count + 1, 0
        current += length
    prefix = [0]
    for length in lengths:
        prefix.append(prefix[-1] + length)
    n = len(lengths)
    inf = float("inf")
    best = [[inf] * (n + 1) for _ in range(count + 1)]
    cut = [[0] * (n + 1) for _ in range(count + 1)]
    best[0][0] = 0
    for parts in range(1, count + 1):
        for end in range(1, n + 1):
            for start in range(parts - 1, end):
                value = max(best[parts - 1][start], prefix[end] - prefix[start])
                if value < best[parts][end]:
                    best[parts][end], cut[parts][end] = value, start
    groups: list[list[list[Mapping]]] = []
    end = n
    for parts in range(count, 0, -1):
        start = cut[parts][end]
        groups.insert(0, sections[start:end])
        end = start
    return groups


def _sections(blocks: list[Mapping], size: int, below: int) -> list[list[Mapping]]:
    depths = Counter(len(b.get("heading_path") or []) for b in blocks
                     if b.get("kind") == "heading")
    levels = sorted(d for d, n in depths.items() if n >= 2 and d > below)
    if not levels:
        return [blocks[i:i + size] for i in range(0, len(blocks), size)]
    level = levels[0]
    sections: list[list[Mapping]] = []
    for block in blocks:
        if block.get("kind") == "heading" and len(block.get("heading_path") or []) == level \
                and sections and sections[-1]:
            sections.append([])
        if not sections:
            sections.append([])
        sections[-1].append(block)
    out: list[list[Mapping]] = []
    for section in sections:
        out += _sections(section, size, level) if len(section) > size else [section]
    return out


def block_input(site_description: str, blocks: Sequence[Mapping]) -> str:
    """A hívás bemenete: a site-leíró sor, utána blokkonként az `[id]` sor és a szöveg, üres
    sorral elválasztva, a dokumentum sorrendjében; heading-útvonal és blokktípus nélkül."""
    parts = [site_description.strip()]
    parts += [f"[{block['id']}]\n{block_text(block)}" for block in blocks]
    return "\n\n".join(parts)


def surface_spans(surface: str, block: Mapping) -> list[tuple[int, int]]:
    """A `surface` szóhatáros előfordulásai (kezdet, vég) a blokk normalizált szövegében
    (whitespace és kis-nagybetű nélkül; táblázatsornál a cellák, majd a sor szövege). Szóhatár:
    előtte és utána nem állhat Unicode-betű."""
    needle = normalize_text(surface)
    if not needle:
        return []
    pattern = rf"(?<![^\W\d_]){re.escape(needle)}(?![^\W\d_])"
    return [m.span() for m in re.finditer(pattern, _searchable(block))]


def check_surface(entity: BlockEntity, blocks: Mapping[str, Mapping]) -> bool:
    """A `surface_form` szerepel-e a megadott blokk szövegében szóhatárral (`surface_spans`);
    ismeretlen blokknál nem."""
    block = blocks.get(entity.block_id)
    return bool(block) and bool(surface_spans(entity.surface_form, block))
