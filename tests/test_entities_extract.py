"""A blokkos LLM-kör (aaa2/entities/extract.py) előre adott válaszokkal: a bemenet, a
szöveg-szerinti-alak ellenőrzés, az említések, az összevonás, az elnevezési hívás és a
mérőszámok. A hívások a valódi LLM-kliensen mennek át (llm_calls, főkönyv), csak az adapter
hamis."""
import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

import aaa2.db.connect as connect_module
from aaa2.cli.main import app
from aaa2.db.connect import connect, db_path
from aaa2.entities.blocks import BLOCK_PROMPT, block_input
from aaa2.entities.dom import page_blocks
from aaa2.entities.extract import run_llm, surface_offsets
from aaa2.entities.llm import site_line
from aaa2.entities.naming import NAMING_PROMPT
from aaa2.entities.rules import run_rules
from aaa2.llm import ledger
from aaa2.llm.adapters import Reply, genai_errors
from aaa2.llm.client import LLMClient, Retry, open_clients
from aaa2.llm.config import Usage, load_config
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
    """A szabály és az LLM ugyanazt az előfordulást találja: egy említés, két forrás; a
    szabály újrafuttatása az llm-entitást szabályira emeli."""
    pages = {f"/{i}/": html(f"P{i}", "<p>Lásd a <a href='/fest/'>Budapest Coffee Fest</a> "
                            "programját</p>") for i in range(3)}
    con = site({**pages, **stub("/fest/")})
    answer = reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "concept"))
    client, _ = client_for(con, [answer] * 3 + [reply()], tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT source FROM entities").fetchall() == [("llm",)]
    run_rules(con)
    assert con.execute("SELECT type, source FROM entities").fetchall() == [("concept", "rule")]
    assert con.execute("SELECT count(*) FROM page_entities").fetchone() == (3,)
    assert con.execute("SELECT source, count(*) FROM mention_sources GROUP BY ALL ORDER BY ALL"
                       ).fetchall() == [("llm", 3), ("rule", 3)]


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
    """3 oldal azonos "Kávégép" anchorral egy crawlolt célra: szabályból jött concept."""
    pages = {f"/{i}/": html(f"P{i}", "<p>A <a href='/gep/'>Kávégép</a> használata egyszerű "
                            "minden reggel</p>") for i in range(3)}
    return site({**pages, **stub("/gep/")})


def gadget(kind):
    return reply(mention("b1", "Kávégép", "Kávégép", kind))


def test_llm_type_is_a_vote_not_a_change(tmp_path):
    con = gadget_site()
    run_rules(con)
    client, _ = client_for(con, [gadget("tech")] * 3 + [reply()], tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT type, source, type_votes, type_suggested FROM entities").fetchall(
    ) == [("concept", "rule", '{"tech": 3}', "tech")]


def test_votes_accumulate_and_a_tie_keeps_the_current_type(tmp_path):
    con = gadget_site()
    run_rules(con)
    client, _ = client_for(con, [gadget("tech"), gadget("concept"), reply(), reply()]
                           + [gadget("product")] * 3 + [reply()], tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT type_votes, type_suggested FROM entities").fetchone() == (
        '{"concept": 1, "tech": 1}', "concept")
    run_llm(con, client)
    assert con.execute("SELECT type, type_votes, type_suggested FROM entities").fetchone() == (
        "concept", '{"concept": 1, "product": 3, "tech": 1}', "product")


def test_same_llm_entity_on_two_pages_is_one_entity(tmp_path):
    con = site({f"/{i}/": html(f"P{i}", "<p>A Budapest Coffee Fest idén is lesz</p>")
                for i in range(2)})
    answer = reply(mention("b1", "Budapest Coffee Fest", "Budapest Coffee Fest", "event"))
    client, _ = client_for(con, [answer, answer], tmp_path)
    run = run_llm(con, client)
    assert (run.entities, run.rows) == (1, 2)
    assert con.execute("SELECT count(*) FROM entities").fetchone() == (1,)


# ---------------------------------------------------------------------------
# elnevezési hívás
# ---------------------------------------------------------------------------


def test_naming_call_renames_and_splits_before_storing(tmp_path):
    """Az elnevezés a birtokos szerkezetet szétbontja, a tapadó főnevet leválasztja; az
    említés a saját szöveg szerinti alakjával kerül tárolásra; mindkét hívás költsége számít."""
    con = site({"/": html("P", "<p>Az Apple ITP-je és a Shopify-integráció fontos.</p>")})
    client, _ = client_for(con, [reply(
        mention("b1", "Apple ITP-je", "Apple ITP", "tech"),
        mention("b1", "Shopify-integráció", "Shopify-integráció", "tech"))], tmp_path)
    naming, naming_adapter = client_for(con, [{"mentions": [
        {"mention_id": "m0", "entities": [
            {"surface_form": "Apple", "canonical_name": "Apple", "type": "org",
             "subtype": "company"},
            {"surface_form": "ITP-je", "canonical_name": "ITP", "type": "tech",
             "subtype": "feature"}]},
        {"mention_id": "m1", "entities": [
            {"surface_form": "Shopify-integráció", "canonical_name": "Shopify", "type": "tech",
             "subtype": "software"}]}]}], tmp_path, provider="openai")
    run = run_llm(con, client, naming_client=naming)
    assert naming_adapter.calls[0][0] == NAMING_PROMPT
    assert mentions(con) == [("Apple", 1, 3, 8, "Apple", "body"),
                             ("ITP", 1, 9, 15, "ITP-je", "body"),
                             ("Shopify", 1, 21, 39, "Shopify-integráció", "body")]
    assert (run.naming_model, run.llm_calls) == ("gpt-6-luna", 2)
    assert con.execute("SELECT purpose, page_id FROM llm_calls ORDER BY call_id").fetchall() == [
        ("extract", 1), ("naming", 1)]
    assert con.execute("SELECT llm_calls FROM entity_runs").fetchone() == (2,)


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
    run = run_llm(con, clients[provider], naming_client=clients[provider], limit=3)
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
    assert (run.llm_calls, run.rows, run.skipped) == (2, 1, {"chunk_schema_mismatch": 1})
