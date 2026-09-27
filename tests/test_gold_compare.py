"""A referencialista elleni mérés (tests/acceptance/gold_compare.py) hálózat nélkül: a gold-fájl,
a szakaszbontás, a pontozás, a jelentés és a futás feltétele."""
import json

import pytest

import tests.acceptance.gold_compare as gc
from aaa2.llm.adapters import Reply
from aaa2.llm.client import LLMClient, Retry
from aaa2.llm.config import Usage, load_config

CONFIG = load_config()
SOURCES = [("a példa kávézó budapesten működik, a pörkölést kiss anna vezeti. "
            "specialty kávé és seo mérés."), "példa kávézó"]


def write_gold(path, entities, primary=("Példa Kávézó", [])):
    path.write_text(json.dumps({"url": "https://pelda.hu/", "primary_entity": {
        "name": primary[0], "aliases": primary[1]}, "entities": entities},
        ensure_ascii=False), encoding="utf-8")
    return path


GOLD_ITEMS = [
    {"name": "Példa Kávézó", "aliases": ["Példa"], "type": "org"},
    {"name": "Kiss Anna", "type": "person"},
    {"name": "Budapest", "type": "place"},
    {"name": "specialty kávé", "type": "product"},
    {"name": "mérés", "aliases": ["mérési rendszer"], "type": "concept"},
    {"name": "SEO", "type": "concept"},
]


def ent(name, kind, evidence):
    return {"name": name, "type": kind, "description": "d", "evidence": evidence,
            "context": evidence}


def test_load_gold_checks_types_and_keys(tmp_path):
    gold = gc.load_gold(write_gold(tmp_path / "g.json", GOLD_ITEMS))
    assert gold.primary == {"pelda kavezo"}
    assert gold.match("pelda").name == "Példa Kávézó"
    assert gold.match("meresi rendszer").concept
    with pytest.raises(ValueError, match="ismeretlen típus: food"):
        gc.load_gold(write_gold(tmp_path / "b.json", [{"name": "X", "type": "food"}]))
    with pytest.raises(ValueError, match="két tételé"):
        gc.load_gold(write_gold(tmp_path / "d.json", [
            {"name": "SEO", "type": "concept"}, {"name": "X", "aliases": ["seo"], "type": "tech"}]))


def test_split_sections_uses_the_first_level_with_three_headings():
    main = "Bevezető. Egy rész a. Kettő rész b. Három rész c. Alcím x."
    headings = [(1, "Cím"), (2, "Egy"), (3, "Alcím"), (2, "Kettő"), (2, "Három")]
    assert gc.split_sections(main, headings) == [
        "Bevezető.", "Egy rész a.", "Kettő rész b.", "Három rész c. Alcím x."]
    assert gc.split_sections(main, [(2, "Egy"), (3, "Alcím")]) == [main]
    # H2-ből kevés van, H3-ból elég: a H3 a határ
    assert gc.split_sections("A x. B y. C z.", [(2, "Q"), (3, "A"), (3, "B"), (3, "C")]) == [
        "A x.", "B y.", "C z."]


def test_score_page_counts_recall_precision_type_and_primary(tmp_path):
    gold = gc.load_gold(write_gold(tmp_path / "g.json", GOLD_ITEMS))
    records = [
        {"primary_entity": "Példa Kávézó", "error": None, "entities": [
            ent("Példa", "brand", "a példa kávézó budapesten"),            # alias, rossz típus
            ent("Kiss Anna", "person", "a pörkölést kiss anna vezeti"),
            ent("Mérés", "concept", "seo mérés"),
            ent("Pörkölés", "concept", "kiss anna vezeti"),               # nincs a gold-listán
            ent("Kitalált Kft.", "org", "a kitalált kft. szervezi"),      # fabrikált
        ]},
        {"primary_entity": "Kávé", "error": None, "entities": [
            ent("Kiss Anna", "person", "kiss anna"),                      # ismétlés
            ent("SEO", "concept", "specialty kávé és seo"),
        ]},
        {"primary_entity": None, "error": "schema_mismatch: x", "entities": None},
    ]
    s = gc.score_page("p", gold, records, SOURCES, [(0.01, 1000), (0.02, 3000), (0.005, 500)])
    assert (s.named_found, s.named, s.concepts_found, s.concepts) == (2, 4, 2, 2)
    assert (s.model_matched, s.model_keys) == (4, 5)
    assert (s.typed_right, s.matched_gold) == (3, 4)
    assert (s.primary_hits, s.fabricated, s.calls, s.errors) == (1, 1, 3, 1)
    # a „Pörkölés” idézetében nincs a név; a ragozott alak („budapesten”) a név kulcsát tartalmazza
    assert s.name_missing == 1
    assert s.missed == {"p": ["Budapest", "specialty kávé"]}
    assert s.extra == {"p": ["Pörkölés"]}
    assert s.cost_usd == pytest.approx(0.035) and s.latencies == [1000, 3000, 500]


def test_report_has_one_table_row_per_model_and_mode(tmp_path):
    gold = gc.load_gold(write_gold(tmp_path / "g.json", GOLD_ITEMS))
    page = gc.score_page("p", gold, [{"primary_entity": "x", "error": None, "entities": [
        ent("Kiss Anna", "person", "kiss anna")]}], SOURCES, [(0.001, 2000)])
    total = gc.Score()
    total.add(page)
    text = gc.report_markdown({("gpt-6-luna", "page"): total})
    assert ("| `gpt-6-luna` | page | 25.0% (1/4) | 0.0% (0/2) | 100.0% (1/1) | 100.0% (1/1) | "
            "0/1 | 0 | 0 | 1 (0) | 0.0010 | 2.0 mp |") in text
    assert "- p: kihagyott (5): Példa Kávézó, Budapest, specialty kávé, mérés, SEO" in text


def test_run_waits_for_the_gold_lists(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "GOLD_DIR", tmp_path)
    with pytest.raises(SystemExit, match="a futás a referencialistára vár"):
        gc.run([("openai", "gpt-6-luna")], ["page"], tmp_path)


class Scripted:
    def __init__(self, replies):
        self.config = CONFIG.providers["anthropic"]
        self.replies = list(replies)

    def call(self, model, schema, prompt, input):
        return Reply(text=json.dumps(self.replies.pop(0), ensure_ascii=False),
                     usage=Usage(input=100, output=10))


def test_call_keeps_primary_entity_and_errors(tmp_path):
    from aaa2.db.connect import connect

    con = connect(":memory:")
    client = LLMClient(con, Scripted([
        {"primary_entity": "Mérés", "entities": [ent("SEO", "concept", "seo mérés")]},
        {"entities": []}]), CONFIG, tmp_path / "l.jsonl", retry=Retry(sleep=lambda _: None),
        model="claude-haiku-4-5-20251001")
    ok = gc._call(client, "szöveg", 7, 0)
    assert (ok["model"], ok["primary_entity"], ok["entities"][0]["name"]) == (
        "claude-haiku-4-5-20251001", "Mérés", "SEO")
    bad = gc._call(client, "szöveg", 7, 1)
    assert bad["call_id"] is not None and bad["error"].startswith("schema_mismatch: ")
