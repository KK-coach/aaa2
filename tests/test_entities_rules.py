"""A determinisztikus entitás-kör (aaa2/engine/entities_rules.py): szintetikus oldalakon a
szabályok, a három rögzített készleten a mért eredmény."""
import json

import pytest
import zstandard
from typer.testing import CliRunner

import aaa2.db.connect as connect_module
from aaa2.cli.main import app
from aaa2.db.connect import connect
from aaa2.engine.entities_rules import (
    SCHEMA_TYPES_FILE,
    alias_key,
    find_name,
    load_schema_types,
    run_rules,
    site_title_names,
    title_endings,
)
from aaa2.engine.normalize import UrlPolicy, normalize
from aaa2.engine.parse import parse_page
from aaa2.llm.schemas import ENTITY_TYPES

SEED = "https://pelda.hu/"
POLICY = UrlPolicy.from_seed(SEED)


def html(title, body, head="", lang="hu"):
    return (f"<html lang='{lang}'><head><title>{title}</title>{head}</head>"
            f"<body>{body}</body></html>")


def ld(data):
    return f"<script type='application/ld+json'>{json.dumps(data, ensure_ascii=False)}</script>"


def site(pages, languages=("hu",)):
    """Oldalak a crawl sémájában: pages, headings, links (to_page_id-vel), schema_blocks."""
    con = connect(":memory:")
    con.execute("INSERT INTO site (domain, seed_url, languages) VALUES ('pelda.hu', ?, ?)",
                [SEED, list(languages)])
    compressor = zstandard.ZstdCompressor()
    for path, page_html in pages.items():
        url = normalize(SEED.rstrip("/") + path, POLICY)
        parsed = parse_page(page_html, url, POLICY)
        (page_id,) = con.execute(
            "INSERT INTO pages (url, status, title, h1, lang, rendered_html) "
            "VALUES (?, 200, ?, ?, ?, ?) RETURNING page_id",
            [url, parsed.title, parsed.h1, parsed.lang, compressor.compress(page_html.encode())],
        ).fetchone()
        for h in parsed.headings:
            con.execute("INSERT INTO headings VALUES (?, ?, ?, ?)",
                        [page_id, h.level, h.text, h.ordinal])
        for link in parsed.links:
            con.execute("INSERT INTO links (from_page_id, to_url, anchor, position, nofollow, "
                        "ordinal) VALUES (?, ?, ?, ?, ?, ?)",
                        [page_id, link.to_url, link.anchor, link.position, link.nofollow,
                         link.ordinal])
        for block in parsed.schema_blocks:
            con.execute("INSERT INTO schema_blocks VALUES (?, ?, ?, ?)",
                        [page_id, block.type, block.json, block.ordinal])
    con.execute("UPDATE links SET to_page_id = pages.page_id FROM pages "
                "WHERE links.to_url = pages.url")
    return con


def entity_rows(con):
    return con.execute(
        "SELECT e.type, e.name, p.url, pe.position, pe.evidence, pe.context, pe.section_ordinal, "
        "pe.count, pe.source FROM page_entities pe JOIN entities e USING (entity_id) "
        "JOIN pages p USING (page_id) ORDER BY e.type, e.name, p.url, pe.position"
    ).fetchall()


# ---------------------------------------------------------------------------
# alias-kulcs, keresés, title-végződések
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "key"), [
    ("Kiss Krisztián", "kiss krisztian"),
    ("GEO — AI  Visibility", "geo ai visibility"),
    ("UX-es-konverzió", "ux es konverzio"),
    ("Measurement &amp; Data", "measurement & data"),
    ("  Kk.coach ", "kk.coach"),
    ("Straße", "strasse"),
])
def test_alias_key(text, key):
    assert alias_key(text) == key


@pytest.mark.parametrize(("text", "key", "found"), [
    ("About - KK", "kk", "KK"),
    ("Szolgáltatások · Kk.coach", "kk", None),
    ("Írj a Kk.coach-nak!", "kk.coach", "Kk.coach"),
    ("Üdv a kk.coach.", "kk.coach", "kk.coach"),
    ("KISS  KRISZTIÁN előadása", "kiss krisztian", "KISS  KRISZTIÁN"),
    ("kkx és kk", "kk", "kk"),
    ("nincs benne", "kk", None),
])
def test_find_name_on_word_boundary(text, key, found):
    span = find_name(text, key)
    assert (text[span[0]:span[1]] if span else None) == found


def test_title_endings():
    assert title_endings("Menu | Materia - Trattoria Moderna |") == [
        "Menu | Materia - Trattoria Moderna", "Materia - Trattoria Moderna", "Trattoria Moderna"]


@pytest.mark.parametrize(("titles", "names"), [
    ([f"Oldal {i} · Kk.coach" for i in range(5)] + [f"Cikk {i} - KK" for i in range(4)],
     ["Kk.coach", "KK"]),
    (["Materia - Trattoria Moderna |"] * 2
     + [f"{p} | Materia - Trattoria Moderna" for p in ("Menu", "Wine List", "Thank You")],
     ["Materia - Trattoria Moderna"]),
    (["Angular Bootstrap"] * 5, ["Angular Bootstrap"]),
    ([f"A{i} · Kk.coach" for i in range(8)] + [f"B{i} — Solutions · Kk.coach" for i in range(4)],
     ["Kk.coach"]),
    ([f"Oldal {i}" for i in range(8)] + ["X | Ritka", "Y | Ritka"], []),
    ([f"Oldal {i}" for i in range(16)] + [f"{i} | Kevés" for i in range(4)], []),
])
def test_site_title_names(titles, names):
    assert site_title_names(titles) == names


def test_schema_types_config(tmp_path):
    assert set(load_schema_types().values()) <= set(ENTITY_TYPES)
    bad = tmp_path / "schema_types.toml"
    bad.write_text(SCHEMA_TYPES_FILE.read_text(encoding="utf-8") + 'Thing = "dolog"\n',
                   encoding="utf-8")
    with pytest.raises(ValueError, match="dolog"):
        load_schema_types(bad)


# ---------------------------------------------------------------------------
# a szabályok szintetikus oldalakon
# ---------------------------------------------------------------------------


GRAPH = {"@context": "https://schema.org", "@graph": [
    {"@type": "Organization", "name": "Példa Kft."},
    {"@type": "WebPage", "name": "Kezdőlap"},
    {"@type": "BlogPosting", "headline": "Cikk",
     "author": {"@type": "Person", "name": "Kiss Anna"}},
    {"@type": ["Product", "Thing"], "name": ["Kávé", "Coffee"]},
    {"@type": "http://schema.org/Event", "name": {"@value": "Pörkölőnap"}},
    {"@type": "Service", "name": "SEO &amp; UX"},
    {"@type": "Person", "@id": "#anna"},
]}


def test_schema_nodes_become_typed_entities():
    con = site({"/": html("Kezdőlap | Példa Kft.", "<h1>Üdv</h1><h2>Rólunk</h2>"
                          + ld({"@type": "Person", "name": "Kovács Béla"}), head=ld(GRAPH))})
    run = run_rules(con)
    schema = [(kind, name, evidence, section, source) for kind, name, _, position, evidence,
              _, section, _, source in entity_rows(con) if position == "schema"]
    assert schema == [
        ("event", "Pörkölőnap", "Pörkölőnap", 0, "schema"),
        ("org", "Példa Kft.", "Példa Kft.", 0, "schema"),
        ("person", "Kiss Anna", "Kiss Anna", 0, "schema"),
        ("person", "Kovács Béla", "Kovács Béla", 2, "schema"),
        ("product", "Kávé", "Kávé", 0, "schema"),
        ("service", "SEO & UX", "SEO &amp; UX", 0, "schema"),
    ]
    assert run.skipped["unmapped_schema_types"] == {"WebPage": 1, "BlogPosting": 1}
    assert run.skipped["schema_without_name"] == {"Person": 1}
    (context,) = con.execute("SELECT context FROM page_entities pe JOIN entities e "
                             "USING (entity_id) WHERE e.name = 'Kiss Anna'").fetchone()
    assert json.loads(context) == {"@type": "Person", "name": "Kiss Anna"}


def test_brand_from_titles_and_h1():
    pages = {f"/{i}/": html(f"Oldal {i} | Példa Kft.", "<h2>Bevezető</h2><h1>Példa Kft.</h1>"
                            if i == 1 else "<h1>Más</h1>",
                            head="<meta property='og:site_name' content='Példa Portál'>")
             for i in range(4)}
    con = site(pages)
    run = run_rules(con)
    brand = [(url, position, evidence, context, section) for kind, _, url, position, evidence,
             context, section, _, _ in entity_rows(con) if kind == "brand"]
    assert brand == [
        ("https://pelda.hu/0/", "title", "Példa Kft.", "Oldal 0 | Példa Kft.", 0),
        ("https://pelda.hu/1/", "h1", "Példa Kft.", "Példa Kft.", 2),
        ("https://pelda.hu/1/", "title", "Példa Kft.", "Oldal 1 | Példa Kft.", 0),
        ("https://pelda.hu/2/", "title", "Példa Kft.", "Oldal 2 | Példa Kft.", 0),
        ("https://pelda.hu/3/", "title", "Példa Kft.", "Oldal 3 | Példa Kft.", 0),
    ]
    assert run.skipped["brand_not_in_title_or_h1"] == ["Példa Portál"]


def test_same_brand_from_two_sources_gives_one_row_per_page():
    head = (ld({"@type": "Organization", "name": "Példa Kft."})
            + "<meta property='og:site_name' content='példa kft.'>")
    con = site({f"/{i}/": html(f"Oldal {i} | Példa Kft.", "", head=head) for i in range(3)})
    run_rules(con)
    assert con.execute("SELECT count(*), count(DISTINCT page_id) FROM page_entities pe "
                       "JOIN entities e USING (entity_id) WHERE e.type = 'brand'").fetchone() == (
        3, 3)


def test_section_ordinals_match_the_headings_table():
    """A noscript-beli heading nem számít, ahogy a headings táblában sem."""
    body = ("<h1>Egy</h1><noscript><h2>Rejtett</h2></noscript><h2>Kettő</h2>"
            "<p><a href='/x/'>Közös</a></p>")
    con = site({f"/{i}/": html(f"P{i}", body) for i in range(3)})
    run_rules(con)
    assert con.execute(
        "SELECT DISTINCT pe.section_ordinal, h.text FROM page_entities pe JOIN headings h "
        "ON h.page_id = pe.page_id AND h.ordinal = pe.section_ordinal").fetchall() == [
        (2, "Kettő")]


def nav(extra=""):
    return ("<nav><ul><li><a href='/szolgaltatasok/'>Szolgáltatások</a></li>"
            "<li><a href='/en/'>English</a></li><li><a href='#'>#</a></li></ul></nav>"
            "<a href='#tartalom'>Ugrás a tartalomra</a>" + extra)


def test_anchor_candidates_with_structural_filters():
    person = ld({"@type": "Person", "name": "Kiss Anna"})
    pages = {
        "/": html("Kezdőlap", nav("<h2>Hírek</h2><p>Írta: <a href='/anna/'>kiss anna</a>.</p>"
                                  "<a href='/ritka/'>Ritka</a>"), head=person),
        "/a/": html("A", nav("<h2>Egy</h2><h3>Kettő</h3><p>Lásd: <a href='/anna/'>Kiss Anna"
                             "</a> cikkét</p><a href='/ritka/'>Ritka</a>")),
        "/b/": html("B", nav("<p><a href='/anna/'>KISS ANNA</a></p>")),
        "/szolgaltatasok/": html("Szolgáltatások", nav()),
        "/anna/": html("Anna", nav()),
        "/en/": html("English", "<a href='/'>Magyar</a>", lang="en"),
    }
    con = site(pages)
    run = run_rules(con)
    rows = entity_rows(con)
    concept = [(url, evidence, context, count) for kind, name, url, position, evidence, context,
               _, count, _ in rows if kind == "concept"]
    # A /szolgaltatasok/ oldalon a link önmagára mutat: 4 oldal marad.
    assert concept == [(f"https://pelda.hu{p}", "Szolgáltatások", "Szolgáltatások", 1)
                       for p in ("/", "/a/", "/anna/", "/b/")]
    anna = [(url, position, evidence, context, section) for kind, name, url, position, evidence,
            context, section, _, _ in rows if kind == "person"]
    assert anna == [
        ("https://pelda.hu/", "anchor", "kiss anna", "Írta: kiss anna .", 1),
        ("https://pelda.hu/", "schema", "Kiss Anna", '{"@type":"Person","name":"Kiss Anna"}', 0),
        ("https://pelda.hu/a/", "anchor", "Kiss Anna", "Lásd: Kiss Anna cikkét", 2),
        ("https://pelda.hu/b/", "anchor", "KISS ANNA", "KISS ANNA", 0),
    ]
    assert con.execute("SELECT aliases FROM entities WHERE type = 'person'").fetchone() == (
        ["KISS ANNA", "kiss anna"],)
    # 5 oldalon az "Ugrás a tartalomra", és a /szolgaltatasok/ saját linkje.
    assert run.skipped["anchor_self_link"] == 5 + 1
    assert run.skipped["anchor_language_switch"] == 5 + 1
    assert run.skipped["anchor_without_letter"] == 5
    assert run.skipped["anchor_texts_under_min_pages"] == 1


def test_canonical_name_comes_from_the_creating_source():
    """A schema alakja a kanonikus név akkor is, ha az anchorok más alakja gyakoribb; a képes
    link contextje az anchor maga."""
    pages = {f"/{i}/": html(f"P{i}", "<p><a href='/anna/'>KISS ANNA</a></p>"
                            "<div><a href='/'><img src='l.png' alt='Logó'></a></div>",
                            head=ld({"@type": "Person", "name": "Kiss Anna"}) if i == 0 else "")
             for i in range(3)}
    con = site(pages)
    run_rules(con)
    assert con.execute("SELECT type, name, aliases FROM entities ORDER BY type").fetchall() == [
        ("concept", "Logó", []), ("person", "Kiss Anna", ["KISS ANNA"])]
    assert {context for (context,) in con.execute(
        "SELECT context FROM page_entities pe JOIN entities e USING (entity_id) "
        "WHERE e.name = 'Logó'").fetchall()} == {"Logó"}


def test_rerun_drops_vanished_candidates():
    pages = {f"/{i}/": html(f"P{i}", "<a href='/x/'>Közös</a>") for i in range(3)}
    con = site(pages)
    run_rules(con)
    assert con.execute("SELECT name FROM entities").fetchall() == [("Közös",)]
    con.execute("DELETE FROM links WHERE from_page_id = 3")
    run = run_rules(con)
    assert (run.entities, con.execute("SELECT count(*) FROM entities").fetchone()) == (0, (0,))


def test_rerun_keeps_ids_and_foreign_rows():
    pages = {f"/{i}/": html(f"Oldal {i} | Példa Kft.", nav(), head=ld(GRAPH)) for i in range(3)}
    pages.update({"/szolgaltatasok/": html("Sz | Példa Kft.", nav()),
                  "/en/": html("En", "", lang="en")})
    con = site(pages)
    first = run_rules(con)
    ids = dict(con.execute("SELECT name || '/' || type, entity_id FROM entities").fetchall())
    con.execute("INSERT INTO entities (name, type, source) VALUES ('Csak LLM', 'concept', 'llm')")
    con.execute("INSERT INTO page_entities (page_id, entity_id, position, evidence, source) "
                "SELECT 1, entity_id, 'body', 'Példa', 'llm' FROM entities "
                "WHERE name = 'Példa Kft.' AND type = 'org'")
    second = run_rules(con)
    assert (second.entities, second.rows) == (first.entities, first.rows)
    assert dict(con.execute("SELECT name || '/' || type, entity_id FROM entities "
                            "WHERE source <> 'llm'").fetchall()) == ids
    assert con.execute("SELECT count(*) FROM page_entities WHERE source = 'llm'").fetchone() == (
        1,)
    assert con.execute("SELECT count(*) FROM entities WHERE name = 'Csak LLM'").fetchone() == (1,)
    runs = con.execute("SELECT run_id, method, llm_calls, pages, entities, row_count "
                       "FROM entity_runs ORDER BY run_id").fetchall()
    assert runs == [(1, "rules", 0, 5, first.entities, first.rows),
                    (2, "rules", 0, 5, second.entities, second.rows)]


def test_lang_is_the_majority_of_the_entity_pages():
    # A cél nincs crawlolva, a nyelve ismeretlen: a link nem nyelvváltó.
    pages = {f"/{i}/": html(f"P{i}", "<a href='/x/'>Közös</a>", lang="en" if i < 3 else "hu-HU")
             for i in range(4)}
    con = site(pages, languages=("hu",))
    run_rules(con)
    assert con.execute("SELECT name, lang FROM entities").fetchall() == [("Közös", "en")]


def test_cli_entities_and_status(tmp_path, monkeypatch):
    monkeypatch.setattr(connect_module, "DATA_DIR", tmp_path)
    con = site({f"/{i}/": html(f"Oldal {i} | Példa Kft.", nav()) for i in range(3)})
    con.execute(f"ATTACH '{connect_module.db_path('pelda.hu')}' AS disk")
    con.execute("COPY FROM DATABASE memory TO disk")
    con.close()
    result = CliRunner().invoke(app, ["entities", "pelda.hu"])
    assert result.exit_code == 0, result.output
    # A nem crawlolt /en/ nyelve ismeretlen, így az "English" is jelölt.
    assert result.output.splitlines()[:3] == [
        ("entitás-futás #1 (rules): 3 entitás 3/3 oldalról, 9 sor (anchor 6, title 3), "
         "LLM-hívás 0"),
        "  concept: 2 entitás, 6 sor",
        "  brand: 1 entitás, 3 sor",
    ]
    status = CliRunner().invoke(app, ["status", "pelda.hu"])
    assert "  entitás-futás #1 (rules, " in status.output


# ---------------------------------------------------------------------------
# a három rögzített készlet
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reference(reference_crawl):
    """Név → (a visszajátszott crawl kapcsolata, az entitás-futása); None, ha nincs felvétel.
    Készletenként egy futás."""
    runs = {}

    def get(name):
        if name not in runs:
            con = reference_crawl(name)
            runs[name] = None if con is None else (con, run_rules(con))
        return runs[name]

    return get


EXPECTED = {
    # Schema: Organization kk.coach, két Person (a keleti és a nyugati névsorrend két entitás),
    # szolgáltatások; brand a title-ből: kk.coach (az og:site_name és a title "Kk.coach"
    # alakja) és KK (a " - KK" végződés 18 oldalon).
    "kk-coach-crawl": {
        "run": (40, 40, 82, 676, {"anchor": 522, "schema": 117, "title": 37}),
        "entities": {("org", "kk.coach"), ("person", "Krisztian Kiss"),
                     ("person", "Kiss Krisztián"), ("brand", "kk.coach"), ("brand", "KK"),
                     ("place", "Worldwide"), ("service", "SEO")},
    },
    # Nincs JSON-LD a felvételben: a brand a title-ből jön, a többi anchor-jelölt.
    "materia-crawl": {
        "run": (14, 14, 9, 63, {"anchor": 49, "title": 14}),
        "entities": {("brand", "Materia - Trattoria Moderna"), ("concept", "Menu"),
                     ("concept", "Borlap"), ("concept", "Carta Vini"), ("concept", "GDPR")},
    },
    # Nincs JSON-LD; a title minden oldalon "Angular Bootstrap".
    "ngx-bootstrap-crawl": {
        "run": (69, 69, 41, 565, {"anchor": 496, "title": 69}),
        "entities": {("brand", "Angular Bootstrap"), ("concept", "ngx-bootstrap"),
                     ("concept", "Examples")},
    },
}


@pytest.mark.parametrize("name", list(EXPECTED))
def test_reference_entities(reference, name):
    if reference(name) is None:
        pytest.skip(f"nincs felvétel: {name}")
    con, run = reference(name)
    assert (run.pages, run.pages_with_entities, run.entities, run.rows, run.by_position) == (
        EXPECTED[name]["run"])
    names = set(con.execute("SELECT type, name FROM entities").fetchall())
    assert EXPECTED[name]["entities"] <= names
    assert con.execute("SELECT llm_calls FROM entity_runs").fetchall() == [(0,)]
    if name != "kk-coach-crawl":
        assert con.execute("SELECT count(*) FROM page_entities WHERE source = 'schema'"
                           ).fetchone() == (0,)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_reference_rows_carry_verbatim_evidence(reference, name):
    """Minden sornak van bizonyítéka, contextje és section_ordinalja, és a bizonyíték szó
    szerint ott van, ahonnan jött."""
    if reference(name) is None:
        pytest.skip(f"nincs felvétel: {name}")
    con, _ = reference(name)
    rows = con.execute(
        "SELECT pe.page_id, pe.position, pe.evidence, pe.context, pe.section_ordinal, p.title, "
        "p.h1 FROM page_entities pe JOIN pages p USING (page_id)").fetchall()
    anchors = set(con.execute("SELECT from_page_id, anchor FROM links").fetchall())
    blocks = {}
    for page_id, raw in con.execute("SELECT page_id, json FROM schema_blocks").fetchall():
        blocks[page_id] = blocks.get(page_id, "") + raw
    wrong = []
    for page_id, position, evidence, context, section, title, h1 in rows:
        source = {"title": title, "h1": h1, "schema": blocks.get(page_id, "")}.get(position)
        ok = ((page_id, evidence) in anchors if position == "anchor"
              else bool(source) and evidence in source)
        if not (evidence and context and section is not None and ok):
            wrong.append((page_id, position, evidence))
    assert (len(rows), wrong) == (EXPECTED[name]["run"][3], [])
