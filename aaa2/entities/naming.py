"""Célzott elnevezési hívás: az első kör (blokkos kinyerés) felismert említéseire egy második
LLM-hívás adja az entitás saját nevét.

- Bemenet oldalanként egy hívásban: a hivatkozott blokkok szövege, utána említésenként egy
  azonosító, a blokk, a `surface_form` és az első kör `canonical_name`-je.
- Kimenet említésenként: az entitás vagy entitások (birtokos szerkezetnél mindegyik külön, a
  saját szöveg szerinti alakjával), kanonikus névvel, típussal, altípussal. A névhez tapadt
  általános főnév nem része a névnek.
- Felismert említés: az első kör kimenetéből az, amelynek a `surface_form`-ja szóhatárral a
  megadott blokkban áll (`blocks.check_surface`); a többi (kitalált) változatlanul marad.
- Ha az elnevezés `surface_form`-ja nincs a blokkban, az első kör `surface_form`-ja marad, a név
  az elnevezésé.
- A prompt állandó, a példái nem a tesztoldalakról valók. A hívás `purpose = naming`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from aaa2.entities.blocks import _subtype_lines, block_text, check_surface, surface_spans
from aaa2.entities.llm import TYPE_DEFINITIONS
from aaa2.llm.client import LLMError, SchemaMismatch
from aaa2.llm.schemas import BlockEntity, NamingResult

NAMING_PROMPT = (
    "You get the entity mentions recognized on one web page. Each mention has an id, the block "
    "it stands in, its surface form and a first-round name. The blocks are given first, with "
    "their text. For each mention give the entity or entities it names.\n\n"
    "Rules:\n"
    "- The name is the entity's own name: its base form, uninflected, in full, not "
    "translated.\n"
    "- A general noun attached to a name is not part of the name: "
    "“Mailchimp-integráció” → Mailchimp; “Notion-sablonok” → "
    "Notion; “mascarpone-krém” → mascarpone; “Figma export” → "
    "Figma.\n"
    "- If one mention holds several entities, as in a possessive construction (“X-nek a "
    "Y-ja”, “X's Y”), return each separately with its own surface form: "
    "“a Stripe Radarja” → Stripe (“Stripe”) and Radar "
    "(“Radarja”).\n"
    "- A brand inside an official product name stays in the name: “Adobe Photoshop” "
    "is one entity.\n"
    "- For a product named after its origin, the product is the entity and its noun is part "
    "of the name: “pármai sonkával” → pármai sonka, not Parma; the place is an entity "
    "only where the text refers to the place itself.\n"
    "- surface_form: the words of the mention that name this entity, exactly as they stand in "
    "the block text, with their suffixes.\n"
    "- Return every mention id once, with at least one entity.\n\n"
    "Types:\n" + "\n".join(f"- {kind}: {text}" for kind, text in TYPE_DEFINITIONS.items())
    + "\n\nSubtypes by type, each with its meaning:\n" + _subtype_lines()
)


def recognized_mentions(record: Mapping, blocks: Mapping[str, Mapping]) -> list[tuple[int, dict]]:
    """Az első kör felismert (nem kitalált) említései a sorszámukkal."""
    return [(index, raw) for index, raw in enumerate(record.get("entities") or [])
            if check_surface(BlockEntity.model_construct(**raw), blocks)]


def naming_input(blocks: Mapping[str, Mapping], mentions: Sequence[tuple[int, dict]]) -> str:
    """A hivatkozott blokkok (dokumentum-sorrendben), utána az említések `[m<sorszám>]`
    azonosítóval."""
    used = []
    for _, raw in mentions:
        if raw["block_id"] not in used:
            used.append(raw["block_id"])
    order = list(blocks)
    used.sort(key=order.index)
    parts = ["Blocks:"] + [f"[{block_id}]\n{block_text(blocks[block_id])}" for block_id in used]
    parts.append("Mentions:\n" + "\n".join(
        f"[m{index}] block {raw['block_id']} | surface form: {raw['surface_form']} | "
        f"first-round name: {raw['canonical_name']}" for index, raw in mentions))
    return "\n\n".join(parts)


def apply_naming(record: Mapping, mentions: Sequence[tuple[int, dict]], result: NamingResult,
                 blocks: Mapping[str, Mapping]) -> tuple[list[dict], int]:
    """Az első kör említései az elnevezéssel: a felismert említés helyére a hívás entitásai
    (a blokk az említésé; a blokkban nem álló `surface_form` helyén az említésé); a kimaradt
    vagy üres választ adó említés és a kitalált változatlan. Visszaad: az új említéslista, és
    hány felismert említésre nem jött válasz."""
    named = {m.mention_id.strip().strip("[]"): m.entities for m in result.mentions}
    replaced: dict[int, list[dict]] = {}
    missing = 0
    for index, raw in mentions:
        entities = named.get(f"m{index}")
        if not entities:
            missing += 1
            continue
        block = blocks[raw["block_id"]]
        replaced[index] = [{"block_id": raw["block_id"],
                            "surface_form": e.surface_form if surface_spans(e.surface_form, block)
                            else raw["surface_form"],
                            "canonical_name": e.canonical_name, "type": e.type,
                            "subtype": e.subtype, "description": raw.get("description", "")}
                           for e in entities]
    out = []
    for index, raw in enumerate(record.get("entities") or []):
        out += replaced.get(index, [raw])
    return out, missing


def name_record(client, record: Mapping, blocks: Mapping[str, Mapping],
                page_id: int | None = None) -> dict:
    """Az első kör rekordja az elnevezési hívással. A kimenet rekordja az első körét követi
    (`entities`, `primary_entities`), mellette `naming_model`, `naming_call_id`, `call_ids` (a
    kinyerés és az
    elnevezés hívásai), `naming_missing`, `naming_error` és a nyers válasz (`naming_raw`)."""
    out = dict(record)
    out.update(naming_model=client.model, naming_call_id=None, naming_missing=0,
               naming_error=None, naming_raw=None,
               call_ids=[i for i in [record.get("call_id")] if i is not None])
    mentions = recognized_mentions(record, blocks)
    if not mentions:
        return out
    try:
        result = client.extract(NamingResult, NAMING_PROMPT, naming_input(blocks, mentions),
                                domain="entity", purpose="naming", page_id=page_id)
    except SchemaMismatch as exc:
        out.update(naming_call_id=exc.call_id, naming_error=f"schema_mismatch: {exc}"[:500])
        out["call_ids"].append(exc.call_id)
        return out
    except LLMError as exc:
        out.update(naming_error=f"call_error: {exc}"[:500])
        return out
    out["entities"], out["naming_missing"] = apply_naming(record, mentions, result.parsed,
                                                          blocks)
    out["naming_raw"] = result.parsed.model_dump()
    out["naming_call_id"] = result.call_id
    out["call_ids"].append(result.call_id)
    return out
