"""Bizonyíték-tábla, entitás-áttekintő és döntési kombinációk (tests/acceptance/evidence.py),
hálózat nélkül."""
import json
from types import SimpleNamespace

import httpx
import pytest

import tests.acceptance.evidence as ev
import tests.acceptance.gate_eval as ge
import tests.acceptance.synthetic_eval as se
from aaa2.entities.gate import wikidata_hit, wikipedia_page
from tests.acceptance.evidence_report import write_report
from tests.test_gate_eval import FULL, MAIN_PAGE, MODEL, REGRESSION, REGRESSION_PAGE, VERDICTS
from tests.test_gate_eval import write as write_record


@pytest.mark.parametrize(("name", "keyword"), [
    ("Mérési Architektúra", "mérési architektúra"),
    ("GA4 (beállítás)!", "ga4 beállítás"),
    ("'nduja di Spilinga D.O.P.", "'nduja di spilinga d.o.p."),
    ("Sebesség–konverzió korreláció", "sebesség konverzió korreláció"),
    ("egy két három négy öt hat hét nyolc kilenc tíz tizenegy", None),
    ("x" * 81, None),
    ("!!!", None),
])
def test_ads_keyword(name, keyword):
    assert ev.ads_keyword(name) == keyword


def test_rows_merge_reference_and_model_with_outcomes_and_verdicts():
    rows = ev.page_rows(MAIN_PAGE, FULL, VERDICTS)
    assert [(r.canonical, r.source, [g.canonical for g in r.models], r.recognized, r.naming,
             r.verdicts) for r in rows] == [
        ("Bérszámfejtés Csomag", "mindkettő (kötelező)", ["Bérszámfejtés Csomag"], "igen",
         "helyes", ["valid"]),
        ("készletforgási sebesség", "mindkettő (kötelező)", ["Készletforgási sebesség"], "igen",
         "helyes", ["valid"]),
        ("Számlázó", "mindkettő (kötelező)", ["Számlázó", "Méri"], "igen", "helyes", []),
        ("havi zárás", "mindkettő (opcionális)", ["Havi zárás"], "igen", "helyes",
         ["megítéletlen"]),
        ("Raktári rendetlenség", "modell", ["Raktári rendetlenség"], "–", "–",
         ["descriptive"])]
    score = se.score_page(MAIN_PAGE, FULL)
    required = ev.outcomes(MAIN_PAGE, FULL, MAIN_PAGE["gold"]["entities"])
    assert sum(r for r, _ in required) == score.recognized
    assert sum(n for _, n in required) == score.well_named


def test_market_keywords_hungarian_pages_go_to_both_markets():
    pages = [MAIN_PAGE, {**REGRESSION_PAGE, "page_id": "ngx", "lang": "en"}]
    rows = {"kk_coach_meres_hu": ev.page_rows(MAIN_PAGE, FULL, {}),
            "ngx": ev.page_rows(pages[1], REGRESSION, {})}
    keywords = ev.market_keywords(pages, rows)
    assert "készletforgási sebesség" in keywords["hu"] and "pizza margherita" not in keywords["hu"]
    assert {"készletforgási sebesség", "pizza margherita", "d.o.p."} <= set(keywords["en"])
    assert keywords["en"] == sorted(set(keywords["en"]))


class Queue:
    """A DataForSEO normál sora: task_post, egy „sorban” válasz, utána kész."""

    def __init__(self):
        self.posts, self.gets = [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Basic ")
        if request.url.path.endswith("/task_post"):
            tasks = json.loads(request.content)
            self.posts.append(tasks)
            return httpx.Response(200, json={"cost": 0.12, "tasks": [
                {"id": f"id-{t['tag']}", "status_code": 20100, "cost": 0.06, "data": t}
                for t in tasks]})
        task_id = request.url.path.rsplit("/", 1)[1]
        self.gets.append(task_id)
        if self.gets.count(task_id) == 1:
            return httpx.Response(200, json={"tasks": [{"status_code": 40602}]})
        return httpx.Response(200, json={"tasks": [{"status_code": 20000, "cost": 0, "result": [
            {"keyword": "a", "search_volume": 50}, {"keyword": "b", "search_volume": None}]}]})


def test_volumes_are_posted_once_polled_and_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFORSEO_LOGIN", "x")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "y")
    queue, sleeps = Queue(), []
    client = httpx.Client(transport=httpx.MockTransport(queue))
    out = ev.fetch_volumes({"hu": ["a", "b"], "en": ["a"]}, tmp_path, client, sleeps.append)
    assert [[t["tag"] for t in post] for post in queue.posts] == [["hu", "en"]]
    assert queue.posts[0][0] == {"keywords": ["a", "b"], "tag": "hu", "location_code": 2348,
                                 "language_code": "hu"}
    assert queue.posts[0][1] == {"keywords": ["a"], "tag": "en", "language_code": "en"}
    assert out["hu"]["results"] == {"a": 50, "b": None} and out["hu"]["cost"] == 0.06
    assert len(sleeps) == 2
    again = ev.fetch_volumes({"hu": ["a", "b"], "en": ["a"]}, tmp_path, client, sleeps.append)
    assert again == out and len(queue.posts) == 1
    assert ev.volume_of(["A", "B"], out["hu"]["results"]) == 50
    assert ev.volume_of(["B"], out["hu"]["results"]) is None
    assert ev.volume_of(["A"], None) is None


def evidence(**kw):
    base = {"page_id": "p", "key": "k", "canonical": "K", "type": "concept", "verdict": "valid",
            "volume_hu": None, "volume_en": None, "knowledge": None, "structure": None,
            "blocks": 1, "prominent": False, "luna": True, "sol": True}
    return ev.Evidence(**{**base, **kw})


@pytest.mark.parametrize(("item", "a", "b", "c", "d"), [
    (evidence(), False, False, False, True),
    (evidence(volume_en=40), True, True, True, True),          # vol ≥ 10
    (evidence(blocks=2, sol=False), True, False, False, False),
    (evidence(blocks=2, sol=False, prominent=True), True, False, True, False),
    (evidence(type="service", structure="table_row:b3", sol=False), True, False, True, False),
    (evidence(volume_hu=40, sol=False), True, False, True, False),
    (evidence(knowledge="wikidata:hu:Q1", sol=None), True, True, True, True),
])
def test_rules(item, a, b, c, d):
    assert [ev.rule(name, 10)(item) for name in "abcd"] == [a, b, c, d]
    assert ev.rule("a", None)(evidence(volume_en=40)) is False


def test_combinations_and_apply():
    labels = [label for label, _, _ in ev.combos()]
    assert labels[:2] == ["szűrés nélkül", "(d) csak Sol"] and len(labels) == 14
    kept = ev.apply(FULL, [evidence(key="raktari rendetlenseg", sol=False)], ev.rule("d", None))
    assert "Raktári rendetlenség" not in {e["canonical_name"] for e in kept["entities"]}
    assert len(kept["entities"]) == len(FULL["entities"]) - 1


def test_knowledge_details():
    assert wikidata_hit({"search": [{"id": "Q1", "match": {"type": "alias", "language": "hu",
                                                           "text": "KPI"}}]}, "kpi", "hu") == {
        "id": "Q1", "match": "alias", "text": "KPI"}
    body = {"query": {"redirects": [{"from": "SEO", "to": "Keresőoptimalizálás"}],
                      "pages": [{"title": "Keresőoptimalizálás"}]}}
    assert wikipedia_page(body) == {"title": "Keresőoptimalizálás", "redirect": True}


def test_report_writes_combinations_and_overview(tmp_path):
    write_record(tmp_path, "kk_coach_meres_hu", "cp", FULL)
    write_record(tmp_path, "materia_etlap_hu", "cp", REGRESSION)
    pages = [MAIN_PAGE, REGRESSION_PAGE]
    records = {p["page_id"]: ge.read_record(tmp_path, p["page_id"], MODEL, "cp") for p in pages}
    rows = {p["page_id"]: ev.page_rows(p, records[p["page_id"]], VERDICTS) for p in pages}
    evid = {p["page_id"]: ev.evidence_rows(p, records[p["page_id"]], [], None, None, {},
                                           VERDICTS) for p in pages}
    args = SimpleNamespace(data_dir=tmp_path, out=tmp_path, source="cp", sol="none")
    volumes = {"hu": {"keywords": ["a"], "task_id": "t", "cost": 0.06, "results": {"a": 5}}}
    text = write_report(args, MODEL, pages, records, rows, evid, volumes, VERDICTS
                        ).read_text(encoding="utf-8")
    assert "- hu: 1 kulcsszó, feladat `t`, költség 0.06 USD; adat 1, ebből > 0: 1" in text
    assert "| szűrés nélkül | 66.7 (2/3) |" in text
    assert "## Entitás-áttekintő: kk_coach_meres_hu" in text
    assert "| (d) csak Sol | 66.7 (2/3) |" in text                    # Sol-döntés nincs
