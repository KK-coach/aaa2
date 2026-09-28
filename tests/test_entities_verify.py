"""Entitásonkénti ellenőrzés (aaa2/entities/verify.py): a bemenet, a döntések alkalmazása és a
prompt példái, hálózat nélkül."""
import json
import re

import tests.acceptance.synthetic_eval as se
from aaa2.db.connect import connect
from aaa2.entities.blocks import block_text
from aaa2.entities.gate import soft_items
from aaa2.entities.verify import VERIFY_PROMPT, item_block, verify_input, verify_record
from aaa2.llm.adapters import Reply
from aaa2.llm.client import LLMClient, Retry
from aaa2.llm.config import Usage, load_config

CONFIG = load_config()
BLOCKS = {b["id"]: b for b in [
    {"id": "b0", "kind": "paragraph", "text": "A bérszámfejtés csomagban havi zárás is van."},
    {"id": "b1", "kind": "heading", "text": "Bérszámfejtés Csomag"},
    {"id": "b2", "kind": "paragraph", "text": "A raktári rendetlenség lassít. " + "x" * 700},
]}


def mention(block, surface, name, kind="concept"):
    return {"block_id": block, "surface_form": surface, "canonical_name": name, "type": kind,
            "subtype": None, "description": "d"}


RECORD = {"call_id": 7, "entities": [
    mention("b0", "bérszámfejtés csomagban", "Bérszámfejtés Csomag", "service"),
    mention("b1", "Bérszámfejtés Csomag", "Bérszámfejtés Csomag", "service"),
    mention("b2", "raktári rendetlenség", "Raktári rendetlenség"),
    mention("b0", "havi zárás", "Havi zárás"),
    mention("b0", "bérszámfejtés", "Bérszámfejtő", "tech"),
]}


class Scripted:
    def __init__(self, replies):
        self.config = CONFIG.providers["openai"]
        self.replies = list(replies)
        self.inputs = []

    def call(self, model, schema, prompt, input, **options):
        self.options = options
        self.inputs.append((prompt, input))
        return Reply(text=json.dumps(self.replies.pop(0), ensure_ascii=False),
                     usage=Usage(input=100, output=10))


def client(tmp_path, replies):
    adapter = Scripted(replies)
    con = connect(":memory:")
    return LLMClient(con, adapter, CONFIG, tmp_path / "l.jsonl",
                     retry=Retry(sleep=lambda _: None)), adapter, con


def test_input_names_type_and_the_structural_block_first():
    items = soft_items(RECORD["entities"], BLOCKS)
    assert item_block(items[0], BLOCKS)["id"] == "b1"
    text = verify_input(items, BLOCKS)
    assert text.startswith("Candidates:\n\n[c0] service · Bérszámfejtés Csomag\n"
                           "heading: Bérszámfejtés Csomag\n\n[c1] concept · Raktári rendetlenség"
                           "\nparagraph: A raktári rendetlenség lassít. xxx")
    assert "x …\n\n[c2] concept · Havi zárás\nparagraph: A bérszámfejtés" in text
    assert "Bérszámfejtő" not in text


def test_decisions_drop_every_mention_and_missing_answers_keep(tmp_path):
    llm, adapter, con = client(tmp_path, [{"decisions": [
        {"candidate_id": "c0", "keep": True}, {"candidate_id": "[c1]", "keep": False}]}])
    out = verify_record(llm, RECORD, BLOCKS)
    assert adapter.inputs[0][0] == VERIFY_PROMPT
    assert [e["canonical_name"] for e in out["entities"]] == [
        "Bérszámfejtés Csomag", "Bérszámfejtés Csomag", "Havi zárás", "Bérszámfejtő"]
    assert out["verify_decisions"] == [("Bérszámfejtés Csomag", "service", True),
                                       ("Raktári rendetlenség", "concept", False),
                                       ("Havi zárás", "concept", None)]
    assert (out["verify_missing"], out["verify_error"]) == (1, None)
    assert out["call_ids"] == [7, out["verify_call_id"]]
    assert con.execute("SELECT purpose FROM llm_calls").fetchall() == [("verify",)]
    assert adapter.options == {"max_output_tokens": 4000}


def test_a_failed_call_keeps_everything(tmp_path):
    llm, _, _ = client(tmp_path, [{"wrong": []}])
    out = verify_record(llm, RECORD, BLOCKS)
    assert out["entities"] == RECORD["entities"]
    assert out["verify_error"].startswith("schema_mismatch: ")
    assert out["call_ids"] == [7, out["verify_call_id"]]


def test_no_candidates_no_call(tmp_path):
    llm, adapter, _ = client(tmp_path, [])
    out = verify_record(llm, {"call_ids": [3], "entities": [RECORD["entities"][-1]]}, BLOCKS)
    assert (adapter.inputs, out["call_ids"], out["verify_decisions"]) == ([], [3], [])


def test_prompt_has_four_to_six_example_pairs_off_every_test_and_development_page():
    examples = re.findall(r"^- (concept|service) “([^”]+)”, (\w+): "
                          r"“([^”]+)” → (keep|do not keep)$",
                          VERIFY_PROMPT, re.MULTILINE)
    assert len(examples) == 8
    assert [e[4] for e in examples] == ["keep", "do not keep"] * 4
    assert {e[0] for e in examples[:4]} == {"concept"}
    assert {e[0] for e in examples[4:]} == {"service"}
    pages = se.load_pages(page_set="all") + se.load_pages(page_set=se.REAL)
    assert {p["page_id"] for p in pages} >= {"kk_coach_meres_hu", "ngx_accordion_en",
                                             "materia_etlap_hu", "s6_calici_evento_it"}
    for page in pages:
        text = "\n".join(block_text(b) for b in page["blocks"]).casefold()
        assert [e[1] for e in examples if e[1].casefold() in text] == [], page["page_id"]
        assert [e[3] for e in examples if e[3].casefold() in text] == [], page["page_id"]
