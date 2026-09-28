"""Entitásonkénti ellenőrzés: a concept- és service-tételek (`gate.soft_items`) egyenként,
igen/nem kérdéssel, egy LLM-hívásban oldalanként.

- Bemenet tételenként: azonosító (`c<sorszám>`), típus, kanonikus név, és a tétel blokkja
  (`item_block`: az első szerkezeti helyű említés blokkja, ha van, különben az első említésé) a
  fajtájával és a szövegével.
- Kimenet tételenként: marad vagy kiesik. A kieső tétel minden említése kiesik; a válasz nélküli
  tétel marad (számolva). Hibás hívásnál minden marad, a hiba a rekordban; a keret-őr
  leállítása (`BudgetExceeded`) továbbmegy.
- A prompt állandó; a példapárjai (fogalom kontra leíró kifejezés, megnevezett ajánlat kontra
  tevékenység-leírás) nem a teszt- és fejlesztési oldalakról valók. A hívás `purpose = verify`,
  saját kimeneti plafonnal (`VERIFY_MAX_OUTPUT_TOKENS`), a költségőr is ezzel számol.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from aaa2.entities.blocks import block_text
from aaa2.entities.gate import STRUCTURAL_KINDS, SoftItem, soft_items
from aaa2.entities.rules import alias_key
from aaa2.llm.client import BudgetExceeded, LLMError, SchemaMismatch
from aaa2.llm.schemas import VerifyResult

BLOCK_LIMIT = 600                 # a blokk szövegéből ennyi karakter megy a bemenetbe
VERIFY_MAX_OUTPUT_TOKENS = 4000   # az ellenőrző hívás kimeneti plafonja (a költségőr is ezzel)

VERIFY_PROMPT = (
    "You check candidate entities extracted from one web page. Each candidate has an id, a "
    "type (concept or service), a name, and the block of the page it stands in (the kind of "
    "the block and its text). Decide for each candidate on its own: keep it or not.\n\n"
    "- concept: keep it if the name is a term of a concept system: an established term of a "
    "field that a glossary or an encyclopedia of that field would define, or a concept the site "
    "names and uses as its own term. Do not keep it if it is a descriptive phrase: ordinary "
    "words that describe a situation, a problem, a benefit or a quality in this text.\n"
    "- service: keep it if the name is a named offer: something the page offers under that "
    "name, as a heading, a card, a price or table row, or a menu item. Do not keep it if the "
    "text only describes an activity in a sentence.\n"
    "- Judge the name and its block only. Return every candidate id once.\n\n"
    "Examples:\n"
    "- concept “készletforgási sebesség”, paragraph: “A készletforgási sebesség "
    "mutatja, hányszor cserélődik a teljes készlet egy év alatt.” → keep\n"
    "- concept “raktári rendetlenség”, paragraph: “A raktári rendetlenség miatt a "
    "rendelések napokat késnek.” → do not keep\n"
    "- concept “mise en place”, paragraph: “Every cook sets up the mise en place before "
    "the evening service.” → keep\n"
    "- concept “quick prep work”, paragraph: “Quick prep work keeps the kitchen calm.” "
    "→ do not keep\n"
    "- service “Bérszámfejtés Csomag”, heading: “Bérszámfejtés Csomag” → keep\n"
    "- service “bérszámfejtés átvétele”, paragraph: “Átvesszük a bérszámfejtést, hogy "
    "ne kelljen vele foglalkoznia.” → do not keep\n"
    "- service “Annual Maintenance Plan”, card: “Annual Maintenance Plan · two "
    "inspections a year · $240” → keep\n"
    "- service “keeping your boiler running”, paragraph: “We keep your boiler running "
    "through the winter.” → do not keep"
)


def item_block(item: SoftItem, blocks: Mapping[str, Mapping]) -> Mapping:
    """A tétel blokkja: az első szerkezeti helyű (`gate.STRUCTURAL_KINDS`) említés blokkja, ha
    van, különben az első említésé."""
    ids = item.blocks
    return next((blocks[i] for i in ids if blocks[i].get("kind") in STRUCTURAL_KINDS),
                blocks[ids[0]])


def verify_input(items: Sequence[SoftItem], blocks: Mapping[str, Mapping]) -> str:
    lines = []
    for index, item in enumerate(items):
        block = item_block(item, blocks)
        text = block_text(block)
        if len(text) > BLOCK_LIMIT:
            text = text[:BLOCK_LIMIT] + " …"
        lines.append(f"[c{index}] {item.type} · {item.canonical}\n"
                     f"{block.get('kind') or 'block'}: {text}")
    return "Candidates:\n\n" + "\n\n".join(lines)


def verify_record(client, record: Mapping, blocks: Mapping[str, Mapping],
                  page_id: int | None = None,
                  select: Callable[[SoftItem], bool] | None = None) -> dict:
    """A rekord az ellenőrzés után. Mellette `verify_model`, `verify_call_id`, `call_ids` (az
    eddigiek és az ellenőrzésé), `verify_decisions` (név, típus, marad-e; válasz nélkül None),
    `verify_missing`, `verify_error`. `select`: csak ezek a tételek mennek a hívásba (a többi
    változatlanul marad); ha nincs ilyen, nincs hívás."""
    out = dict(record)
    out.update(verify_model=client.model, verify_call_id=None, verify_decisions=[],
               verify_missing=0, verify_error=None,
               call_ids=list(record.get("call_ids")
                             or [i for i in [record.get("call_id")] if i is not None]))
    items = [item for item in soft_items(record.get("entities") or [], blocks)
             if select is None or select(item)]
    if not items:
        return out
    try:
        result = client.extract(VerifyResult, VERIFY_PROMPT, verify_input(items, blocks),
                                domain="entity", purpose="verify", page_id=page_id,
                                max_output_tokens=VERIFY_MAX_OUTPUT_TOKENS)
    except SchemaMismatch as exc:
        out.update(verify_call_id=exc.call_id, verify_error=f"schema_mismatch: {exc}"[:500])
        out["call_ids"].append(exc.call_id)
        return out
    except BudgetExceeded:
        raise
    except LLMError as exc:
        out.update(verify_error=f"call_error: {exc}"[:500])
        return out
    answers = {d.candidate_id.strip().strip("[]"): d.keep for d in result.parsed.decisions}
    dropped = set()
    for index, item in enumerate(items):
        keep = answers.get(f"c{index}")
        out["verify_decisions"].append((item.canonical, item.type, keep))
        if keep is None:
            out["verify_missing"] += 1
        elif not keep:
            dropped.add(item.key)
    out["entities"] = [raw for raw in record.get("entities") or []
                       if alias_key(raw["canonical_name"]) not in dropped]
    out["verify_call_id"] = result.call_id
    out["call_ids"].append(result.call_id)
    return out
