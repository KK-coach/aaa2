"""Az entitás-pipeline a megközelítés v3 szerint (aaa2/entities/v3.py, extract.run_llm,
report.py, `aaa entities`, `aaa entity-report`) előre adott válaszokkal, hálózat nélkül."""
import csv
import json
from datetime import UTC, date, datetime

import pytest
from typer.testing import CliRunner

import aaa2.db.connect as connect_module
from aaa2.cli.main import app
from aaa2.db.connect import connect, db_path
from aaa2.entities.dom import page_blocks
from aaa2.entities.extract import estimate_llm, run_llm
from aaa2.entities.gate import KnowledgeBase
from aaa2.entities.knowledge import link_entities
from aaa2.entities.report import entity_table, run_report, wikipedia_url, write_entity_table
from aaa2.entities.rules import run_rules
from aaa2.entities.v3 import (
    STEPS,
    Steps,
    V3Step,
    load_pipeline,
    page_context,
    verify_usage,
)
from aaa2.entities.verify import VERIFY_PROMPT
from aaa2.llm import ledger
from aaa2.llm.adapters import Reply
from aaa2.llm.client import LLMClient, Retry
from aaa2.llm.config import Usage, load_config
from tests.test_entities_rules import html, site

NOON = datetime(2026, 9, 28, 12, 0, tzinfo=UTC).replace(tzinfo=None)
CONFIG = load_config()
ALL_STEPS = Steps()


class Scripted:
    """Kinyerő és ellenőrző válaszok külön sorban (a prompt szerint); a hívásokat rögzíti."""

    def __init__(self, extract=(), verify=()):
        self.config = CONFIG.providers["gemini"]
        self.replies = {"extract": list(extract), "verify": list(verify)}
        self.calls = []

    def call(self, model, schema, prompt, input, **options):
        kind = "verify" if prompt == VERIFY_PROMPT else "extract"
        self.calls.append((kind, input))
        reply = self.replies[kind].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Reply(text=json.dumps(reply, ensure_ascii=False),
                     usage=Usage(input=1000, output=200))

    def list_models(self):
        return []


def client_for(con, adapter, tmp_path):
    return LLMClient(con, adapter, CONFIG, tmp_path / "ledger.jsonl", lambda: NOON,
                     Retry(sleep=lambda _: None))


def mention(block, surface, name, kind, subtype=None):
    return {"block_id": block, "surface_form": surface, "canonical_name": name, "type": kind,
            "subtype": subtype, "description": "leírás"}


def reply(*mentions):
    return {"primary_entities": [], "entities": list(mentions)}


def decisions(*keeps):
    return {"decisions": [{"candidate_id": f"c{i}", "keep": keep} for i, keep in enumerate(keeps)]}


# b0 title, b1 navigáció („Könyvelés”, chrome), b2 h1, b3 bekezdés, b4 h2, b5 bekezdés
BODY = ("<nav><a href='/konyveles/'>Könyvelés</a></nav><main><h1>Bérszámfejtés Csomag</h1>"
        "<p>Átvesszük a számlázást a Számlázóval, a cash-flow átlátható marad.</p>"
        "<h2>Könyvelés</h2><p>A cash-flow és a készletforgás havonta.</p></main>")

PAGE_REPLY = reply(
    mention("b2", "Bérszámfejtés Csomag", "Bérszámfejtés Csomag", "service"),
    mention("b4", "Könyvelés", "Könyvelés", "service"),
    mention("b3", "számlázást", "Számlázás", "service"),
    mention("b3", "Számlázóval", "Számlázó", "tech"),
    mention("b3", "cash-flow", "Cash-flow", "concept"),
    mention("b5", "cash-flow", "Cash-flow", "concept"),
    mention("b5", "készletforgás", "Készletforgás", "concept"),
)


class Knowledge:
    """A `KnowledgeBase` kérője hálózat nélkül: névre Wikidata-találat (`wikidata`: név → QID,
    vagy (QID, label / alias) párok listája, vagy a nevek halmaza, akkor Q1), az elem osztályai és leírása (`classes`: QID → (osztály-
    címkék, leírás)), Wikipedia-cím (`wikipedia`); a `failing` nevekre hibás válasz."""

    def __init__(self, wikidata=(), wikipedia=(), failing=(), classes=None):
        self.wikidata = wikidata if isinstance(wikidata, dict) else dict.fromkeys(wikidata, "Q1")
        self.wikipedia, self.failing = set(wikipedia), set(failing)
        self.classes = classes or {}
        self.requests = []

    def __call__(self, service, url, params):
        args = dict(params)
        if args.get("action") == "wbgetentities":
            return "k", self._entities(args)
        name = args.get("search") or args.get("titles", "").split("|")[0]
        self.requests.append((service, name))
        if name in self.failing:
            return "k", None
        if service == "wikidata":
            lang = args["language"]
            found = self.wikidata.get(name, [])
            found = found if isinstance(found, list) else [(found, "label")]
            hits = [{"id": qid, "match": {"type": kind, "language": lang, "text": name}}
                    for qid, kind in found]
            return "k", {"search": hits}
        pages = [{"title": name}] if name in self.wikipedia else [{"title": name,
                                                                   "missing": True}]
        return "k", {"query": {"pages": pages}}

    def _entities(self, args):
        ids = args["ids"].split("|")
        if args["props"] == "labels":
            return {"entities": {i: {"labels": {"en": {"value": i.removeprefix("C:")}}}
                                 for i in ids}}
        labels, description = self.classes.get(ids[0], ([], ""))
        return {"entities": {ids[0]: {
            "descriptions": {"en": {"value": description}},
            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": f"C:{label}"}}}}
                               for label in labels]}}}}


def pipeline_run(con, adapter, tmp_path, steps=ALL_STEPS, knowledge=None, **options):
    client = client_for(con, adapter, tmp_path)
    kb = KnowledgeBase(knowledge or Knowledge())
    refine = V3Step(con, steps, client, kb, "hu")
    return run_llm(con, client, refine=refine, save=steps.save, clock=lambda: NOON,
                   **options)


def entity_names(con):
    return sorted(con.execute(
        "SELECT DISTINCT e.name, e.type FROM entities e JOIN page_entities pe USING (entity_id)"
    ).fetchall())


# ---------------------------------------------------------------------------
# konfiguráció
# ---------------------------------------------------------------------------


def test_pipeline_config_has_every_step_on_and_a_two_dollar_cap():
    pipeline = load_pipeline()
    assert pipeline.steps == Steps() and pipeline.max_usd == 2.0
    assert CONFIG.pipeline == {"extraction": "gpt-6-luna", "naming": "off",
                               "verify": "gpt-6-sol"}


@pytest.mark.parametrize(("text", "message"), [
    ("[steps]\nrules = true\n[limits]\nmax_usd = 1", r"\[steps\]"),
    ("[steps]\n" + "".join(f"{s} = 'igen'\n" for s in STEPS) + "[limits]\nmax_usd = 1",
     r"\[steps\]"),
    ("[steps]\n" + "".join(f"{s} = true\n" for s in STEPS) + "[limits]\nmax_usd = 0",
     r"\[limits\]"),
])
def test_invalid_pipeline_config_is_refused(tmp_path, text, message):
    path = tmp_path / "pipeline.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_pipeline(path)


# ---------------------------------------------------------------------------
# a v3 lépés a pipeline-ban
# ---------------------------------------------------------------------------


def test_v3_keeps_structural_services_without_veto_and_concepts_with_evidence(tmp_path):
    con = site({"/": html("Könyvelés és bérszámfejtés", BODY)})
    adapter = Scripted([PAGE_REPLY], [decisions(True, False)])
    run = pipeline_run(con, adapter, tmp_path, knowledge=Knowledge(wikidata=["Cash-flow"]))
    # A „Számlázás” bekezdésben áll: hívás nélkül kiesik; a „Könyvelés” a Sol vétójával.
    assert "[c0] service · Bérszámfejtés Csomag" in adapter.calls[1][1]
    assert "Számlázás" not in adapter.calls[1][1]
    assert entity_names(con) == [("Bérszámfejtés Csomag", "service"), ("Cash-flow", "concept"),
                                 ("Készletforgás", "concept"), ("Számlázó", "tech")]
    checks = con.execute(
        "SELECT canonical, type, entity_id IS NOT NULL, structure, blocks, mentions, "
        "prominent, rank, knowledge, sol, kept FROM soft_checks ORDER BY type, canonical"
    ).fetchall()
    assert checks == [
        ("Cash-flow", "concept", True, None, 2, 2, False, 1, "wikidata:hu:Q1 (Cash-flow)",
         None, True),
        ("Készletforgás", "concept", True, None, 1, 1, False, 2, None, None, True),
        ("Bérszámfejtés Csomag", "service", True, "heading:b2", None, 1, None, None, None,
         True, True),
        ("Könyvelés", "service", False, "heading:b4", None, 1, None, None, None, False, False),
        ("Számlázás", "service", False, None, None, 1, None, None, None, None, False),
    ]
    assert (run.pages, run.llm_calls) == (1, 2)
    assert con.execute("SELECT status, call_ids, chunks FROM entity_run_pages").fetchall() == [
        ("done", [1, 2], 1)]


def test_steps_switch_off_the_service_rule_and_the_concept_evidence(tmp_path):
    con = site({"/": html("Könyvelés és bérszámfejtés", BODY)})
    adapter = Scripted([PAGE_REPLY])
    pipeline_run(con, adapter, tmp_path, Steps(services=False, concepts=False))
    assert [kind for kind, _ in adapter.calls] == ["extract"]
    assert ("Számlázás", "service") in entity_names(con)
    assert con.execute("SELECT count(*) FROM soft_checks").fetchone() == (0,)


def test_a_failed_verify_call_saves_nothing_and_resume_retries_only_the_verify(tmp_path):
    con = site({"/": html("Könyvelés és bérszámfejtés", BODY)})
    adapter = Scripted([PAGE_REPLY], [{"decisions": "nem lista"}, decisions(True, True)])
    first = pipeline_run(con, adapter, tmp_path)
    assert con.execute("SELECT count(*) FROM page_entities").fetchone() == (0,)
    status, error, extraction = con.execute(
        "SELECT status, error, extraction FROM entity_run_pages").fetchone()
    assert status == "verify_error" and error.startswith("schema_mismatch")
    assert json.loads(extraction)["entities"] == PAGE_REPLY["entities"]
    assert first.skipped == {"verify_error": 1}
    second = pipeline_run(con, adapter, tmp_path, resume=True)
    assert [kind for kind, _ in adapter.calls] == ["extract", "verify", "verify"]
    assert second.run_id == first.run_id and second.skipped == {}
    assert (second.pages, second.llm_calls) == (1, 3)
    assert ("Könyvelés", "service") in entity_names(con)


def test_resume_skips_done_pages_and_totals_cover_the_whole_run(tmp_path):
    pages = {f"/{i}/": html(f"P{i}", "<p>A Budapest Coffee Fest idén is lesz</p>")
             for i in range(3)}
    con = site(pages)
    answer = reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "event"))
    adapter = Scripted([answer] * 3)
    first = pipeline_run(con, adapter, tmp_path, limit=2)
    second = pipeline_run(con, adapter, tmp_path, resume=True)
    assert len(adapter.calls) == 3
    assert second.run_id == first.run_id
    assert (second.pages, second.pages_with_entities, second.rows, second.llm_calls) == (
        3, 3, 3, 3)
    third = pipeline_run(con, Scripted(), tmp_path, resume=True)
    assert (third.pages, third.llm_calls) == (3, 3)


def test_the_cost_cap_stops_the_remaining_pages(tmp_path):
    con = site({f"/{i}/": html(f"P{i}", "<p>szöveg itt</p>") for i in range(3)})
    adapter = Scripted([reply()] * 3)
    cost = (1000 * 0.75 + 200 * 3.75) / 1e6
    run = pipeline_run(con, adapter, tmp_path, max_usd=cost * 1.5)
    assert len(adapter.calls) == 2
    assert run.skipped == {"cost_cap_stopped_pages": 1}
    assert con.execute("SELECT status, count(*) FROM entity_run_pages GROUP BY status "
                       "ORDER BY status").fetchall() == [("done", 2), ("stopped", 1)]


def test_a_budget_stop_during_verify_keeps_the_extraction_for_resume(tmp_path):
    con = site({"/": html("Könyvelés és bérszámfejtés", BODY)})
    adapter = Scripted([PAGE_REPLY], [decisions(True, True)])
    client = client_for(con, adapter, tmp_path)

    class Guard:
        """Az első ellenőrző hívás előtt a főkönyv a küszöbre ugrik."""

        def __init__(self):
            self.model = client.model

        def extract(self, *args, **kwargs):
            ledger.append({"model": "gemini-3.8-flash", "cost_usd": 4.5},
                          tmp_path / "ledger.jsonl")
            return client.extract(*args, **kwargs)

    refine = V3Step(con, Steps(), Guard(), KnowledgeBase(Knowledge()), "hu")
    run = run_llm(con, client, refine=refine, clock=lambda: NOON)
    assert run.skipped == {"budget_stopped_pages": 1} and run.pages == 0
    status, extraction = con.execute("SELECT status, extraction FROM entity_run_pages").fetchone()
    assert status == "stopped" and json.loads(extraction)["entities"]


def test_without_save_nothing_is_stored_and_a_later_resume_saves_without_calls(tmp_path):
    con = site({"/": html("Könyvelés és bérszámfejtés", BODY)})
    adapter = Scripted([PAGE_REPLY], [decisions(True, True)])
    dry = pipeline_run(con, adapter, tmp_path, Steps(save=False))
    assert dry.pages == 1 and dry.rows == 0
    assert con.execute("SELECT count(*) FROM page_entities").fetchone() == (0,)
    assert con.execute("SELECT status FROM entity_run_pages").fetchone() == ("extracted",)
    saved = pipeline_run(con, adapter, tmp_path, resume=True)
    assert len(adapter.calls) == 2 and saved.rows == 6
    assert con.execute("SELECT count(*) FROM soft_checks").fetchone() == (5,)


def test_mentionless_llm_entities_are_removed_before_merging(tmp_path):
    con = site({"/": html("P", "<p>A Budapest Coffee Fest idén is lesz</p>")})
    con.execute("INSERT INTO entities (name, type, aliases, source, created_at) "
                "VALUES ('Budapest Coffee Fest', 'org', [], 'llm', ?)", [NOON])
    answer = reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "event"))
    pipeline_run(con, Scripted([answer]), tmp_path)
    assert entity_names(con) == [("Budapest Coffee Fest", "event")]


# ---------------------------------------------------------------------------
# költségbecslés
# ---------------------------------------------------------------------------


def test_estimate_counts_chunks_and_leaves_verify_until_after_extraction(tmp_path):
    con = site({f"/{i}/": html(f"P{i}", "<p>szöveg itt</p>") for i in range(3)})
    day = date(2026, 9, 28)
    guess = estimate_llm(con, CONFIG, "gpt-6-luna", None, "gpt-6-sol", day)
    assert (guess.pages, guess.chunks, guess.verify_pages, guess.verify_pending) == (3, 3, 0, 3)
    assert guess.verify_usd == 0 and guess.total_usd == guess.extract_usd > 0
    assert estimate_llm(con, CONFIG, "gpt-6-luna", None, None, day).verify_pending == 0
    adapter = Scripted([reply()] * 2)
    run_llm(con, client_for(con, adapter, tmp_path), limit=2, clock=lambda: NOON)
    rest = estimate_llm(con, CONFIG, "gemini-3.8-flash", None, None, day, resume=True)
    assert (rest.pages, rest.chunks) == (1, 1)


def test_estimate_prices_verify_from_the_extraction_on_structural_service_pages(tmp_path):
    con = site({"/": html("Könyvelés és bérszámfejtés", BODY),
                "/b/": html("B", "<p>A Budapest Coffee Fest idén is lesz</p>")})
    answer = reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "event"))
    adapter = Scripted([PAGE_REPLY, answer], [{"decisions": "nem lista"}])
    pipeline_run(con, adapter, tmp_path)
    day = date(2026, 9, 28)
    guess = estimate_llm(con, CONFIG, "gemini-3.8-flash", None, "gpt-6-sol", day, resume=True)
    # Csak a verify_error oldal van hátra; a kinyerése megvan, két szerkezeti helyű service-szel.
    assert (guess.pages, guess.chunks, guess.extract_usd) == (1, 0, 0)
    assert (guess.verify_pages, guess.verify_pending) == (1, 0)
    page = page_context(con, 1, page_blocks(con, 1, region="content"), "hu")
    extraction = json.loads(con.execute(
        "SELECT extraction FROM entity_run_pages WHERE page_id = 1").fetchone()[0])
    usage = verify_usage(extraction, page)
    assert usage.output == 200 + 2 * 40
    assert guess.verify_usd == pytest.approx(CONFIG.cost_usd("gpt-6-sol", usage, day))
    assert verify_usage({"entities": [PAGE_REPLY["entities"][2]]}, page) is None


# ---------------------------------------------------------------------------
# tudásbázis az entitásokra
# ---------------------------------------------------------------------------


def test_entities_get_a_wikidata_status_and_errors_stay_unchecked(tmp_path):
    con = site({"/": html("Könyvelés és bérszámfejtés", BODY)})
    pipeline_run(con, Scripted([PAGE_REPLY], [decisions(True, True)]), tmp_path)
    source = Knowledge(wikidata={"Cash-flow": "Q1", "Készletforgás": "Q2", "Számlázó": "Q3"},
                       wikipedia=["Cash-flow", "Számlázó"], failing=["Számlázó"],
                       classes={"Q1": (["financial concept"], "flow of money"),
                                "Q2": (["human"], "a person")})
    run = link_entities(con, KnowledgeBase(source), lambda: NOON, "hu")
    # A service nem kapcsolható; a Készletforgás találata ember: none; a Számlázó hibás.
    assert (run.entities, run.confident, run.probable, run.none, run.errors) == (5, 1, 0, 3, 1)
    assert con.execute("SELECT name, wikidata_id, wikipedia, wikidata_status FROM entities "
                       "WHERE wikidata_status IS NOT NULL ORDER BY name").fetchall() == [
        ("Bérszámfejtés Csomag", None, None, "none"),
        ("Cash-flow", "Q1", "hu:Cash-flow", "confident"),
        ("Készletforgás", None, None, "none"), ("Könyvelés", None, None, "none")]
    source.requests.clear()
    again = link_entities(con, KnowledgeBase(source), lambda: NOON, "hu")
    assert again.entities == 1 and {name for _, name in source.requests} == {"Számlázó"}


# ---------------------------------------------------------------------------
# kimenetek
# ---------------------------------------------------------------------------


def test_entity_table_counts_pages_mentions_places_and_links(tmp_path):
    pages = {"/": html("Könyvelés és bérszámfejtés", BODY),
             "/b/": html("Bérszámfejtés Csomag", "<nav><a href='/'>Cash-flow</a></nav>"
                         "<main><p>A Bérszámfejtés Csomag havonta.</p></main>")}
    con = site(pages)
    adapter = Scripted([PAGE_REPLY, reply(
        mention("b0", "Bérszámfejtés Csomag", "Bérszámfejtés Csomag", "service"),
        mention("b2", "Bérszámfejtés Csomag", "Bérszámfejtés Csomag", "service"))],
        [decisions(True, True), decisions(True)])
    pipeline_run(con, adapter, tmp_path, knowledge=Knowledge())
    con.execute("UPDATE entities SET wikidata_id = 'Q1', wikipedia = 'hu:Cash flow' "
                "WHERE name = 'Cash-flow'")
    rows = {row["entity"]: row for row in entity_table(con)}
    assert rows["Bérszámfejtés Csomag"] | {} == {
        "entity": "Bérszámfejtés Csomag", "type": "service", "subtype": "", "tier": "",
        "flags": "", "source": "llm", "pages": 2, "mentions": 3, "title": 1, "heading": 1,
        "nav": 0, "card": 0, "table_row": 0, "anchor_page": "", "wikidata_qid": "",
        "wikidata_status": "", "wikipedia": ""}
    assert (rows["Cash-flow"]["pages"], rows["Cash-flow"]["mentions"], rows["Cash-flow"]["nav"],
            rows["Cash-flow"]["wikipedia"]) == (1, 2, 0, "https://hu.wikipedia.org/wiki/Cash_flow")
    assert rows["Könyvelés"]["nav"] == 1
    assert next(iter(rows)) == "Bérszámfejtés Csomag"
    path = tmp_path / "out" / "t.csv"
    assert write_entity_table(con, path) == len(rows)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        assert next(csv.reader(handle))[:8] == ["entity", "type", "subtype", "tier", "flags",
                                                "source", "pages", "mentions"]


def test_wikipedia_url():
    assert wikipedia_url("hu:Cash flow") == "https://hu.wikipedia.org/wiki/Cash_flow"
    assert wikipedia_url("en:C++ (language)") == \
        "https://en.wikipedia.org/wiki/C%2B%2B_%28language%29"
    assert wikipedia_url(None) == ""


def test_run_report_has_pages_calls_errors_types_and_the_regression(tmp_path):
    con = site({"/": html("Könyvelés és bérszámfejtés", BODY),
                "/b/": html("B", "<p>A Budapest Coffee Fest idén is lesz</p>")})
    run_rules(con)
    adapter = Scripted([PAGE_REPLY, {"hibás": True}], [decisions(True, False)])
    pipeline_run(con, adapter, tmp_path)
    baseline = connect(":memory:")
    baseline.execute("INSERT INTO entities (entity_id, name, type, aliases, source, created_at) "
                     "VALUES (1, 'X', 'tech', [], 'llm', ?)", [NOON])
    baseline.execute("INSERT INTO page_entities (page_id, entity_id, surface_form, position) "
                     "VALUES (1, 1, 'X', 'schema')")
    text = run_report(con, "pelda", baseline)
    assert "# Entitás-pipeline: pelda" in text
    assert "- oldalak: 2 feldolgozva; állapot szerint: done 1, failed 1" in text
    assert "- extract (gemini-3.8-flash): 2 hívás" in text
    assert "- verify (gemini-3.8-flash): 1 hívás" in text
    assert "- oldalszinten: schema_mismatch 1" in text
    assert "| concept | 2 | 3 | 1 | 0 | 0 |" in text
    assert "kiesett szerkezeti hely nélkül 1, az ellenőrzés vétójával 1" in text
    assert "| tech | 1 | 1 | 1 | 1 |" in text
    assert "| concept | 0 | 2 | 0 | 3 |" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _disk_site(tmp_path, monkeypatch, pages):
    monkeypatch.setattr(connect_module, "DATA_DIR", tmp_path)
    con = site(pages)
    path = tmp_path / "munka" / "pelda.duckdb"
    path.parent.mkdir()
    con.execute(f"ATTACH '{path}' AS disk")
    con.execute("COPY FROM DATABASE memory TO disk")
    con.close()
    return path


def test_cli_estimate_and_the_cap(tmp_path, monkeypatch):
    path = _disk_site(tmp_path, monkeypatch, {"/": html("P", "<p>szöveg itt</p>")})
    result = CliRunner().invoke(app, ["entities", "pelda.hu", "--db", str(path), "--estimate"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("becslés: 1 oldal, 1 kinyerő darab, ~")
    assert "határ 2.00 USD" in result.output
    capped = CliRunner().invoke(app, ["entities", "pelda.hu", "--db", str(path),
                                      "--max-usd", "0.000001", "--no-knowledge"])
    assert capped.exit_code == 2
    assert "a becslés a határ fölött van, a futás nem indul" in capped.output
    con = connect(path)
    assert con.execute("SELECT count(*) FROM llm_calls").fetchone() == (0,)
    assert not db_path("pelda.hu").exists()


def test_cli_rules_only_and_the_report(tmp_path, monkeypatch):
    path = _disk_site(tmp_path, monkeypatch,
                      {f"/{i}/": html(f"Oldal {i} | Példa Kft.", "<p>szöveg</p>")
                       for i in range(3)})
    result = CliRunner().invoke(app, ["entities", "pelda.hu", "--db", str(path), "--no-llm",
                                      "--no-knowledge"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].startswith("entitás-futás #1 (rules, ")
    out = tmp_path / "riport"
    report = CliRunner().invoke(app, ["entity-report", "pelda.hu", "--db", str(path),
                                      "--out", str(out)])
    assert report.exit_code == 0, report.output
    assert (out / "pelda-run.md").read_text(encoding="utf-8").startswith(
        "# Entitás-pipeline: pelda")
    assert "(1 entitás)" in report.output and (out / "pelda-entities.csv").exists()
