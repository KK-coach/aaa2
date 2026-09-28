"""A megközelítés v3 szabálya (tests/acceptance/v3_eval.py) és a zárolt oldalak sablonja
(tests/acceptance/annotation.py), hálózat nélkül."""
import json

from aaa2.db.connect import connect
from aaa2.entities.gate import PageContext
from aaa2.llm.adapters import Reply
from aaa2.llm.client import LLMClient, Retry
from aaa2.llm.config import Usage, load_config
from tests.acceptance.annotation import LOCKED_SITES, locked_page_id, locked_sources
from tests.acceptance.v3_eval import apply_v3

CONFIG = load_config()
BLOCKS = [
    {"id": "b0", "kind": "title", "text": "Könyvelés és bérszámfejtés"},
    {"id": "b1", "kind": "heading", "text": "Bérszámfejtés Csomag"},
    {"id": "b2", "kind": "heading", "text": "Éves zárás"},
    {"id": "b3", "kind": "paragraph", "text": "Átvesszük a számlázást a Számlázóval."},
    {"id": "b4", "kind": "paragraph", "text": "A készletforgás és a cash-flow. Készletforgás."},
]
PAGE = PageContext(BLOCKS, chrome=["Tanácsadás"], anchors=["Adóbevallás"], lang="hu")


def mention(block, surface, name, kind):
    return {"block_id": block, "surface_form": surface, "canonical_name": name, "type": kind,
            "subtype": None, "description": "d"}


RECORD = {"call_ids": [5], "entities": [
    mention("b1", "Bérszámfejtés Csomag", "Bérszámfejtés Csomag", "service"),
    mention("b2", "Éves zárás", "Éves zárás", "service"),
    mention("b3", "számlázást", "Számlázás", "service"),
    mention("b3", "Számlázóval", "Számlázó", "tech"),
    mention("b4", "cash-flow", "Cash-flow", "concept"),
    mention("b4", "készletforgás", "Készletforgás", "concept"),
    mention("b4", "Készletforgás", "Készletforgás", "concept"),
    mention("b0", "bérszámfejtés", "Bérszámfejtés", "concept"),
]}


class Scripted:
    def __init__(self, replies):
        self.config = CONFIG.providers["openai"]
        self.replies = list(replies)
        self.inputs = []

    def call(self, model, schema, prompt, input, **options):
        self.options = options
        self.inputs.append(input)
        return Reply(text=json.dumps(self.replies.pop(0), ensure_ascii=False),
                     usage=Usage(input=100, output=10))


def test_services_need_structure_and_no_sol_veto_concepts_all_stay(tmp_path):
    adapter = Scripted([{"decisions": [{"candidate_id": "c0", "keep": True},
                                       {"candidate_id": "c1", "keep": False}]}])
    client = LLMClient(connect(":memory:"), adapter, CONFIG, tmp_path / "l.jsonl",
                       retry=Retry(sleep=lambda _: None))
    known = {"Cash-flow": "wikidata:hu:Q1"}
    out = apply_v3(RECORD, PAGE, client, lambda names, lang: known.get(names[0]))
    assert "[c0] service · Bérszámfejtés Csomag" in adapter.inputs[0]
    assert "Számlázás" not in adapter.inputs[0]                 # nincs szerkezeti helye
    assert [e["canonical_name"] for e in out["entities"]] == [
        "Bérszámfejtés Csomag", "Számlázó", "Cash-flow", "Készletforgás", "Készletforgás",
        "Bérszámfejtés"]
    assert out["v3"]["services"] == [
        {"canonical": "Bérszámfejtés Csomag", "structure": "heading:b1", "sol": True,
         "kept": True},
        {"canonical": "Éves zárás", "structure": "heading:b2", "sol": False, "kept": False},
        {"canonical": "Számlázás", "structure": None, "sol": None, "kept": False}]
    assert [(c["canonical"], c["rank"], c["mentions"], c["prominent"], c["blocks"],
             c["knowledge"]) for c in out["v3"]["concepts"]] == [
        ("Bérszámfejtés", 1, 1, True, 2, None),
        ("Készletforgás", 2, 2, False, 1, None),
        ("Cash-flow", 3, 1, False, 1, "wikidata:hu:Q1")]
    assert out["call_ids"] == [5, out["verify_call_id"]]


def test_no_structural_service_no_call():
    record = {"entities": [mention("b3", "számlázást", "Számlázás", "service")]}
    out = apply_v3(record, PAGE, None, lambda names, lang: None)
    assert out["entities"] == [] and out["v3"]["services"][0]["kept"] is False


def test_locked_page_ids_and_sources():
    assert locked_page_id("kk", "https://kk.coach/privacy-policy/") == "locked_kk_privacy_policy"
    assert locked_page_id("ngx", "https://x.com/components/tabs?tab=api") == "locked_ngx_tabs_api"
    long = locked_page_id("kk", "https://kk.coach/hu/egy-ketto-harom-negy-ot-hat-het-nyolc-"
                                "kilenc-tiz-tizenegy-tizenketto/")
    assert long == "locked_kk_egy_ketto_harom_negy_ot_hat_het_nyolc"          # ≤ 40 jel
    sources = locked_sources()
    assert len(sources) == 6
    assert {db for _, db, _, _ in sources} == set(LOCKED_SITES)
    assert all(pid.startswith("locked_") for pid, _, _, _ in sources)


GOLD_PAGE = {
    "page_id": "p", "lang": "hu", "blocks": BLOCKS,
    "gold": {"primary_entities": [], "negatives": [], "optional": [], "entities": [
        {"canonical": "Bérszámfejtés Csomag", "type": "service", "aliases": [],
         "surface_forms": [{"block": "b1", "text": "Bérszámfejtés Csomag"}]},
        {"canonical": "Éves zárás", "type": "service", "aliases": [],
         "surface_forms": [{"block": "b2", "text": "Éves zárás"}]},
        {"canonical": "Számlázó", "type": "tech", "aliases": [],
         "surface_forms": [{"block": "b3", "text": "Számlázóval"}]},
        {"canonical": "készletforgás", "type": "concept", "aliases": [],
         "surface_forms": [{"block": "b4", "text": "készletforgás"}]},
    ]}}


def test_scoring_named_services_concepts_and_the_rule(tmp_path):
    from tests.acceptance.v3_eval import (
        export_v3_verdicts,
        report_markdown,
        score_v3,
        total,
    )
    out = apply_v3(RECORD, PAGE, None, lambda names, lang: None)
    out["entities"] = [e for e in out["entities"] if e["canonical_name"] != "Éves zárás"]
    verdicts = {("p", "berszamfejtes csomag", "service"): "valid",
                ("p", "berszamfejtes", "concept"): "valid",
                ("p", "keszletforgas", "concept"): "descriptive"}
    score = score_v3(GOLD_PAGE, out, verdicts, source=RECORD)
    assert (score.named, score.named_recognized, score.named_found) == (1, 1, 1)
    assert (score.good_hard, score.wrong_hard) == (1, 0)
    assert (score.services, score.services_found, score.service_verdicts) == (2, 1, ["valid"])
    assert (score.concepts, score.concepts_recognized) == (1, 1)
    assert score.top[10] == ["valid", "descriptive", None]
    assert score.missed == [("Éves zárás", "service", "felismerési")]
    assert score.rule_dropped == [("Éves zárás", "kötelező: Éves zárás")]
    text = report_markdown([("zárolt oldalak", [score]), ("fejlesztési oldalak", [])])
    assert "| **zárolt oldalak** | 100.0 (1/1) | 100.0 (1/1) | 100.0 (1/1) | 50.0 (1/2) | " \
           "100.0 (1/1) | 100.0 (1/1) | 50.0 (1/2) | 50.0 (1/2) | 1 |" in text
    assert "- saját ajánlat recall: 50.0 (1/2) (≥ 90: NEM)" in text
    assert total([score, score], "x").services == 4
    path = tmp_path / "v.json"
    assert export_v3_verdicts([GOLD_PAGE], {"p": out}, path) == 4
    items = json.loads(path.read_text(encoding="utf-8"))["items"]
    assert [(i["canonical"], i["type"]) for i in items] == [
        ("Bérszámfejtés Csomag", "service"), ("Cash-flow", "concept"),
        ("Készletforgás", "concept"), ("Bérszámfejtés", "concept")]
