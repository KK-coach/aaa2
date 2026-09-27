"""A blokkos prompt v2 (aaa2/entities/blocks.py) és a mesterséges oldalak pontozása
(tests/acceptance/synthetic_eval.py), hálózat nélkül."""
import json

import pytest

import tests.acceptance.synthetic_eval as se
from aaa2.entities.blocks import BLOCK_PROMPT, block_input, block_text, check_surface
from aaa2.llm.adapters import Reply
from aaa2.llm.client import LLMClient, Retry
from aaa2.llm.config import Usage, load_config
from aaa2.llm.schemas import BlockEntity

CONFIG = load_config()
PAGES = {page["page_id"]: page for page in se.load_pages()}
S1 = PAGES["s1_lumen_meres_hu"]


def mention(block, surface, canonical, kind, subtype=None):
    return {"block_id": block, "surface_form": surface, "canonical_name": canonical,
            "type": kind, "subtype": subtype, "description": "d"}


def gold_as_output(page):
    return {"primary_entities": page["gold"]["primary_entities"], "entities": [
        mention(s["block"], s["text"], e["canonical"], e["type"], e["subtype"])
        for e in page["gold"]["entities"] for s in e["surface_forms"]]}


def test_the_three_pages():
    assert {pid: (len(p["blocks"]), len(p["gold"]["entities"])) for pid, p in PAGES.items()} == {
        "s1_lumen_meres_hu": (17, 27), "s2_osteria_etlap_hu": (19, 24),
        "s3_atlas_tabs_en": (15, 20)}


@pytest.mark.parametrize("page_id", list(PAGES))
def test_the_reference_itself_scores_full(page_id):
    """A referencia szöveg szerinti alakjai a megadott blokkban állnak; a tökéletes kimenet 100%."""
    s = se.score_page(PAGES[page_id], gold_as_output(PAGES[page_id]))
    assert (s.found, s.good, s.wrong, s.type_right, s.fabricated) == (
        s.required, s.required, 0, s.required, [])
    assert (s.subtype_right, s.block_right, s.primary_found) == (
        s.subtype_total, s.block_total, s.primary_total)


def test_block_prompt_v2():
    assert BLOCK_PROMPT.startswith(
        "Extract every entity the page is about or mentions: named things, offered products or "
        "services, and definable professional concepts. A single mention is enough. Generic "
        "nouns on their own are not entities.")
    for phrase in ("block_id", "surface_form", "canonical_name", "subtype", "at most 10 words",
                   "primary_entities", "compound name yields a separate entity only if"):
        assert phrase in BLOCK_PROMPT
    for gone in ("generic noun mentioned once", "3 to 15"):
        assert gone not in BLOCK_PROMPT
    for page in PAGES.values():                           # ismert entitás nem kerül a promptba
        assert all(e["canonical"] not in BLOCK_PROMPT for e in page["gold"]["entities"]
                   if len(e["canonical"]) > 4)


def test_block_input_lists_the_blocks_with_id_and_heading_path():
    text = block_input(S1["site_description"], S1["blocks"][:4])
    assert text.split("\n\n") == [
        S1["site_description"],
        "[b0]\nMérési rendszer kiépítése – Lumen Growth",
        "[b1] Mérési rendszer kiépítése\nMérési rendszer kiépítése",
        "[b2] Mérési rendszer kiépítése\n" + S1["blocks"][2]["text"],
        "[b3] Mérési rendszer kiépítése › Mikor van szükséged rá?\nMikor van szükséged rá?"]


def test_table_row_carries_the_column_headers():
    row = next(b for b in PAGES["s2_osteria_etlap_hu"]["blocks"] if b["id"] == "b15")
    assert block_text(row) == "Bor: Barolo DOCG; Régió: Piemont; Évjárat: 2019; Ár: 32 000 Ft"
    blocks = {"b15": row}
    assert check_surface(BlockEntity(**mention("b15", "barolo  DOCG", "Barolo", "product")),
                         blocks)
    assert check_surface(BlockEntity(**mention("b15", "Barolo DOCG | Piemont", "x", "product")),
                         blocks)                           # a sor eredeti szövege is számít
    assert not check_surface(BlockEntity(**mention("b9", "Barolo", "Barolo", "product")), blocks)


def test_score_page_classifies_every_output():
    output = {"primary_entities": ["Lumen Growth"], "entities": [
        mention("b12", "Clarity", "Clarity", "tech", "software"),        # alias → találat
        mention("b6", "Apple", "Apple", "tech", "company"),              # rossz típus
        mention("b10", "Looker Studióban", "Looker Studio", "tech", "tool"),  # rossz altípus
        mention("b9", "szerver oldali mérés", "szerver oldali mérés", "concept", "method"),
        mention("b15", "Kérj ajánlatot", "Kérj ajánlatot", "concept"),   # negatív
        mention("b2", "Mérés", "mérés", "concept"),                     # referencián kívül
        mention("b9", "Meta", "Meta", "org", "company"),                 # opcionális: semleges
        mention("b2", "Google Analytics", "Google Analytics 4", "tech"),  # nincs a blokkban
        mention("b99", "GA4", "GA4", "tech"),                            # ismeretlen blokk
    ]}
    s = se.score_page(S1, output, (0.0012, 3000, 800))
    assert (s.found, s.required) == (4, 27)
    assert s.by_difficulty == {"easy": [3, 25], "hard": [1, 2]}
    assert (s.good, s.wrong, s.neutral) == (4, 2, 1)
    assert s.wrong_types == [("Apple", "org", "tech")]
    assert s.wrong_subtypes == [("Looker Studio", "software", "tool")]
    assert s.wrong_blocks == [("szerver oldali mérés", "b9")]
    assert (s.block_right, s.block_total) == (3, 4)
    assert s.negatives_hit == [("Kérj ajánlatot", "Kérj ajánlatot")]
    assert s.unlisted == [("mérés", "concept")]
    assert s.fabricated == [("Google Analytics 4", "Google Analytics", "b2"), ("GA4", "GA4", "b99")]
    assert (s.primary_found, s.primary_total, s.extra_primary) == (0, 1, ["Lumen Growth"])
    assert (s.cost_usd, s.tokens_in, s.tokens_out, s.mentions) == (0.0012, 3000, 800, 9)
    assert ("Debrecen", "hard", "place") in s.missed


def test_report_lists_the_errors_per_page():
    score = se.score_page(S1, {"primary_entities": [], "entities": [
        mention("b15", "Kérj ajánlatot", "Kérj ajánlatot", "concept"),
        mention("b6", "Apple", "Apple", "tech")]}, (0.0005, 100, 50))
    text = se.report_markdown("gpt-6-luna", [score])
    assert ("| s1_lumen_meres_hu | 3.7% (1/27) | 4.0% (1/25) | 0.0% (0/2) | 50.0% (1/2) | "
            "0.0% (0/1) | 0.0% (0/1) | 100.0% (1/1) | 0/1 | 0 | 1 / 0 | 0.00050 | 100 / 50 |"
            ) in text
    assert "- **Hibás típus (1):** Apple: org → tech" in text
    assert "- **Téves találat, negatív (1):** Kérj ajánlatot (= „Kérj ajánlatot”)" in text
    assert "Debrecen (place, hard)" in text


class Scripted:
    def __init__(self, replies):
        self.config = CONFIG.providers["openai"]
        self.replies = list(replies)
        self.inputs = []

    def call(self, model, schema, prompt, input):
        self.inputs.append((prompt, input))
        return Reply(text=json.dumps(self.replies.pop(0), ensure_ascii=False),
                     usage=Usage(input=100, output=10))


def test_call_sends_the_block_input_and_keeps_the_raw_output(tmp_path):
    from aaa2.db.connect import connect

    adapter = Scripted([gold_as_output(S1), {"entities": []}])
    client = LLMClient(connect(":memory:"), adapter, CONFIG, tmp_path / "l.jsonl",
                       retry=Retry(sleep=lambda _: None))
    ok = se.call(client, S1)
    assert adapter.inputs[0] == (BLOCK_PROMPT, block_input(S1["site_description"],
                                                           S1["blocks"]))
    assert (ok["model"], ok["primary_entities"], len(ok["entities"])) == (
        "gpt-6-luna", ["Mérési rendszer kiépítése"], 37)
    bad = se.call(client, S1)
    assert bad["call_id"] is not None and bad["error"].startswith("schema_mismatch: ")
