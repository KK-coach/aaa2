"""A blokkos LLM-kör (aaa2/entities/extract.py) előre adott válaszokkal: a bemenet, a
szöveg-szerinti-alak ellenőrzés, az említések, az összevonás és a mérőszámok. A hívások a
valódi LLM-kliensen mennek át (llm_calls, főkönyv), csak az adapter hamis."""
import json
import re
import threading
import time
from datetime import UTC, datetime

import pytest
import zstandard
from typer.testing import CliRunner

import aaa2.db.connect as connect_module
from aaa2.cli.main import app
from aaa2.db.connect import connect, db_path
from aaa2.entities.blocks import BLOCK_PROMPT, block_input
from aaa2.entities.dom import build_blocks, page_blocks
from aaa2.entities.extract import (
    Worker,
    estimate_llm,
    input_models,
    page_estimate,
    reusable_pages,
    run_llm,
    surface_offsets,
)
from aaa2.entities.llm import site_line
from aaa2.entities.rules import run_rules
from aaa2.llm import ledger
from aaa2.llm.adapters import Reply, genai_errors
from aaa2.llm.client import BudgetExceeded, LLMClient, Retry, open_clients
from aaa2.llm.config import Usage, load_config
from aaa2.llm.schemas import BlockExtraction
from tests.test_entities_rules import html, ld, site, stub

NOON = datetime(2026, 9, 26, 12, 0, tzinfo=UTC).replace(tzinfo=None)
CONFIG = load_config()


class FakeAdapter:
    """Hívásonként egy előre adott válasz (dict → JSON, vagy kivétel); a kapott promptot és
    bemenetet rögzíti."""

    def __init__(self, replies, provider="gemini"):
        self.config = CONFIG.providers[provider]
        self.replies = list(replies)
        self.calls = []

    def call(self, model, schema, prompt, input):
        self.calls.append((prompt, input))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Reply(text=json.dumps(reply, ensure_ascii=False),
                     usage=Usage(input=1000, output=200))

    def list_models(self):
        return []


def client_for(con, replies, tmp_path, provider="gemini"):
    adapter = FakeAdapter(replies, provider)
    return LLMClient(con, adapter, CONFIG, tmp_path / "ledger.jsonl", lambda: NOON,
                     Retry(sleep=lambda _: None)), adapter


def mention(block, surface, name, kind, subtype=None, description="leírás"):
    return {"block_id": block, "surface_form": surface, "canonical_name": name, "type": kind,
            "subtype": subtype, "description": description}


def reply(*mentions, primary=()):
    return {"primary_entities": list(primary), "entities": list(mentions)}


# b0 title, b1 h1, b2 bekezdés, b3 h2, b4 bekezdés
BODY = ("<h1>Példa Kávézó</h1><p>A Példa Kávézó Budapest belvárosában működik 2016 óta, "
        "specialty kávéval.</p><h2>Csapat</h2><p>A pörkölést Kiss Anna vezeti, aki a "
        "Budapest Coffee Festen is bemutatót tartott.</p>")


def one_page(**extra):
    return site({"/": html("Példa Kávézó | Kávé", BODY, **extra)})


def mentions(con):
    return con.execute(
        "SELECT e.name, b.ordinal, pe.char_start, pe.char_end, pe.surface_form, pe.position "
        "FROM page_entities pe JOIN entities e USING (entity_id) "
        "LEFT JOIN blocks b USING (block_id) ORDER BY b.ordinal, pe.char_start").fetchall()


# ---------------------------------------------------------------------------
# bemenet
# ---------------------------------------------------------------------------


def test_input_is_the_content_blocks_without_known_entities(tmp_path):
    """A prompt a rögzített blokkos prompt; a bemenet a site-leíró mondat és a content-régió
    blokkjai; a determinisztikus kör és más oldalak entitásai, a chrome-régió nem kerül bele."""
    head = ld([{"@type": "Organization", "name": "Példa Kávézó"},
               {"@type": "Person", "name": "Titkos Tivadar"}])
    con = site({"/": html("Példa Kávézó | Kávé", BODY + "<footer><p>Lábléc Lajos</p></footer>",
                          head=head),
                "/masik/": html("Másik", "<p>Rejtett Rudolf oldala</p>")})
    run_rules(con)
    assert "Titkos Tivadar" in [n for (n,) in con.execute("SELECT name FROM entities").fetchall()]
    client, adapter = client_for(con, [reply()] * 2, tmp_path)
    run_llm(con, client)
    prompt, text = adapter.calls[0]
    assert prompt == BLOCK_PROMPT
    assert text == block_input(site_line(con), page_blocks(con, 1, region="content"))
    assert text.startswith("This page belongs to the website pelda.hu, whose home page is "
                           "titled “Példa Kávézó | Kávé”.\n\n[b0]\nPélda Kávézó | Kávé\n\n"
                           "[b1]\nPélda Kávézó\n\n[b2]\nA Példa Kávézó Budapest")
    for sent in adapter.calls:
        assert "Titkos Tivadar" not in "".join(sent)
    assert "Rejtett Rudolf" not in "".join(adapter.calls[0])
    assert "Lábléc Lajos" not in "".join(adapter.calls[0])


def test_blocks_are_built_once_and_kept(tmp_path):
    con = one_page()
    client, _ = client_for(con, [reply(), reply()], tmp_path)
    run_llm(con, client)
    first = con.execute("SELECT block_id, ordinal, kind, level, heading_path FROM blocks "
                        "ORDER BY ordinal").fetchall()
    assert [row[1:] for row in first] == [
        (0, "title", None, []), (1, "heading", 1, ["Példa Kávézó"]),
        (2, "paragraph", None, ["Példa Kávézó"]), (3, "heading", 2, ["Példa Kávézó", "Csapat"]),
        (4, "paragraph", None, ["Példa Kávézó", "Csapat"])]
    run_llm(con, client)
    assert con.execute("SELECT block_id, ordinal, kind, level, heading_path FROM blocks "
                       "ORDER BY ordinal").fetchall() == first


# ---------------------------------------------------------------------------
# a szöveg szerinti alak ellenőrzése
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("surface", "text", "expected"), [
    ("Kiss Anna", "A pörkölést Kiss Anna vezeti", [(12, 21)]),
    ("KISS  anna", "A pörkölést Kiss\nAnna vezeti", [(12, 21)]),
    ("Kávé", "A Kávézó kávéval", []),                                  # szóhatár
    ("Kávé", "Kávé, kávé", [(0, 4), (6, 10)]),
    ("C++", "C++ és C", [(0, 3)]),
    ("", "bármi", []),
])
def test_surface_offsets(surface, text, expected):
    assert surface_offsets(surface, text) == expected


def test_surface_not_in_its_block_is_dropped_and_counted(tmp_path):
    con = one_page()
    client, _ = client_for(con, [reply(
        mention("b2", "Példa Kávézó", "Példa Kávézó", "org"),
        mention("b2", "Kiss Anna", "Kiss Anna", "person"),               # másik blokkban áll
        mention("b9", "Csapat", "Csapat", "concept"),                    # nincs ilyen blokk
        mention("b2", "Kávé", "kávé", "product"),                        # nem szóhatáron
        mention("b4", "KISS  anna", "Kiss Anna", "person"),
    )], tmp_path)
    run = run_llm(con, client)
    assert (run.rows, run.fabricated, run.skipped) == (2, 3, {})
    assert con.execute("SELECT fabricated_count FROM llm_calls").fetchall() == [(3,)]
    assert con.execute("SELECT fabricated FROM entity_runs").fetchall() == [(3,)]
    assert mentions(con) == [("Példa Kávézó", 2, 2, 14, "Példa Kávézó", "body"),
                             ("Kiss Anna", 4, 12, 21, "Kiss Anna", "body")]


# ---------------------------------------------------------------------------
# említések, pozíció, források
# ---------------------------------------------------------------------------


def test_mentions_carry_block_offsets_position_and_source(tmp_path):
    con = one_page()
    client, _ = client_for(con, [reply(
        mention("b0", "Példa Kávézó", "Példa Kávézó", "org", "company", "a kávézó neve"),
        mention("b1", "Példa Kávézó", "Példa Kávézó", "org"),
        mention("b3", "Csapat", "csapat", "concept"),
        mention("b4", "Budapest Coffee Festen", "Budapest Coffee Fest", "event", "festival"),
    )], tmp_path)
    run = run_llm(con, client)
    assert mentions(con) == [
        ("Példa Kávézó", 0, 0, 12, "Példa Kávézó", "title"),
        ("Példa Kávézó", 1, 0, 12, "Példa Kávézó", "h1"),
        ("csapat", 3, 0, 6, "Csapat", "heading"),
        ("Budapest Coffee Fest", 4, 36, 58, "Budapest Coffee Festen", "body")]
    assert con.execute("SELECT description FROM page_entities ORDER BY mention_id").fetchall(
    )[0] == ("a kávézó neve",)
    assert con.execute("SELECT DISTINCT source, run_id, llm_call_id FROM mention_sources"
                       ).fetchall() == [("llm", run.run_id, 1)]
    assert con.execute("SELECT name, subtype FROM entities ORDER BY name").fetchall() == [
        ("Budapest Coffee Fest", "festival"), ("Példa Kávézó", "company"), ("csapat", None)]
    assert run.by_position == {"body": 1, "h1": 1, "heading": 1, "title": 1}


def test_the_same_span_twice_is_one_mention(tmp_path):
    con = one_page()
    client, _ = client_for(con, [reply(
        mention("b4", "Kiss Anna", "Kiss Anna", "person"),
        mention("b4", "kiss anna", "Kiss Anna", "person"))], tmp_path)
    run = run_llm(con, client)
    assert run.rows == 1
    assert con.execute("SELECT count(*) FROM page_entities").fetchone() == (1,)
    assert con.execute("SELECT count(*) FROM mention_sources").fetchone() == (1,)


def test_rule_and_llm_finding_the_same_mention_store_it_once(tmp_path):
    """A szabály (anchor egy schema-entitáshoz) és az LLM ugyanazt az előfordulást találja: egy
    említés, két forrás; a szabály újrafuttatása az llm-entitást schema-forrásúra emeli."""
    event = ld({"@type": "Event", "name": "Budapest Coffee Fest"})
    pages = {f"/{i}/": html(f"P{i}", "<p>Lásd a <a href='/fest/'>Budapest Coffee Fest</a> "
                            "programját</p>", head=event if i == 0 else "") for i in range(3)}
    con = site({**pages, **stub("/fest/")})
    answer = reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "event"))
    client, _ = client_for(con, [answer] * 3 + [reply()], tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT source FROM entities").fetchall() == [("llm",)]
    run_rules(con)
    assert con.execute("SELECT type, source FROM entities").fetchall() == [("event", "schema")]
    assert con.execute("SELECT count(*) FROM page_entities").fetchone() == (4,)
    assert con.execute("SELECT source, count(*) FROM mention_sources GROUP BY ALL ORDER BY ALL"
                       ).fetchall() == [("llm", 3), ("rule", 3), ("schema", 1)]


# ---------------------------------------------------------------------------
# összevonás és típusszavazat
# ---------------------------------------------------------------------------


def test_llm_mentions_merge_into_rule_and_schema_entities(tmp_path):
    """Azonos kulcs vagy alias → a meglévő entitás; a schema-típus és -forrás nem változik;
    person-nél a fordított névsorrend is ugyanaz; az eltérő alak alias."""
    head = ld([{"@type": "Organization", "name": "Példa Kávézó"},
               {"@type": "Person", "name": "Kiss Anna"}])
    con = one_page(head=head)
    run_rules(con)
    before = dict(con.execute("SELECT name, entity_id FROM entities").fetchall())
    client, _ = client_for(con, [reply(
        mention("b2", "Példa Kávézó", "PÉLDA KÁVÉZÓ", "brand"),
        mention("b4", "Kiss Anna", "Anna Kiss", "person"),
        mention("b4", "Budapest Coffee Festen", "Budapest Coffee Fest", "event"),
    )], tmp_path)
    run_llm(con, client)
    entities = con.execute("SELECT entity_id, name, type, source, aliases FROM entities "
                           "ORDER BY entity_id").fetchall()
    assert entities[:2] == [
        (before["Példa Kávézó"], "Példa Kávézó", "org", "schema", ["PÉLDA KÁVÉZÓ"]),
        (before["Kiss Anna"], "Kiss Anna", "person", "schema", ["Anna Kiss"])]
    assert entities[2][1:4] == ("Budapest Coffee Fest", "event", "llm")
    assert con.execute(
        "SELECT pe.entity_id FROM page_entities pe JOIN mention_sources s USING (mention_id) "
        "WHERE s.source = 'llm' ORDER BY pe.entity_id").fetchall() == [
        (before["Példa Kávézó"],), (before["Kiss Anna"],), (entities[2][0],)]


def test_same_type_match_wins_then_the_stronger_source(tmp_path):
    head = ld([{"@type": "Organization", "name": "Példa Kávézó"},
               {"@type": "Brand", "name": "Példa Kávézó"}])
    con = one_page(head=head)
    run_rules(con)
    con.execute("INSERT INTO entities (name, type, source) VALUES ('Példa Kávézó', 'concept', "
                "'llm')")
    ids = dict(con.execute("SELECT type, entity_id FROM entities").fetchall())
    client, _ = client_for(con, [reply(
        mention("b2", "Példa Kávézó", "Példa Kávézó", "brand"),
        mention("b1", "Példa Kávézó", "példa kávézó", "product"))], tmp_path)
    run_llm(con, client)
    assert con.execute(
        "SELECT pe.entity_id, pe.position FROM page_entities pe JOIN mention_sources s "
        "USING (mention_id) WHERE s.source = 'llm' ORDER BY pe.entity_id").fetchall() == [
        (ids["org"], "h1"), (ids["brand"], "body")]


def gadget_site():
    """3 oldal a "Kávégép" nevű JSON-LD Producttal: schemából jött product."""
    product = ld({"@type": "Product", "name": "Kávégép"})
    pages = {f"/{i}/": html(f"P{i}", "<p>A Kávégép használata egyszerű minden reggel</p>",
                            head=product) for i in range(3)}
    return site(pages)


def gadget(kind):
    return reply(mention("b1", "Kávégép", "Kávégép", kind))


def test_llm_type_is_a_vote_not_a_change(tmp_path):
    con = gadget_site()
    run_rules(con)
    client, _ = client_for(con, [gadget("tech")] * 3, tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT type, source, type_votes, type_suggested FROM entities").fetchall(
    ) == [("product", "schema", '{"tech": 3}', "tech")]


def test_votes_accumulate_and_a_tie_keeps_the_current_type(tmp_path):
    con = gadget_site()
    run_rules(con)
    client, _ = client_for(con, [gadget("tech"), gadget("product"), reply()]
                           + [gadget("tech")] * 3, tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT type_votes, type_suggested FROM entities").fetchone() == (
        '{"product": 1, "tech": 1}', "product")
    run_llm(con, client)
    assert con.execute("SELECT type, type_votes, type_suggested FROM entities").fetchone() == (
        "product", '{"product": 1, "tech": 4}', "tech")


def test_same_llm_entity_on_two_pages_is_one_entity(tmp_path):
    con = site({f"/{i}/": html(f"P{i}", "<p>A Budapest Coffee Fest idén is lesz</p>")
                for i in range(2)})
    answer = reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "event"))
    client, _ = client_for(con, [answer, answer], tmp_path)
    run = run_llm(con, client)
    assert (run.entities, run.rows) == (1, 2)
    assert con.execute("SELECT count(*) FROM entities").fetchone() == (1,)


# ---------------------------------------------------------------------------
# újrafuttatás, hibák, mérőszámok
# ---------------------------------------------------------------------------


def test_rerun_replaces_the_mentions_of_the_same_model(tmp_path):
    con = one_page()
    client, _ = client_for(con, [reply(mention("b4", "Kiss Anna", "Kiss Anna", "person")),
                                 reply(mention("b2", "Budapest", "Budapest", "place"))], tmp_path)
    run_llm(con, client)
    run_llm(con, client)
    assert con.execute("SELECT e.name, s.llm_call_id FROM page_entities pe JOIN entities e "
                       "USING (entity_id) JOIN mention_sources s USING (mention_id)"
                       ).fetchall() == [("Budapest", 2)]
    assert con.execute("SELECT name FROM entities").fetchall() == [("Budapest",)]


def test_errors_are_counted_and_the_run_goes_on(tmp_path):
    con = site({f"/{i}/": html(f"P{i}", "<p>A Budapest Coffee Fest idén is lesz</p>")
                for i in range(3)})
    client, _ = client_for(con, [
        reply(mention("b1", "X", "X", "software")),
        genai_errors.ClientError(400, {"error": {"message": "rossz kérés"}}),
        reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "event")),
    ], tmp_path)
    run = run_llm(con, client)
    assert (run.pages, run.llm_calls, run.rows) == (3, 2, 1)
    assert run.skipped == {"schema_mismatch": 1, "call_error": 1}


def test_budget_stop_ends_the_run(tmp_path):
    con = site({f"/{i}/": html(f"P{i}", "<p>szöveg</p>") for i in range(3)})
    client, adapter = client_for(con, [reply()] * 3, tmp_path)
    ledger.append({"model": "gemini-3.8-flash", "cost_usd": 4.5}, tmp_path / "ledger.jsonl")
    run = run_llm(con, client)
    assert (run.pages, run.llm_calls, adapter.calls) == (0, 0, [])
    assert run.skipped == {"budget_stopped_pages": 3}


def test_run_metrics(tmp_path):
    con = site({f"/{i}/": html(f"P{i}", "<p>A Budapest Coffee Fest idén is lesz</p>")
                for i in range(2)})
    answer = reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "event"),
                   mention("b1", "Kitalált Kft.", "Kitalált Kft.", "org"))
    client, _ = client_for(con, [answer, answer], tmp_path)
    run = run_llm(con, client)
    cost = (1000 * 0.75 + 200 * 3.75) / 1e6
    assert (run.model, run.pages, run.llm_calls, run.rows, run.fabricated) == (
        "gemini-3.8-flash", 2, 2, 2, 2)
    assert run.cost_usd == pytest.approx(2 * cost)
    assert con.execute("SELECT method, model, pages, llm_calls, row_count, fabricated, cost_usd "
                       "FROM entity_runs").fetchone() == (
        "llm", "gemini-3.8-flash", 2, 2, 2, 2, pytest.approx(2 * cost))


def test_limit_and_page_ids_pick_the_pages(tmp_path):
    con = site({f"/{i}/": html(f"P{i}", "<p>szöveg itt</p>") for i in range(4)})
    client, adapter = client_for(con, [reply()] * 6, tmp_path)
    assert run_llm(con, client, limit=2).pages == 2
    ids = [page_id for (page_id,) in con.execute(
        "SELECT page_id FROM pages WHERE url LIKE '%/1/' OR url LIKE '%/3/'").fetchall()]
    assert run_llm(con, client, page_ids=ids, limit=5).pages == 2
    assert [call[1].split("\n\n")[1] for call in adapter.calls[2:]] == ["[b0]\nP1", "[b0]\nP3"]
    assert run_llm(con, client, page_ids=[]).pages == 0


def test_status_shows_the_llm_run_per_page(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_module, "DATA_DIR", tmp_path)
    memory = site({f"/{i}/": html(f"P{i}", "<p>A Budapest Coffee Fest idén is lesz</p>")
                   for i in range(2)})
    memory.execute(f"ATTACH '{db_path('pelda.hu')}' AS disk")
    memory.execute("COPY FROM DATABASE memory TO disk")
    memory.close()
    con = connect(db_path("pelda.hu"))
    answer = reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "event"),
                   mention("b1", "Kitalált Kft.", "Kitalált Kft.", "org"))
    client, _ = client_for(con, [answer, answer], tmp_path)
    run_rules(con)
    run_llm(con, client)
    con.close()
    lines = CliRunner().invoke(app, ["status", "pelda.hu"]).output.splitlines()
    llm_line = next(line for line in lines if "(llm gemini-3.8-flash, " in line)
    assert llm_line.endswith("): 1 entitás 2/2 oldalról, 2 sor (body 2), LLM-hívás 2; "
                             "oldalanként 1.00 hívás, 0.0015 USD, 1.00 sor, 1.00 fabrikált")
    assert any(line.startswith("  entitás-futás #1 (rules, ") for line in lines)


@pytest.mark.live
def test_live_three_pages_with_the_pipeline_models(reference_crawl):
    """Élő: a kk.coach-készlet első 3 oldala a `[pipeline]` modelljeivel; minden llm-említés
    szöveg szerinti alakja a blokkja szövegében áll (`pytest -m live -s -k pipeline_models`)."""
    con = reference_crawl("kk-coach-crawl")
    if con is None:
        pytest.skip("nincs felvétel: kk-coach-crawl")
    pipeline = CONFIG.pipeline
    provider = CONFIG.provider_of(pipeline["extraction"])
    clients, skipped = open_clients(con, models={provider: pipeline["extraction"]})
    if provider not in clients:
        pytest.skip(skipped[provider])
    run_rules(con)
    run = run_llm(con, clients[provider], limit=3)
    print(f"\n{run}")
    rows = con.execute(
        "SELECT e.name, pe.surface_form, b.text, pe.char_start, pe.char_end "
        "FROM page_entities pe JOIN entities e USING (entity_id) JOIN blocks b USING (block_id) "
        "JOIN mention_sources s USING (mention_id) WHERE s.source = 'llm'").fetchall()
    assert run.pages == 3 and run.rows > 0
    for name, surface, text, start, end in rows:
        print(f"  {name}  «{surface}»")
        assert text[start:end] == surface


def long_page():
    """Title, H1, három H2-szakasz 30-30 bekezdéssel (95 blokk): két darab, 33 és 63 blokk."""
    body = "<h1>Hosszú</h1>" + "".join(
        f"<h2>Szakasz {s}</h2>" + "".join(f"<p>Bekezdés {s}-{i} a Kávé Kft. termékéről</p>"
                                            for i in range(30)) for s in range(3))
    return site({"/": html("Hosszú oldal", body)})


def test_a_long_page_is_extracted_in_chunks_and_merged(tmp_path):
    con = long_page()
    client, adapter = client_for(con, [
        reply(mention("b3", "Kávé Kft.", "Kávé Kft.", "org"), primary=["Kávé Kft."]),
        reply(mention("b40", "Kávé Kft.", "Kávé Kft.", "org"), primary=["kávé kft."]),
    ], tmp_path)
    run = run_llm(con, client)
    assert [text.count("\n[b") for _, text in adapter.calls] == [33, 63]
    assert all(text.split("\n\n")[1] == "[b0]\nHosszú oldal" for _, text in adapter.calls)
    assert (run.llm_calls, run.rows, run.skipped) == (2, 2, {})
    assert [row[1] for row in mentions(con)] == [3, 40]


def test_a_failed_chunk_keeps_the_others(tmp_path):
    con = long_page()
    client, _ = client_for(con, [
        reply(mention("b3", "Kávé Kft.", "Kávé Kft.", "org")),
        reply(mention("b40", "X", "X", "software")),
    ], tmp_path)
    run = run_llm(con, client)
    assert (run.llm_calls, run.rows, run.skipped) == (
        2, 1, {"chunk_schema_mismatch": 1, "partial_pages": 1})


def page_log(con, run_id):
    status, extraction, refined = con.execute(
        "SELECT status, extraction, refined FROM entity_run_pages WHERE run_id = ?",
        [run_id]).fetchone()
    return status, json.loads(extraction), json.loads(refined) if refined else None


def test_a_page_with_a_failed_chunk_is_partial_and_resume_retries_only_that_chunk(tmp_path):
    con = long_page()
    refined = []

    def refine(extraction, page_id, blocks, lang, prior=None):
        refined.append(len(extraction["entities"]))
        return {**extraction, "checked": True}

    bad = reply(mention("b40", "X", "X", "software"))
    client, adapter = client_for(con, [
        reply(mention("b3", "Kávé Kft.", "Kávé Kft.", "org"), primary=["Kávé Kft."]), bad,
    ], tmp_path)
    run = run_llm(con, client, refine=refine)
    status, extraction, _ = page_log(con, run.run_id)
    assert status == "partial"                                   # nem done
    assert (extraction["failed_chunks"], extraction["chunks"]) == ([1], 2)
    assert run.pages == 1 and run.skipped["partial_pages"] == 1
    assert [row[1] for row in mentions(con)] == [3]              # a sikeres darab mentve
    assert reusable_pages(con, client.model) == {}               # nem kész eredmény
    # a folytatás csak a hibás darabot kéri újra, és a kinyerés utáni lépés újra lefut
    adapter.replies = [reply(mention("b40", "Kávé Kft.", "Kávé Kft.", "org"))]
    adapter.calls.clear()
    again = run_llm(con, client, refine=refine, resume=True)
    assert again.run_id == run.run_id
    assert [text.count("\n[b") for _, text in adapter.calls] == [63]
    status, extraction, record = page_log(con, run.run_id)
    assert status == "done" and "failed_chunks" not in extraction
    assert [e["block_id"] for e in extraction["entities"]] == ["b3", "b40"]
    assert record["checked"] and refined == [1, 2]
    assert [row[1] for row in mentions(con)] == [3, 40]
    assert again.skipped == {"partial_retried": 1}
    assert set(reusable_pages(con, client.model)) == {1}         # most már újrahasználható


def test_a_partial_page_with_changed_blocks_is_extracted_whole_on_resume(tmp_path):
    con = long_page()
    client, adapter = client_for(con, [
        reply(mention("b3", "Kávé Kft.", "Kávé Kft.", "org")),
        reply(mention("b40", "X", "X", "software")),
    ], tmp_path)
    run = run_llm(con, client)
    stored = con.execute("SELECT status, input_hash FROM entity_run_pages").fetchone()
    assert stored[0] == "partial"
    # a tartalom változik, a blokkok és a darabok száma nem (két darab, 33 és 63 blokk)
    body = "<h1>Hosszú</h1>" + "".join(
        f"<h2>Szakasz {s}</h2>" + "".join(f"<p>Bekezdés {s}-{i} a Tea Bt. termékéről</p>"
                                            for i in range(30)) for s in range(3))
    con.execute("UPDATE pages SET rendered_html = ?, main_content = ?",
                [zstandard.ZstdCompressor().compress(html("Hosszú oldal", body).encode()),
                 "a Tea Bt. termékéről"])
    assert build_blocks(con) == 1
    adapter.replies = [reply(mention("b3", "Tea Bt.", "Tea Bt.", "org")),
                       reply(mention("b40", "Tea Bt.", "Tea Bt.", "org"))]
    adapter.calls.clear()
    again = run_llm(con, client, resume=True)
    assert again.run_id == run.run_id
    assert [text.count("\n[b") for _, text in adapter.calls] == [33, 63]    # mindkét darab
    status, extraction, _ = page_log(con, run.run_id)
    assert status == "done" and extraction["chunks"] == 2
    assert [e["canonical_name"] for e in extraction["entities"]] == ["Tea Bt.", "Tea Bt."]
    assert con.execute("SELECT input_hash FROM entity_run_pages").fetchone()[0] != stored[1]
    assert [row[1] for row in mentions(con)] == [3, 40]
    assert again.skipped == {"partial_input_changed": 1}


def test_a_still_failing_chunk_stays_partial_and_a_new_run_extracts_the_whole_page(tmp_path):
    con = long_page()
    bad = reply(mention("b40", "X", "X", "software"))
    client, adapter = client_for(con, [
        reply(mention("b3", "Kávé Kft.", "Kávé Kft.", "org")), bad, bad,
    ], tmp_path)
    run = run_llm(con, client)
    again = run_llm(con, client, resume=True)
    status, extraction, _ = page_log(con, run.run_id)
    assert (status, extraction["failed_chunks"]) == ("partial", [1])
    assert again.skipped == {"chunk_schema_mismatch": 1, "partial_pages": 1,
                             "partial_retried": 1}
    # új futás újrahasználattal: a részleges rekord nem kész eredmény, az egész oldal újra megy
    adapter.replies = [reply(mention("b3", "Kávé Kft.", "Kávé Kft.", "org")),
                       reply(mention("b40", "Kávé Kft.", "Kávé Kft.", "org"))]
    adapter.calls.clear()
    fresh = run_llm(con, client, reuse=True)
    assert fresh.run_id != run.run_id and len(adapter.calls) == 2
    assert page_log(con, fresh.run_id)[0] == "done"
    assert fresh.skipped == {}




# ---------------------------------------------------------------------------
# párhuzamosság
# ---------------------------------------------------------------------------


class PageAdapter(FakeAdapter):
    """Az oldal szövegéből válaszol (a sorrendtől független); a hívások átfedését méri."""

    def __init__(self, delay=0.05):
        super().__init__([])
        self.delay, self.active, self.peak = delay, 0, 0
        self.lock = threading.Lock()

    def call(self, model, schema, prompt, input):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
            self.calls.append((prompt, input))
        number = re.search(r"Termék(\d+)", input).group(1)
        return Reply(text=json.dumps(reply(mention("b1", f"Termék{number}", f"Termék{number}",
                                                   "product"))),
                     usage=Usage(input=1000, output=200))


def product_site():
    return site({f"/{i}/": html(f"P{i}", f"<p>A Termék{i} a legjobb választás.</p>")
                 for i in range(6)})


def parallel_run(tmp_path, workers):
    con = product_site()
    adapter = PageAdapter()
    client = LLMClient(con, adapter, CONFIG, tmp_path / f"ledger{workers}.jsonl", lambda: NOON,
                       Retry(sleep=lambda _: None))
    run = run_llm(con, client, workers=workers, fork=lambda cursor: Worker(client.bind(cursor)))
    rows = con.execute(
        "SELECT e.entity_id, e.name, pe.page_id, pe.char_start FROM page_entities pe "
        "JOIN entities e USING (entity_id) ORDER BY pe.page_id").fetchall()
    return run, rows, adapter


def test_parallel_extraction_matches_the_sequential_one(tmp_path):
    """Több szálon ugyanaz az eredmény (a mentés oldalsorrendben a fő szálon), és a hívások
    ténylegesen átfednek."""
    _, serial_rows, serial_adapter = parallel_run(tmp_path, 1)
    parallel, parallel_rows, parallel_adapter = parallel_run(tmp_path, 3)
    assert serial_rows == parallel_rows and len(parallel_rows) == 6
    assert (parallel.pages, parallel.llm_calls, parallel.rows) == (6, 6, 6)
    assert serial_adapter.peak == 1 and parallel_adapter.peak >= 2


def test_the_cost_cap_reserves_the_pages_in_flight_and_is_never_exceeded(tmp_path):
    """Négy szálon, szimulált hívásokkal: a futás lekönyvelt költsége nem lépi túl a határt,
    mert az új oldal csak akkor indul, ha a lekönyvelt és a lefoglalt költség vele együtt a
    határ alatt marad; a többi oldal `stopped`."""
    con = site({f"/{i}/": html(f"P{i}", f"<p>A Termék{i} a legjobb választás.</p>")
                for i in range(12)})
    class Estimated(PageAdapter):
        """A hívás annyi tokent számláz, amennyit a becslés vár (bemenet / 3, a kimenet a
        kétszerese)."""

        def call(self, model, schema, prompt, input):
            answer = super().call(model, schema, prompt, input)
            tokens = round((len(prompt) + len(input)) / 3.0)
            return Reply(text=answer.text, usage=Usage(input=tokens, output=2 * tokens))

    adapter = Estimated()
    client = LLMClient(con, adapter, CONFIG, tmp_path / "ledger.jsonl", lambda: NOON,
                       Retry(sleep=lambda _: None))
    build_blocks(con)
    estimates = [page_estimate(client, None, site_line(con) or "",
                               page_blocks(con, page_id, region="content"), {}, NOON.date())
                 for page_id in range(1, 13)]
    estimate, actual = max(estimates), min(estimates)
    assert 0 < actual <= estimate < 1.1 * actual   # az oldalak közel egyformák
    cap = 3.5 * estimate                           # egyszerre legfeljebb három oldal fér be
    run = run_llm(con, client, workers=4, max_usd=cap,
                  fork=lambda cursor: Worker(client.bind(cursor)), clock=lambda: NOON)
    (booked,) = con.execute("SELECT coalesce(sum(cost_usd), 0) FROM llm_calls").fetchone()
    assert 0 < booked <= cap and run.cost_usd == pytest.approx(booked)
    assert 2 <= adapter.peak <= 3                  # párhuzamosan fut, de a foglalás korlátoz
    statuses = dict(con.execute("SELECT status, count(*) FROM entity_run_pages GROUP BY status"
                                ).fetchall())
    assert statuses["done"] == len(adapter.calls) >= 3 and statuses["stopped"] >= 1
    assert statuses["done"] + statuses["stopped"] == 12
    assert run.skipped == {"cost_cap_stopped_pages": statuses["stopped"]}
    # a határ nélküli futás mind a 12 oldalt feldolgozza: a megállást a határ okozta
    assert booked + estimate > cap - estimate


def test_budget_guard_counts_the_calls_in_flight(tmp_path):
    """Amíg egy hívás fut, a legnagyobb költsége foglalt: a második hívás, amely vele együtt
    a küszöb fölé vinné, nem indul; a futó hívás után igen."""
    con = one_page()
    release, started = threading.Event(), threading.Event()

    class Blocking(FakeAdapter):
        def call(self, model, schema, prompt, input):
            started.set()
            release.wait(5)
            return super().call(model, schema, prompt, input)

    adapter = Blocking([reply(), reply()])
    ledger_path = tmp_path / "ledger.jsonl"
    client = LLMClient(con, adapter, CONFIG, ledger_path, lambda: NOON,
                       Retry(sleep=lambda _: None))
    worst = client.worst_case_usd("p", "x", NOON.date())
    ledger.append({"model": "gemini-3.8-flash",
                   "cost_usd": client.provider.stop_usd - 1.5 * worst}, ledger_path)
    first = threading.Thread(target=client.bind(con.cursor()).extract,
                             args=(BlockExtraction, "p", "x"), kwargs={"domain": "d"})
    first.start()
    assert started.wait(5)
    with pytest.raises(BudgetExceeded):
        client.extract(BlockExtraction, "p", "x", domain="d")
    release.set()
    first.join(5)
    client.extract(BlockExtraction, "p", "x", domain="d")
    assert len(adapter.calls) == 2


# ---------------------------------------------------------------------------
# inkrementális futás
# ---------------------------------------------------------------------------


def hashed_product_site():
    con = product_site()
    con.execute("UPDATE pages SET raw_html_hash = 'h-' || page_id")
    return con


def incremental_run(con, adapter, tmp_path, **options):
    client = LLMClient(con, adapter, CONFIG, tmp_path / "ledger.jsonl", lambda: NOON,
                       Retry(sleep=lambda _: None))
    return run_llm(con, client, reuse=True, **options)


def test_unchanged_pages_reuse_their_extraction_without_calls(tmp_path):
    con = hashed_product_site()
    adapter = PageAdapter(delay=0)
    first = incremental_run(con, adapter, tmp_path)
    before = mentions(con)
    second = incremental_run(con, adapter, tmp_path)
    assert (first.llm_calls, second.llm_calls, len(adapter.calls)) == (6, 0, 6)
    assert second.skipped == {"reused_extraction": 6} and mentions(con) == before
    assert con.execute("SELECT count(*) FROM entity_run_pages WHERE run_id = ? "
                       "AND input_hash IS NOT NULL AND raw_html_hash IS NOT NULL",
                       [second.run_id]).fetchone() == (6,)


def test_a_changed_page_gets_new_blocks_and_only_it_is_extracted_again(tmp_path):
    con = hashed_product_site()
    adapter = PageAdapter(delay=0)
    incremental_run(con, adapter, tmp_path)
    (page_id,) = con.execute("SELECT page_id FROM pages WHERE url LIKE '%/2/'").fetchone()
    changed = html("P2", "<p>A Termék9 az új választás.</p>")
    con.execute("UPDATE pages SET rendered_html = ?, raw_html_hash = 'uj' WHERE page_id = ?",
                [zstandard.ZstdCompressor().compress(changed.encode()), page_id])
    assert build_blocks(con) == 1
    assert con.execute("SELECT count(*) FROM page_entities WHERE page_id = ?",
                       [page_id]).fetchone() == (0,)          # a régi blokkok említései
    guess = estimate_llm(con, CONFIG, "gemini-3.8-flash", None, NOON.date(),
                         reuse_models=input_models("gemini-3.8-flash", None))
    assert (guess.pages, guess.reused) == (1, 5)
    run = incremental_run(con, adapter, tmp_path)
    assert run.llm_calls == 1 and len(adapter.calls) == 7
    assert ("Termék9", 1) in {(name, ordinal) for name, ordinal, *_ in mentions(con)}
    assert "Termék2" not in {name for name, *_ in mentions(con)}


def test_a_changed_rendered_content_rebuilds_the_blocks_without_a_raw_hash_change(tmp_path):
    """A JavaScripttel betöltött tartalom a nyers HTML hash-ének változása nélkül változik: a
    blokkok a renderelt tartalom hash-e alapján épülnek újra, és csak ez az oldal megy újra."""
    con = hashed_product_site()
    adapter = PageAdapter(delay=0)
    incremental_run(con, adapter, tmp_path)
    (page_id, raw_hash) = con.execute(
        "SELECT page_id, raw_html_hash FROM pages WHERE url LIKE '%/2/'").fetchone()
    assert build_blocks(con) == 0                              # változatlan: nincs újraépítés
    changed = html("P2", "<p>A Termék9 az új választás.</p>")
    con.execute("UPDATE pages SET rendered_html = ?, main_content = ? WHERE page_id = ?",
                [zstandard.ZstdCompressor().compress(changed.encode()),
                 "A Termék9 az új választás.", page_id])
    assert con.execute("SELECT raw_html_hash FROM pages WHERE page_id = ?",
                       [page_id]).fetchone() == (raw_hash,)    # a nyers hash ugyanaz
    assert build_blocks(con) == 1
    assert con.execute("SELECT count(*) FROM page_entities WHERE page_id = ?",
                       [page_id]).fetchone() == (0,)
    assert "Termék9" in con.execute(
        "SELECT string_agg(text, ' ') FROM blocks WHERE page_id = ?", [page_id]).fetchone()[0]
    run = incremental_run(con, adapter, tmp_path)
    assert run.llm_calls == 1 and len(adapter.calls) == 7
    assert "Termék2" not in {name for name, *_ in mentions(con)}
    assert build_blocks(con) == 0


def test_blocks_without_a_content_hash_are_adopted_without_rebuilding():
    con = hashed_product_site()
    build_blocks(con)
    con.execute("UPDATE blocks_built SET content_hash = NULL")   # a 024 előtti adatbázis
    before = con.execute("SELECT block_id FROM blocks ORDER BY block_id").fetchall()
    assert build_blocks(con) == 0
    assert con.execute("SELECT block_id FROM blocks ORDER BY block_id").fetchall() == before
    assert con.execute("SELECT count(*) FROM blocks_built WHERE content_hash IS NOT NULL"
                       ).fetchone() == (6,)


def test_an_older_builder_version_rebuilds_only_the_pages_whose_blocks_differ(tmp_path):
    from aaa2.entities.dom import BLOCKS_VERSION
    con = hashed_product_site()
    adapter = PageAdapter(delay=0)
    incremental_run(con, adapter, tmp_path)
    assert con.execute("SELECT DISTINCT builder_version FROM blocks_built").fetchall() == [
        (BLOCKS_VERSION,)]
    before = mentions(con)
    (page_id,) = con.execute("SELECT page_id FROM pages WHERE url LIKE '%/2/'").fetchone()
    # egy régebbi építő blokkjai: öt oldalon a mostanival azonosak, egy oldalon mások
    con.execute("UPDATE blocks_built SET builder_version = NULL")
    con.execute("UPDATE blocks SET text = text || ' (régi építő)' WHERE page_id = ? AND "
                "ordinal = (SELECT max(ordinal) FROM blocks WHERE page_id = ?)",
                [page_id, page_id])
    unchanged = con.execute("SELECT block_id FROM blocks WHERE page_id <> ? ORDER BY block_id",
                            [page_id]).fetchall()
    assert build_blocks(con) == 1                              # csak az eltérő oldal épül újra
    assert con.execute("SELECT block_id FROM blocks WHERE page_id <> ? ORDER BY block_id",
                       [page_id]).fetchall() == unchanged
    assert con.execute("SELECT count(*) FROM blocks WHERE text LIKE '%régi építő%'"
                       ).fetchone() == (0,)
    assert con.execute("SELECT count(*), min(builder_version) FROM blocks_built").fetchone() \
        == (6, BLOCKS_VERSION)
    # az azonos blokkú oldalak említései megmaradnak, az újraépítetté törlődnek
    assert con.execute("SELECT count(*) FROM page_entities WHERE page_id = ?",
                       [page_id]).fetchone() == (0,)
    assert [m for m in mentions(con)] == [m for m in before if "Termék2" not in m[0]]
    assert build_blocks(con) == 0
    # az újraépített oldal blokkjai itt a kinyeréskoriakkal azonosak (a bemenet-hash egyezik):
    # a tárolt kinyerése hívás nélkül visszakerül
    run = incremental_run(con, adapter, tmp_path)
    assert run.llm_calls == 0 and run.skipped == {"reused_extraction": 6}
    assert mentions(con) == before


def test_legacy_blocks_are_adopted_without_rebuilding():
    con = hashed_product_site()
    build_blocks(con)
    con.execute("DELETE FROM blocks_built")                  # a 016 előtti adatbázis
    before = con.execute("SELECT block_id FROM blocks ORDER BY block_id").fetchall()
    assert build_blocks(con) == 0
    assert con.execute("SELECT block_id FROM blocks ORDER BY block_id").fetchall() == before
    assert con.execute("SELECT count(*) FROM blocks_built").fetchone() == (6,)
