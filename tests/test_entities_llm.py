"""Az LLM-es entitás-kör (aaa2/entities/llm.py) előre adott válaszokkal: a prompt, a
fabrikáció-szűrő, az összevonás és a mérőszámok. A hívások a valódi LLM-kliensen mennek át
(llm_calls, főkönyv), csak az adapter hamis."""
import json
import re
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

import aaa2.db.connect as connect_module
from aaa2.cli.main import app
from aaa2.db.connect import connect, db_path
from aaa2.entities.llm import (
    MAX_INPUT_CHARS,
    PROMPT,
    TYPE_DEFINITIONS,
    check_evidence,
    name_in_evidence,
    normalize_text,
    page_input,
    run_llm,
    site_line,
)
from aaa2.entities.rules import run_rules
from aaa2.llm import ledger
from aaa2.llm.adapters import Reply, genai_errors
from aaa2.llm.client import LLMClient, Retry, open_clients
from aaa2.llm.config import Usage, load_config
from aaa2.llm.schemas import ENTITY_TYPES, ExtractedEntity
from tests.test_entities_rules import html, ld, site, stub

NOON = datetime(2026, 9, 26, 12, 0, tzinfo=UTC).replace(tzinfo=None)
CONFIG = load_config()


class FakeAdapter:
    """Oldalanként egy előre adott válasz (dict → JSON, vagy kivétel); a kapott promptot és
    bemenetet rögzíti."""

    def __init__(self, replies):
        self.config = CONFIG.providers["gemini"]
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


def client_for(con, replies, tmp_path):
    adapter = FakeAdapter(replies)
    return LLMClient(con, adapter, CONFIG, tmp_path / "ledger.jsonl", lambda: NOON,
                     Retry(sleep=lambda _: None)), adapter


def entity(name, kind, evidence, context=""):
    return {"name": name, "type": kind, "description": "leírás", "evidence": evidence,
            "context": context or evidence}


BODY = ("<h1>Példa Kávézó</h1><p>A Példa Kávézó Budapest belvárosában működik 2016 óta, "
        "specialty kávéval.</p><h2>Csapat</h2><p>A pörkölést Kiss Anna vezeti, aki a "
        "Budapest Coffee Festen is bemutatót tartott.</p>")


def one_page(**extra):
    return site({"/": html("Példa Kávézó | Kávé", BODY, **extra)})


# ---------------------------------------------------------------------------
# prompt és bemenet
# ---------------------------------------------------------------------------


def test_prompt_is_constant_and_defines_the_ten_types():
    assert set(TYPE_DEFINITIONS) == set(ENTITY_TYPES)
    for kind, definition in TYPE_DEFINITIONS.items():
        assert f"- {kind}: {definition}" in PROMPT


def test_prompt_never_carries_the_known_entities(tmp_path):
    """A determinisztikus kör és egy másik oldal entitásai nem kerülnek a hívásba: a prompt az
    állandó, a bemenet csak az oldal title-je, headingjei és main contentje."""
    head = ld([{"@type": "Organization", "name": "Példa Kávézó"},
               {"@type": "Person", "name": "Titkos Tivadar"}])
    con = site({"/": html("Példa Kávézó | Kávé", BODY, head=head),
                "/masik/": html("Másik", "<p>Rejtett Rudolf oldala</p>")})
    run_rules(con)
    known = [name for (name,) in con.execute("SELECT name FROM entities").fetchall()]
    assert "Titkos Tivadar" in known
    client, adapter = client_for(con, [{"primary_entity": "x", "entities": []}] * 2, tmp_path)
    run_llm(con, client)
    title, main_content = con.execute(
        "SELECT title, main_content FROM pages WHERE url = 'https://pelda.hu/'").fetchone()
    headings = con.execute("SELECT level, text FROM headings WHERE page_id = 1 ORDER BY ordinal"
                           ).fetchall()
    prompt, text = adapter.calls[0]
    assert prompt == PROMPT
    assert text == page_input(title, headings, main_content, site_line(con))[0]
    assert text.startswith("This page belongs to the website pelda.hu, whose home page is "
                           "titled “Példa Kávézó | Kávé”.\n\nPélda Kávézó | Kávé\n\n")
    for sent in adapter.calls:
        assert "Titkos Tivadar" not in "".join(sent)
    assert "Rejtett Rudolf" not in "".join(adapter.calls[0])


def test_page_input_cuts_the_main_content():
    text, truncated = page_input("T", [(1, "H"), (2, "")], "x" * (MAX_INPUT_CHARS + 5))
    assert text.split("\n\n") == ["T", "H", "x" * MAX_INPUT_CHARS]
    assert truncated


def test_page_input_carries_no_structural_label():
    text, _ = page_input("Angular Bootstrap", [(1, "Accordion"), (3, "Usage")], "Szöveg.")
    assert text == "Angular Bootstrap\n\nAccordion\n\nUsage\n\nSzöveg."
    assert not re.search(r"(?i)\b(title|headings|text|h[1-6])\s*:", text)
    with_site, _ = page_input("T", [], "x", "This page belongs to the website pelda.hu.")
    assert with_site.split("\n\n") == ["This page belongs to the website pelda.hu.", "T", "x"]


def test_site_line_names_the_domain_and_the_home_title():
    con = site({"/": html("Példa   Kávézó\n| Kávé", BODY), "/b/": html("B", "<p>b</p>")})
    assert site_line(con) == ("This page belongs to the website pelda.hu, whose home page is "
                              "titled “Példa Kávézó | Kávé”.")
    con.execute("UPDATE site SET home_urls = ['https://pelda.hu/b/']")
    assert site_line(con).endswith("titled “B”.")
    con.execute("UPDATE site SET home_urls = []")
    assert site_line(con) == "This page belongs to the website pelda.hu."


def test_prompt_v2_asks_for_every_entity_and_the_primary_one():
    assert PROMPT.startswith("List every entity the web page below is about or mentions: "
                             "named things and concepts.")
    for phrase in ("primary_entity", "description", "contains the name",
                   "The titles of this site's own pages are not works"):
        assert phrase in PROMPT
    for gone in ("generic noun", "3 to 15", "named entities mentioned"):
        assert gone not in PROMPT
    assert "a food, a dish, a drink, a wine" in TYPE_DEFINITIONS["product"]
    assert "a company is org, not brand" in TYPE_DEFINITIONS["org"]


# ---------------------------------------------------------------------------
# fabrikáció-szűrő
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("evidence", "reason"), [
    ("a Példa Kávézó Budapest belvárosában", None),
    ("A  PÉLDA kávézó\nbudapest belvárosában", None),
    ("Példa Kávézó | Kávé", None),
    ("Példa Kávézó Szegeden működik", "fabricated"),
    # hosszkorlát nincs: az egyszavas és a teljes mondatnyi idézet is megmarad
    ("Példa Kávézó", None),
    ("Kávézó", None),
    (("A Példa Kávézó Budapest belvárosában működik 2016 óta, specialty kávéval. A pörkölést "
      "Kiss Anna vezeti, aki a Budapest Coffee Festen is bemutatót tartott."), None),
    ("", "fabricated"),
    # a korábbi bemenet címke-előtagja leválik az összevetés előtt
    ("TITLE: Példa Kávézó | Kávé", None),
    ("h2:  a Példa Kávézó Budapest belvárosában", None),
    ("TEXT: H1: a Példa Kávézó Budapest belvárosában", None),
    ("TITLE: Példa Kávézó", None),
    ("TITLE: Példa Kávézó Szegeden működik", "fabricated"),
    ("TITLE: Példa Kávézó | Kávé HEADINGS:", None),
    ("Példa Kávézó | Kávé TEXT: A Példa Kávézó", "fabricated"),
])
def test_check_evidence(evidence, reason):
    """Csak a szó szerinti egyezés számít (a címke nélkül); hosszkorlát nincs."""
    sources = [normalize_text(s) for s in (
        ("A Példa Kávézó Budapest belvárosában működik 2016 óta, specialty kávéval. A pörkölést "
         "Kiss Anna vezeti, aki a Budapest Coffee Festen is bemutatót tartott."),
        "Példa Kávézó | Kávé")]
    assert check_evidence(ExtractedEntity(name="X", type="org", description="d", evidence=evidence,
                                          context="c"), sources) == reason


def test_fabricated_rows_are_dropped_and_counted(tmp_path):
    con = one_page()
    client, _ = client_for(con, [{"primary_entity": "x", "entities": [
        entity("Példa Kávézó", "org", "A PÉLDA KÁVÉZÓ   Budapest belvárosában"),
        entity("Szegedi Kávé", "org", "a Szegedi Kávé Szegeden pörköl"),
        entity("Kiss Anna", "person", "Kiss Anna"),
    ]}], tmp_path)
    run = run_llm(con, client)
    assert (run.rows, run.fabricated, run.skipped) == (2, 1, {})
    assert con.execute("SELECT fabricated_count FROM llm_calls").fetchall() == [(1,)]
    assert con.execute("SELECT fabricated FROM entity_runs").fetchall() == [(1,)]
    assert con.execute("SELECT name FROM entities ORDER BY name").fetchall() == [
        ("Kiss Anna",), ("Példa Kávézó",)]


@pytest.mark.parametrize(("name", "evidence", "inside"), [
    ("Kiss Anna", "A pörkölést Kiss Anna vezeti", True),
    ("Kiss Anna", "A pörkölést KISS  ANNA vezeti", True),
    ("Példa Kávézó", "TITLE: Pelda Kavezo | Kávé", True),
    ("Kiss Anna", "A pörkölést a vezető végzi", False),
])
def test_name_in_evidence_is_a_measure(name, evidence, inside):
    assert name_in_evidence(ExtractedEntity(name=name, type="person", description="d",
                                            evidence=evidence, context="c")) is inside


# ---------------------------------------------------------------------------
# pozíció, szakasz, összevonás
# ---------------------------------------------------------------------------


def test_position_and_section_of_llm_rows(tmp_path):
    con = one_page()
    client, _ = client_for(con, [{"primary_entity": "x", "entities": [
        entity("Példa Kávézó", "org", "Példa Kávézó | Kávé"),
        entity("Kiss Anna", "person", "A pörkölést Kiss Anna vezeti",
               "A pörkölést Kiss Anna vezeti, aki a Budapest Coffee Festen is bemutatót tartott."),
        entity("Budapest", "place", "Kávézó Budapest belvárosában működik"),
        entity("Csapat", "concept", "Csapat"),
    ]}], tmp_path)
    run_llm(con, client)
    rows = con.execute(
        "SELECT e.name, pe.position, pe.section_ordinal, pe.source, pe.llm_call_id "
        "FROM page_entities pe JOIN entities e USING (entity_id) ORDER BY e.name").fetchall()
    assert rows == [("Budapest", "body", 1, "llm", 1), ("Csapat", "heading", 2, "llm", 1),
                    ("Kiss Anna", "body", 2, "llm", 1), ("Példa Kávézó", "title", 0, "llm", 1)]


def test_heading_evidence_gets_the_heading_position(tmp_path):
    con = site({"/": html("P", "<h1>Egy</h1><h2>A Példa Kávézó csapata ma</h2><p>szöveg</p>")})
    client, _ = client_for(con, [{"primary_entity": "x", "entities": [
        entity("Példa Kávézó", "org", "A Példa Kávézó csapata")]}], tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT position, section_ordinal FROM page_entities").fetchall() == [
        ("heading", 2)]


def test_title_label_quote_is_kept(tmp_path):
    """A felvett ngx-kimenet esete: a címke nélkül a title, megtartott sor (hosszkorlát nincs)."""
    quote = ExtractedEntity(name="Angular Bootstrap", type="brand", description="d",
                            evidence="TITLE: Angular Bootstrap", context="c")
    assert check_evidence(quote, [normalize_text("Angular Bootstrap")]) is None
    con = site({"/": html("Angular Bootstrap", "<h1>Accordion</h1><p>szöveg</p>")})
    client, _ = client_for(con, [{"primary_entity": "Accordion", "entities": [
        entity("Angular Bootstrap", "brand", "TITLE: Angular Bootstrap")]}], tmp_path)
    run = run_llm(con, client)
    assert (run.rows, run.fabricated) == (1, 0)
    assert con.execute("SELECT position, evidence FROM page_entities").fetchall() == [
        ("title", "Angular Bootstrap")]


def test_labelled_evidence_is_kept_without_the_label(tmp_path):
    """A címke-előtagos idézet nem fabrikált: címke nélkül tárolva, a helyének pozíciójával."""
    con = site({"/": html("Példa Kávézó Budapest Belváros",
                          "<h1>Egy</h1><h2>A Példa Kávézó csapata ma</h2><p>szöveg</p>")})
    client, _ = client_for(con, [{"primary_entity": "x", "entities": [
        entity("Példa Kávézó", "org", "TITLE: Példa Kávézó Budapest Belváros"),
        entity("Egy", "concept", "H2: A Példa Kávézó csapata")]}], tmp_path)
    run = run_llm(con, client)
    assert run.fabricated == 0
    assert con.execute("SELECT position, evidence FROM page_entities ORDER BY position"
                       ).fetchall() == [("heading", "A Példa Kávézó csapata"),
                                        ("title", "Példa Kávézó Budapest Belváros")]


def test_llm_rows_merge_into_rule_and_schema_entities(tmp_path):
    """Azonos kulcs vagy alias → a meglévő entitás; a schema-típus és -forrás nem változik;
    person-nél a fordított névsorrend is ugyanaz; az eltérő alak alias."""
    head = ld([{"@type": "Organization", "name": "Példa Kávézó"},
               {"@type": "Person", "name": "Kiss Anna"}])
    con = one_page(head=head)
    run_rules(con)
    before = dict(con.execute("SELECT name, entity_id FROM entities").fetchall())
    client, _ = client_for(con, [{"primary_entity": "x", "entities": [
        entity("PÉLDA KÁVÉZÓ", "brand", "A Példa Kávézó Budapest belvárosában"),
        entity("Anna Kiss", "person", "A pörkölést Kiss Anna vezeti"),
        entity("Budapest Coffee Fest", "event", "a Budapest Coffee Festen is bemutatót"),
    ]}], tmp_path)
    run_llm(con, client)
    entities = con.execute("SELECT entity_id, name, type, source, aliases FROM entities "
                           "ORDER BY entity_id").fetchall()
    assert entities[:2] == [
        (before["Példa Kávézó"], "Példa Kávézó", "org", "schema", ["PÉLDA KÁVÉZÓ"]),
        (before["Kiss Anna"], "Kiss Anna", "person", "schema", ["Anna Kiss"])]
    assert entities[2][1:4] == ("Budapest Coffee Fest", "event", "llm")
    assert con.execute("SELECT entity_id, source FROM page_entities WHERE source = 'llm' "
                       "ORDER BY entity_id").fetchall() == [
        (before["Példa Kávézó"], "llm"), (before["Kiss Anna"], "llm"), (entities[2][0], "llm")]


def test_same_type_match_wins_then_the_stronger_source(tmp_path):
    """Az azonos kulcsú entitások közül az LLM típusával egyező; ha nincs ilyen, az erősebb
    forrású (schema a kézzel beírt llm-concept előtt)."""
    head = ld([{"@type": "Organization", "name": "Példa Kávézó"},
               {"@type": "Brand", "name": "Példa Kávézó"}])
    con = one_page(head=head)
    run_rules(con)
    con.execute("INSERT INTO entities (name, type, source) VALUES ('Példa Kávézó', 'concept', "
                "'llm')")
    ids = dict(con.execute("SELECT type, entity_id FROM entities").fetchall())
    client, _ = client_for(con, [{"primary_entity": "x", "entities": [
        entity("Példa Kávézó", "brand", "A Példa Kávézó Budapest belvárosában"),
        entity("példa kávézó", "product", "Példa Kávézó Budapest belvárosában működik"),
    ]}], tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT entity_id, position FROM page_entities WHERE source = 'llm' "
                       "ORDER BY entity_id").fetchall() == [(ids["org"], "body"),
                                                            (ids["brand"], "body")]


def test_repeated_entity_on_a_page_is_one_row_with_a_count(tmp_path):
    con = one_page()
    client, _ = client_for(con, [{"primary_entity": "x", "entities": [
        entity("Kiss Anna", "person", "A pörkölést Kiss Anna vezeti"),
        entity("Kiss Anna", "person", "Kiss Anna vezeti, aki a"),
    ]}], tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT evidence, count FROM page_entities").fetchall() == [
        ("A pörkölést Kiss Anna vezeti", 2)]


def gadget_site():
    """3 oldal azonos "Kávégép" anchorral egy crawlolt célra: szabályból jött concept."""
    pages = {f"/{i}/": html(f"P{i}", "<p>A <a href='/gep/'>Kávégép</a> használata egyszerű "
                            "minden reggel</p>") for i in range(3)}
    return site({**pages, **stub("/gep/")})


def gadget(kind):
    return {"primary_entity": "x", "entities": [entity("Kávégép", kind, "A Kávégép használata egyszerű")]}


def test_llm_type_is_a_vote_not_a_change(tmp_path):
    """A szabályból jött concept, amit az LLM háromszor tech-nek mond, concept marad;
    type_votes = {"tech": 3}, type_suggested = tech."""
    con = gadget_site()
    run_rules(con)
    client, _ = client_for(con, [gadget("tech")] * 3, tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT type, source, type_votes, type_suggested FROM entities").fetchall(
    ) == [("concept", "rule", '{"tech": 3}', "tech")]


def test_votes_accumulate_and_a_tie_keeps_the_current_type(tmp_path):
    con = gadget_site()
    run_rules(con)
    client, _ = client_for(con, [gadget("tech"), gadget("concept"), {"primary_entity": "x", "entities": []}]
                           + [gadget("product")] * 3, tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT type_votes, type_suggested FROM entities").fetchone() == (
        '{"concept": 1, "tech": 1}', "concept")
    run_llm(con, client)
    assert con.execute("SELECT type, type_votes, type_suggested FROM entities").fetchone() == (
        "concept", '{"concept": 1, "product": 3, "tech": 1}', "product")


def test_new_llm_entity_votes_for_its_own_type(tmp_path):
    con = one_page()
    client, _ = client_for(con, [{"primary_entity": "x", "entities": [
        entity("Kiss Anna", "person", "A pörkölést Kiss Anna vezeti")]}], tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT type, type_votes, type_suggested FROM entities").fetchone() == (
        "person", '{"person": 1}', "person")


def test_same_llm_entity_on_two_pages_is_one_entity(tmp_path):
    pages = {f"/{i}/": html(f"P{i}", "<p>A Budapest Coffee Fest idén is lesz</p>")
             for i in range(2)}
    con = site(pages)
    reply = {"primary_entity": "x", "entities": [entity("Budapest Coffee Fest", "event",
                                 "A Budapest Coffee Fest idén")]}
    client, _ = client_for(con, [reply, reply], tmp_path)
    run = run_llm(con, client)
    assert (run.entities, run.rows) == (1, 2)
    assert con.execute("SELECT count(*) FROM entities").fetchone() == (1,)


def test_rule_rerun_upgrades_an_llm_entity_to_rule(tmp_path):
    pages = {f"/{i}/": html(f"P{i}", "<p>Lásd a <a href='/fest/'>Budapest Coffee Fest</a> "
                            "programját</p>") for i in range(3)}
    con = site({**pages, **stub("/fest/")})
    reply = {"primary_entity": "x", "entities": [entity("Budapest Coffee Fest", "concept",
                                 "Lásd a Budapest Coffee Fest programját")]}
    client, _ = client_for(con, [reply] * 3, tmp_path)
    run_llm(con, client)
    assert con.execute("SELECT source FROM entities").fetchall() == [("llm",)]
    run_rules(con)
    assert con.execute("SELECT type, source FROM entities").fetchall() == [("concept", "rule")]
    assert con.execute("SELECT source, count(*) FROM page_entities GROUP BY ALL ORDER BY ALL"
                       ).fetchall() == [("llm", 3), ("rule", 3)]


# ---------------------------------------------------------------------------
# újrafuttatás, hibák, mérőszámok
# ---------------------------------------------------------------------------


def test_rerun_replaces_the_rows_of_the_same_model(tmp_path):
    con = one_page()
    first = {"primary_entity": "x", "entities": [entity("Kiss Anna", "person", "A pörkölést Kiss Anna vezeti")]}
    second = {"primary_entity": "x", "entities": [entity("Budapest", "place", "Kávézó Budapest belvárosában működik")]}
    client, _ = client_for(con, [first, second], tmp_path)
    run_llm(con, client)
    run_llm(con, client)
    assert con.execute("SELECT e.name, pe.llm_call_id FROM page_entities pe "
                       "JOIN entities e USING (entity_id)").fetchall() == [("Budapest", 2)]
    assert con.execute("SELECT name FROM entities").fetchall() == [("Budapest",)]


def test_errors_are_counted_and_the_run_goes_on(tmp_path):
    pages = {f"/{i}/": html(f"P{i}", "<p>A Budapest Coffee Fest idén is lesz</p>")
             for i in range(3)}
    con = site(pages)
    client, _ = client_for(con, [
        {"primary_entity": "x", "entities": [{"name": "X", "type": "software", "evidence": "e", "context": "c"}]},
        genai_errors.ClientError(400, {"error": {"message": "rossz kérés"}}),
        {"primary_entity": "x", "entities": [entity("Budapest Coffee Fest", "event", "A Budapest Coffee Fest idén")]},
    ], tmp_path)
    run = run_llm(con, client)
    assert (run.pages, run.llm_calls, run.rows) == (3, 2, 1)
    assert run.skipped == {"schema_mismatch": 1, "call_error": 1}


def test_budget_stop_ends_the_run(tmp_path):
    pages = {f"/{i}/": html(f"P{i}", "<p>szöveg</p>") for i in range(3)}
    con = site(pages)
    client, adapter = client_for(con, [{"primary_entity": "x", "entities": []}] * 3, tmp_path)
    ledger.append({"model": "gemini-3.8-flash", "cost_usd": 4.5}, tmp_path / "ledger.jsonl")
    run = run_llm(con, client)
    assert (run.pages, run.llm_calls, adapter.calls) == (0, 0, [])
    assert run.skipped == {"budget_stopped_pages": 3}


def test_run_metrics(tmp_path):
    pages = {f"/{i}/": html(f"P{i}", "<p>A Budapest Coffee Fest idén is lesz</p>")
             for i in range(2)}
    con = site(pages)
    reply = {"primary_entity": "x", "entities": [entity("Budapest Coffee Fest", "event", "A Budapest Coffee Fest idén"),
                          entity("Kitalált Kft.", "org", "a Kitalált Kft. szervezi")]}
    client, _ = client_for(con, [reply, reply], tmp_path)
    run = run_llm(con, client)
    cost = (1000 * 0.75 + 200 * 3.75) / 1e6
    assert (run.model, run.pages, run.llm_calls, run.rows, run.fabricated) == (
        "gemini-3.8-flash", 2, 2, 2, 2)
    assert run.cost_usd == pytest.approx(2 * cost)
    assert con.execute("SELECT method, model, pages, llm_calls, row_count, fabricated, cost_usd "
                       "FROM entity_runs").fetchone() == (
        "llm", "gemini-3.8-flash", 2, 2, 2, 2, pytest.approx(2 * cost))


def test_limit_caps_the_pages(tmp_path):
    con = site({f"/{i}/": html(f"P{i}", "<p>szöveg itt</p>") for i in range(4)})
    client, adapter = client_for(con, [{"primary_entity": "x", "entities": []}] * 4, tmp_path)
    assert run_llm(con, client, limit=2).pages == 2
    assert len(adapter.calls) == 2


def test_page_ids_pick_the_pages(tmp_path):
    con = site({f"/{i}/": html(f"P{i}", "<p>szöveg itt</p>") for i in range(4)})
    ids = [page_id for (page_id,) in con.execute(
        "SELECT page_id FROM pages WHERE url LIKE '%/1/' OR url LIKE '%/3/'").fetchall()]
    client, adapter = client_for(con, [{"primary_entity": "x", "entities": []}] * 4, tmp_path)
    assert run_llm(con, client, page_ids=ids, limit=5).pages == 2
    assert [call[1].split("\n\n")[:2] for call in adapter.calls] == [
        ["This page belongs to the website pelda.hu.", "P1"],
        ["This page belongs to the website pelda.hu.", "P3"]]
    assert run_llm(con, client, page_ids=[]).pages == 0


def test_status_shows_the_llm_run_per_page(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_module, "DATA_DIR", tmp_path)
    memory = site({f"/{i}/": html(f"P{i}", "<p>A Budapest Coffee Fest idén is lesz</p>")
                   for i in range(2)})
    memory.execute(f"ATTACH '{db_path('pelda.hu')}' AS disk")
    memory.execute("COPY FROM DATABASE memory TO disk")
    memory.close()
    con = connect(db_path("pelda.hu"))
    reply = {"primary_entity": "x", "entities": [entity("Budapest Coffee Fest", "event", "A Budapest Coffee Fest idén"),
                          entity("Kitalált Kft.", "org", "a Kitalált Kft. szervezi")]}
    client, _ = client_for(con, [reply, reply], tmp_path)
    run_rules(con)
    run_llm(con, client)
    con.close()
    lines = CliRunner().invoke(app, ["status", "pelda.hu"]).output.splitlines()
    llm_line = next(line for line in lines if "(llm gemini-3.8-flash, " in line)
    assert llm_line.endswith("): 1 entitás 2/2 oldalról, 2 sor (body 2), LLM-hívás 2; "
                             "oldalanként 1.00 hívás, 0.0015 USD, 1.00 sor, 1.00 fabrikált")
    assert any(line.startswith("  entitás-futás #1 (rules, ") for line in lines)


@pytest.mark.live
def test_live_three_pages_with_gemini(reference_crawl):
    """Élő: a kk.coach-készlet első 3 oldala Geminivel; minden llm-sor bizonyítéka szó szerint a
    main contentben, a title-ben vagy egy headingben (`pytest -m live -s -k three_pages`)."""
    con = reference_crawl("kk-coach-crawl")
    if con is None:
        pytest.skip("nincs felvétel: kk-coach-crawl")
    clients, skipped = open_clients(con)
    if "gemini" not in clients:
        pytest.skip(skipped["gemini"])
    run_rules(con)
    run = run_llm(con, clients["gemini"], limit=3)
    print(f"\n{run}")
    rows = con.execute(
        "SELECT p.url, e.type, e.name, e.source, pe.position, pe.section_ordinal, pe.evidence, "
        "p.main_content, p.title, (SELECT list(text) FROM headings h WHERE h.page_id = p.page_id) "
        "FROM page_entities pe JOIN entities e USING (entity_id) JOIN pages p USING (page_id) "
        "WHERE pe.source = 'llm' ORDER BY p.url, e.type, e.name").fetchall()
    for url, kind, name, source, position, section, evidence, *_ in rows:
        print(f"  {url[-40:]:40} {kind:8} {source:6} {position:7} {section:3}  {name}  «{evidence}»")
    assert run.pages == 3 and run.llm_calls == 3 and run.rows > 0
    for *_, evidence, main_content, title, headings in rows:
        sources = [normalize_text(s) for s in (main_content, title, *(headings or []))]
        assert any(normalize_text(evidence) in source for source in sources)
