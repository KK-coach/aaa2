"""A determinisztikus entitás-kör (aaa2/entities/rules.py): szintetikus oldalakon a
szabályok, a három rögzített készleten a mért eredmény."""
import json

import pytest
import zstandard
from typer.testing import CliRunner

import aaa2.db.connect as connect_module
from aaa2.cli.main import app
from aaa2.db.connect import connect
from aaa2.engine.normalize import UrlPolicy, normalize
from aaa2.engine.parse import parse_page
from aaa2.entities.rules import (
    SCHEMA_TYPES_FILE,
    alias_key,
    find_name,
    is_abbreviation,
    load_schema_types,
    run_rules,
    site_title_names,
    title_endings,
    trivial_anchor,
)
from aaa2.llm.schemas import ENTITY_TYPES
from aaa2.resolver.pages import entity_page_ids, page_roles

SEED = "https://pelda.hu/"
POLICY = UrlPolicy.from_seed(SEED)


def html(title, body, head="", lang="hu"):
    return (f"<html lang='{lang}'><head><title>{title}</title>{head}</head>"
            f"<body>{body}</body></html>")


def stub(*paths, lang="hu"):
    """Csupasz céloldalak, hogy a rájuk mutató link crawlolt célra mutasson."""
    return {path: html(path, "", lang=lang) for path in paths}


def ld(data):
    return f"<script type='application/ld+json'>{json.dumps(data, ensure_ascii=False)}</script>"


def site(pages, languages=("hu",)):
    """Oldalak a crawl sémájában: pages, headings, links (to_page_id-vel), schema_blocks; a
    `site.home_urls` a seed oldal, ha az is az oldalak között van (ahogy a site-profil írja)."""
    con = connect(":memory:")
    con.execute("INSERT INTO site (domain, seed_url, languages) VALUES ('pelda.hu', ?, ?)",
                [SEED, list(languages)])
    compressor = zstandard.ZstdCompressor()
    for path, page_html in pages.items():
        url = normalize(SEED.rstrip("/") + path, POLICY)
        parsed = parse_page(page_html, url, POLICY)
        (page_id,) = con.execute(
            "INSERT INTO pages (url, status, title, h1, lang, main_content, rendered_html) "
            "VALUES (?, 200, ?, ?, ?, ?, ?) RETURNING page_id",
            [url, parsed.title, parsed.h1, parsed.lang, parsed.main_content,
             compressor.compress(page_html.encode())],
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
    seed = normalize(SEED, POLICY)
    con.execute("UPDATE site SET home_urls = ?",
                [[seed] if con.execute("SELECT 1 FROM pages WHERE url = ?", [seed]).fetchone()
                 else []])
    return con


def entity_rows(con):
    """(típus, név, URL, position, szöveg szerinti alak, blokksorszám, kezdőpozíció, count,
    forrás) említésenként és forrásonként."""
    return con.execute(
        "SELECT e.type, e.name, p.url, pe.position, pe.surface_form, b.ordinal, pe.char_start, "
        "s.count, s.source FROM page_entities pe JOIN entities e ON e.entity_id = pe.entity_id "
        "JOIN pages p ON p.page_id = pe.page_id LEFT JOIN blocks b ON b.block_id = pe.block_id "
        "JOIN mention_sources s ON s.mention_id = pe.mention_id "
        "ORDER BY e.type, e.name, p.url, pe.position, b.ordinal"
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
    schema = [(kind, name, surface, ordinal, source) for kind, name, _, position, surface,
              ordinal, _, _, source in entity_rows(con) if position == "schema"]
    assert schema == [                                  # a schema-említésnek nincs blokkja
        ("event", "Pörkölőnap", "Pörkölőnap", None, "schema"),
        ("org", "Példa Kft.", "Példa Kft.", None, "schema"),
        ("person", "Kiss Anna", "Kiss Anna", None, "schema"),
        ("person", "Kovács Béla", "Kovács Béla", None, "schema"),
        ("product", "Kávé", "Kávé", None, "schema"),
        ("service", "SEO & UX", "SEO &amp; UX", None, "schema"),
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
    brand = [(url, position, surface, ordinal, start) for kind, _, url, position, surface,
             ordinal, start, _, _ in entity_rows(con) if kind == "brand"]
    assert brand == [                                   # a title a 0. blokk, a H1 a H2 után
        ("https://pelda.hu/0/", "title", "Példa Kft.", 0, 10),
        ("https://pelda.hu/1/", "h1", "Példa Kft.", 2, 0),
        ("https://pelda.hu/1/", "title", "Példa Kft.", 0, 10),
        ("https://pelda.hu/2/", "title", "Példa Kft.", 0, 10),
        ("https://pelda.hu/3/", "title", "Példa Kft.", 0, 10),
    ]
    assert run.skipped["site_name_not_in_title_or_h1"] == ["Példa Portál"]


def test_site_name_of_an_org_writes_its_title_rows_to_the_org():
    """Ugyanaz a név org-ként (schema) és site-névként (og:site_name, title) egy org entitás; a
    title-sorok oldalanként egyszer."""
    head = (ld({"@type": "Organization", "name": "Példa Kft."})
            + "<meta property='og:site_name' content='példa kft.'>")
    con = site({f"/{i}/": html(f"Oldal {i} | Példa Kft.", "", head=head) for i in range(3)})
    run_rules(con)
    assert con.execute(
        "SELECT e.type, e.name, pe.position, count(*), count(DISTINCT pe.page_id) "
        "FROM page_entities pe JOIN entities e USING (entity_id) GROUP BY ALL ORDER BY ALL"
    ).fetchall() == [("org", "Példa Kft.", "schema", 3, 3), ("org", "Példa Kft.", "title", 3, 3)]


def test_short_site_name_is_an_alias_of_the_org_when_it_abbreviates_it():
    titles = [f"A{i} | Példa Kft." for i in range(4)] + [f"B{i} - PK" for i in range(3)]
    pages = {f"/{i}/": html(title, "", head=ld({"@type": "Organization", "name": "Példa Kft."}))
             for i, title in enumerate(titles)}
    con = site(pages)
    run_rules(con)
    assert con.execute("SELECT type, name, aliases, role FROM entities").fetchall() == [
        ("org", "Példa Kft.", ["PK"], "brand")]
    assert con.execute("SELECT surface_form, count(*) FROM page_entities "
                       "WHERE position = 'title' GROUP BY ALL ORDER BY ALL").fetchall() == [
        ("PK", 3), ("Példa Kft.", 4)]


@pytest.mark.parametrize(("short", "org", "abbreviation"), [
    ("kk", "kk.coach", True),
    ("pk", "pelda kft.", True),
    ("mtm", "materia trattoria moderna", True),
    ("xyz", "pelda kft.", False),
    ("kp", "pelda kft.", False),
    ("pelda", "pelda kft.", False),
])
def test_is_abbreviation(short, org, abbreviation):
    assert is_abbreviation(short, org) is abbreviation


def test_short_site_name_that_is_no_abbreviation_stays_a_brand():
    titles = [f"A{i} | Példa Kft." for i in range(4)] + [f"B{i} - XYZ" for i in range(3)]
    pages = {f"/{i}/": html(title, "", head=ld({"@type": "Organization", "name": "Példa Kft."}))
             for i, title in enumerate(titles)}
    con = site(pages)
    run_rules(con)
    assert con.execute("SELECT type, name, aliases FROM entities ORDER BY type").fetchall() == [
        ("brand", "XYZ", []), ("org", "Példa Kft.", [])]


def test_person_names_in_either_order_are_one_person():
    """Kéttokenes név azonos token-halmazzal egy person; kanonikus a schema ékezetes alakja, a
    nyelve azoké az oldalaké, ahol ez az alak áll."""
    pages = {"/0/": html("P0", "", head=ld({"@type": "Person", "name": "Krisztian Kiss"}),
                         lang="en"),
             "/1/": html("P1", "", head=ld({"@type": "Person", "name": "Kiss Krisztián"}))}
    pages.update({f"/{i}/": html(f"P{i}", "<p><a href='/szerzo/'>Krisztian Kiss</a></p>",
                                 lang="en") for i in range(2, 5)})
    pages.update(stub("/szerzo/", lang="en"))
    con = site(pages)
    run_rules(con)
    assert con.execute("SELECT type, name, aliases, lang FROM entities").fetchall() == [
        ("person", "Kiss Krisztián", ["Krisztian Kiss"], "hu")]
    assert con.execute("SELECT count(*) FROM page_entities").fetchone() == (5,)


@pytest.mark.parametrize(("first", "second", "kind"), [
    ("Kiss Anna Mária", "Anna Kiss Mária", "Person"),
    ("Alfa Béta", "Béta Alfa", "Organization"),
])
def test_other_name_orders_stay_apart(first, second, kind):
    pages = {"/0/": html("P0", "", head=ld({"@type": kind, "name": first})),
             "/1/": html("P1", "", head=ld({"@type": kind, "name": second}))}
    con = site(pages)
    run_rules(con)
    assert con.execute("SELECT count(*) FROM entities").fetchone() == (2,)
    assert con.execute("SELECT entity_id, count(*) FROM page_entities GROUP BY ALL "
                       "ORDER BY ALL").fetchall() == [(1, 1), (2, 1)]


def test_anchor_goes_to_the_org_before_a_same_named_brand():
    head = ld([{"@type": "Organization", "name": "Példa"}, {"@type": "Brand", "name": "Példa"}])
    con = site({**{f"/{i}/": html(f"P{i}", "<a href='/rolunk/'>Példa</a>", head=head)
                   for i in range(3)}, **stub("/rolunk/")})
    run_rules(con)
    assert con.execute("SELECT e.type, pe.position, count(*) FROM page_entities pe "
                       "JOIN entities e USING (entity_id) GROUP BY ALL ORDER BY ALL").fetchall() == [
        ("brand", "schema", 3), ("org", "anchor", 3), ("org", "schema", 3)]


def test_navigational_home_short_and_roman_anchors_drop_out():
    pages = {"/": html("Kezdőlap", "")}
    pages.update({
        f"/{i}/": html(f"P{i}", f"<p><a href='/cikk-{i % 2}/'>Tovább</a></p>"
                                "<a href='/'>Kezdőlap</a><a href='/x/'>AB</a>"
                                "<a href='/y/'>VII</a><a href='/z/'>Mix</a>"
                                f"<a href='/anna-{i % 2}/'>Kiss Anna</a>",
                     head=ld({"@type": "Person", "name": "Kiss Anna"}) if i == 1 else "")
        for i in range(1, 4)})
    pages.update(stub("/cikk-0/", "/cikk-1/", "/x/", "/y/", "/z/", "/anna-0/", "/anna-1/"))
    con = site(pages)
    run = run_rules(con, entity_pages=entity_page_ids(page_roles(con)))
    # A „Mix” egyetlen, segédoldalra mutat: navigációs címke, nem entitás (M2/6, 5. pont).
    assert con.execute("SELECT type, name FROM entities ORDER BY type").fetchall() == [
        ("person", "Kiss Anna")]
    assert con.execute("SELECT count(*) FROM page_entities WHERE position = 'anchor' AND "
                       "entity_id = (SELECT entity_id FROM entities WHERE type = 'person')"
                       ).fetchone() == (3,)
    assert (run.skipped["anchor_texts_navigational"], run.skipped["anchor_to_home"],
            run.skipped["anchor_short"], run.skipped["anchor_roman_numeral"],
            run.skipped["anchor_to_support_page"]) == (1, 3, 3, 3, 1)


def test_anchor_to_an_uncrawled_target_drops_out():
    """Ugyanaz az anchor 3 oldalon: nem crawlolt célra kiesik; crawlolt segédoldalra
    navigációs címke, nem entitás."""
    pages = {f"/{i}/": html(f"P{i}", "<a href='/demo/'>Kattints ide</a>"
                            "<a href='/program/'>Programok</a>") for i in range(3)}
    con = site({**pages, **stub("/program/")})
    run = run_rules(con, entity_pages=entity_page_ids(page_roles(con)))
    assert con.execute("SELECT type, name FROM entities").fetchall() == []
    assert run.skipped["anchor_uncrawled_target"] == 3
    assert run.skipped["anchor_to_support_page"] == 1


def test_anchor_to_an_entity_page_is_left_to_the_site_round():
    """Az entitásoldalra (itt: saját JSON-LD Service) mutató anchor nem szabály-entitás: a
    site-kör köti a céloldal entitásához."""
    service = ld({"@type": "Service", "name": "Mérés és adatarchitektúra",
                  "url": "https://pelda.hu/meres/"})
    pages = {f"/{i}/": html(f"P{i}", "<a href='/meres/'>Mérés</a>") for i in range(3)}
    pages["/meres/"] = html("Mérés", "<p>Szolgáltatás</p>", head=service)
    con = site(pages)
    run = run_rules(con, entity_pages=entity_page_ids(page_roles(con)))
    assert run.skipped["anchor_to_entity_page"] == 1
    assert con.execute("SELECT type, name FROM entities").fetchall() == [
        ("service", "Mérés és adatarchitektúra")]
    assert con.execute("SELECT DISTINCT position FROM page_entities").fetchall() == [
        ("schema",)]


@pytest.mark.parametrize(("anchor", "reason"), [
    ("#", "anchor_without_letter"), ("2", "anchor_without_letter"), ("1.01", "anchor_without_letter"),
    ("KK", "anchor_short"), ("é", "anchor_short"), ("VII", "anchor_roman_numeral"),
    ("MIX", "anchor_roman_numeral"), ("Mix", None), ("vii", None), ("I (page 1)", None),
    ("Next »", None),
])
def test_trivial_anchor(anchor, reason):
    assert trivial_anchor(anchor) == reason


def test_anchor_mention_sits_in_its_block_under_its_headings():
    """Az anchor-említés a linket tartalmazó blokkban, a helyével; a noscript-beli heading nem
    számít a heading-útvonalban."""
    body = ("<h1>Egy</h1><noscript><h2>Rejtett</h2></noscript><h2>Kettő</h2>"
            "<p>Lásd a <a href='/x/'>Közös</a> oldalt</p>")
    org = ld({"@type": "Organization", "name": "Közös"})
    con = site({**{f"/{i}/": html(f"P{i}", body, head=org if i == 0 else "")
                   for i in range(3)}, **stub("/x/")})
    run_rules(con)
    assert con.execute(
        "SELECT DISTINCT b.heading_path, b.text, pe.char_start, pe.char_end, pe.surface_form "
        "FROM page_entities pe JOIN blocks b ON b.block_id = pe.block_id").fetchall() == [
        (["Egy", "Kettő"], "Lásd a Közös oldalt", 7, 12, "Közös")]


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
        **stub("/ritka/"),
    }
    con = site(pages)
    run = run_rules(con, entity_pages=entity_page_ids(page_roles(con)))
    rows = entity_rows(con)
    # A „Szolgáltatások” menüpont 4 oldalon (a /szolgaltatasok/ saját linkje önmagára mutat),
    # egyetlen segédoldalra: navigációs címke, nem entitás.
    assert [kind for kind, *_ in rows if kind == "concept"] == []
    assert run.skipped["anchor_to_support_page"] == 1
    anna = [(url, position, surface, ordinal, start) for kind, name, url, position, surface,
            ordinal, start, _, _ in rows if kind == "person"]
    assert anna == [
        ("https://pelda.hu/", "anchor", "kiss anna", 6, 6),          # "Írta: kiss anna ."
        ("https://pelda.hu/", "schema", "Kiss Anna", None, None),
        ("https://pelda.hu/a/", "anchor", "Kiss Anna", 7, 6),        # "Lásd: Kiss Anna cikkét"
        ("https://pelda.hu/b/", "anchor", "KISS ANNA", 5, 0),
    ]
    assert con.execute("SELECT aliases FROM entities WHERE type = 'person'").fetchone() == (
        ["KISS ANNA", "kiss anna"],)
    # 5 oldalon az "Ugrás a tartalomra", és a /szolgaltatasok/ saját linkje.
    assert run.skipped["anchor_self_link"] == 5 + 1
    # Az "English" 5 magyar oldalon; az angol oldal "Magyar" linkje a kezdőoldalra mutat.
    assert run.skipped["anchor_language_switch"] == 5
    assert run.skipped["anchor_to_home"] == 1
    assert run.skipped["anchor_without_letter"] == 5
    assert run.skipped["anchor_texts_under_min_pages"] == 1


def test_canonical_name_comes_from_the_creating_source():
    """A schema alakja a kanonikus név akkor is, ha az anchorok más alakja gyakoribb; a képes
    link anchorja (az alt-szöveg) nincs a látható szövegben: nincs említése, nem lesz belőle
    entitás."""
    pages = {f"/{i}/": html(f"P{i}", "<p><a href='/anna/'>KISS ANNA</a></p>"
                            "<div><a href='/rolunk/'><img src='l.png' alt='Logó'></a></div>",
                            head=ld({"@type": "Person", "name": "Kiss Anna"}) if i == 0 else "")
             for i in range(3)}
    pages[f"/{0}/"] = pages["/0/"].replace(
        "</head>", ld({"@type": "Organization", "name": "Logó"}) + "</head>")
    con = site({**pages, **stub("/anna/", "/rolunk/")})
    run = run_rules(con)
    assert con.execute("SELECT type, name, aliases FROM entities ORDER BY type").fetchall() == [
        ("org", "Logó", []), ("person", "Kiss Anna", ["KISS ANNA"])]
    assert con.execute("SELECT DISTINCT position FROM page_entities pe JOIN entities e "
                       "USING (entity_id) WHERE e.type = 'org'").fetchall() == [("schema",)]
    assert run.skipped["anchor_not_in_visible_block"] == 3


def test_rerun_drops_vanished_candidates():
    pages = {f"/{i}/": html(f"P{i} | Közös", "<p>szöveg</p>") for i in range(3)}
    con = site(pages)
    run_rules(con)
    assert con.execute("SELECT name FROM entities").fetchall() == [("Közös",)]
    con.execute("UPDATE pages SET title = 'P2' WHERE page_id = 3")
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
    (mention_id,) = con.execute(
        "INSERT INTO page_entities (page_id, entity_id, block_id, char_start, char_end, "
        "surface_form, position) SELECT 1, entity_id, (SELECT block_id FROM blocks WHERE "
        "page_id = 1 AND ordinal = 0), 0, 5, 'Oldal', 'title' FROM entities "
        "WHERE name = 'Példa Kft.' AND type = 'org' RETURNING mention_id").fetchone()
    con.execute("INSERT INTO mention_sources (mention_id, source, run_id) VALUES (?, 'llm', 99)",
                [mention_id])
    second = run_rules(con)
    assert (second.entities, second.rows) == (first.entities, first.rows)
    assert dict(con.execute("SELECT name || '/' || type, entity_id FROM entities "
                            "WHERE source <> 'llm'").fetchall()) == ids
    assert con.execute("SELECT mention_id FROM mention_sources WHERE source = 'llm'"
                       ).fetchall() == [(mention_id,)]
    assert con.execute("SELECT count(*) FROM page_entities WHERE mention_id = ?",
                       [mention_id]).fetchone() == (1,)
    assert con.execute("SELECT count(*) FROM entities WHERE name = 'Csak LLM'").fetchone() == (1,)
    runs = con.execute("SELECT run_id, method, llm_calls, pages, entities, row_count "
                       "FROM entity_runs ORDER BY run_id").fetchall()
    assert runs == [(1, "rules", 0, 5, first.entities, first.rows),
                    (2, "rules", 0, 5, second.entities, second.rows)]


def test_lang_is_the_majority_of_the_entity_pages():
    pages = {f"/{i}/": html(f"P{i} | Közös", "<p>szöveg</p>", lang="en" if i < 3 else "hu-HU")
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
    result = CliRunner().invoke(app, ["entities", "pelda.hu", "--no-llm", "--no-knowledge"])
    assert result.exit_code == 0, result.output
    # A /szolgaltatasok/ és az /en/ nincs crawlolva: a rájuk mutató anchorok kiesnek.
    lines = result.output.splitlines()
    assert lines[0].startswith("entitás-futás #1 (rules, ")
    assert lines[0].endswith("): 1 entitás 3/3 oldalról, 3 sor (title 3), LLM-hívás 0")
    assert lines[-1] == "  brand: 1 entitás, 3 sor"
    assert {"  kimaradt, anchor_self_link: 3", "  kimaradt, anchor_uncrawled_target: 6"} <= set(
        lines)
    status = CliRunner().invoke(app, ["status", "pelda.hu"])
    assert "  entitás-futás #1 (rules, " in status.output


def test_schema_attributes_are_not_entities_and_home_orgs_are_the_site_org():
    """A jobTitle és az areaServed alatti csomópont attribútum; a kezdőoldal URL-jével jelölt
    más nevű szervezet (a publisher) a site-szervezet aliasa, a kanonikus név a site neve."""
    person = ld({"@type": "Person", "name": "Kiss Anna",
                 "jobTitle": {"@type": "Organization", "name": "Tanácsadó"},
                 "areaServed": {"@type": "Place", "name": "Worldwide"}})
    org = ld({"@type": "Organization", "name": "Példa Kft.", "url": "https://pelda.hu/"})
    post = ld({"@type": "BlogPosting", "headline": "Cikk", "publisher": {
        "@type": "Organization", "name": "Jobb marketing", "url": "https://pelda.hu"}})
    pages = {f"/{i}/": html(f"Oldal {i} | Példa Kft.", "<p>Szöveg</p>",
                            head=person + org + (post if i else "")) for i in range(3)}
    con = site({"/": html("Példa Kft.", "<p>Kezdőlap</p>"), **pages})
    run_rules(con)
    assert con.execute("SELECT type, name, aliases FROM entities ORDER BY type").fetchall() == [
        ("org", "Példa Kft.", ["Jobb marketing"]), ("person", "Kiss Anna", [])]


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
    # Schema: Organization kk.coach, szolgáltatások, egy Person (a "Krisztian Kiss" és a "Kiss
    # Krisztián" egy). A site neve (og:site_name, a title "Kk.coach" alakja) és a " - KK"
    # végződés az org-hoz kerül; brand nincs.
    # Anchor-említés minden blokkban, ahol a link áll (a menüben és a láblécben is), csak a
    # talált entitásokhoz; az ajánlatoldalakra mutató menüpontokat (18 szöveg) a site-kör köti,
    # a segédoldalakra mutatók (8) navigációs címkék.
    "kk-coach-crawl": {
        # A „Worldwide” az areaServed értéke (attribútum), a „Digitális marketing coach” a
        # blogposztok kezdőoldal-URL-es publisher-e: a site-szervezet aliasa (M2/6, 10. pont).
        # A soron belüli elemek a renderelt szöveg szerint illeszkednek: a site két oldalán a
        # link a szó közepén áll (`Explor<a>GEO — AI Visibility</a>e GEO`), a renderelt szöveg
        # „ExplorGEO — AI Visibilitye GEO”, ezért ez a két anchor-említés nincs meg.
        "run": (40, 40, 47, 301, {"anchor": 153, "schema": 111, "title": 37}),
        "not_in_block": 0,
        "entities": {("org", "kk.coach"), ("person", "Kiss Krisztián"), ("service", "SEO")},
        "absent": {("brand", "kk.coach"), ("brand", "KK"), ("person", "Krisztian Kiss"),
                   ("concept", "Home"), ("concept", "Főoldal"), ("concept", "Read more"),
                   ("place", "Worldwide"), ("org", "Digitális marketing coach")},
        "aliases": {"kk.coach": ["Digitális marketing coach", "KK", "Kk.coach"],
                    "Kiss Krisztián": ["Kiss Krisztian", "Krisztian Kiss"]},
    },
    # A site-on nincs JSON-LD (élőben is): a brand a title-ből jön. A "Home" és a "Főoldal" a
    # kezdőoldalakra mutat; a "Menu" két célra (/menu/, /it/menu/): navigációs. Az étlap-, a
    # borlap- és az adatvédelmi oldalra mutató menüpontok (5) segédoldalra mutatnak: nem
    # entitások (M2/6, 5. pont).
    "materia-crawl": {
        "run": (14, 14, 1, 14, {"title": 14}),
        "not_in_block": 0,
        "entities": {("brand", "Materia - Trattoria Moderna")},
        "absent": {("concept", "Menu"), ("concept", "Home"), ("concept", "Főoldal"),
                   ("concept", "Borlap"), ("concept", "Carta Vini"), ("concept", "GDPR"),
                   ("concept", "Wine List"), ("concept", "Étlap")},
        "aliases": {},
    },
    # Nincs JSON-LD; a title minden oldalon "Angular Bootstrap". Az "Examples", az "API" és az
    # "Overview" 17 célra, az "ngx-bootstrap" 19 célra mutat: navigációs; a "components" a
    # kezdőoldalra. A komponens-demók linkjei ("Previous", "Start 🏁") a nem crawlolt
    # /ngx-bootstrap-ra mutatnak (256 előfordulás): concept nem marad.
    "ngx-bootstrap-crawl": {
        "run": (69, 69, 1, 69, {"title": 69}),
        "not_in_block": 0,
        "entities": {("brand", "Angular Bootstrap")},
        "absent": {("concept", "ngx-bootstrap"), ("concept", "Examples"), ("concept", "API"),
                   ("concept", "components"), ("concept", "Previous"), ("concept", "Start 🏁")},
        "aliases": {},
        "uncrawled": 256,
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
    assert not EXPECTED[name]["absent"] & names
    for entity, aliases in EXPECTED[name]["aliases"].items():
        assert con.execute("SELECT aliases FROM entities WHERE name = ?", [entity]
                           ).fetchone() == (aliases,)
    assert con.execute("SELECT llm_calls FROM entity_runs").fetchall() == [(0,)]
    # kk.coach és Materia: egyetlen nem crawlolt célra mutató anchor sincs.
    assert run.skipped.get("anchor_uncrawled_target", 0) == EXPECTED[name].get("uncrawled", 0)
    assert run.skipped.get("anchor_not_in_visible_block", 0) == EXPECTED[name]["not_in_block"]
    if name != "kk-coach-crawl":
        assert con.execute("SELECT count(*) FROM mention_sources WHERE source = 'schema'"
                           ).fetchone() == (0,)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_reference_mentions_point_into_their_blocks(reference, name):
    """Minden nem schema említés szöveg szerinti alakja a blokkja szövegének
    [char_start, char_end) szelete; a title a title-blokkban, a H1 egy első szintű headingben áll;
    a schema-említésnek nincs blokkja, és a neve a JSON-LD-ben áll. Egy említés egy forrás."""
    if reference(name) is None:
        pytest.skip(f"nincs felvétel: {name}")
    con, run = reference(name)
    rows = con.execute(
        "SELECT pe.page_id, pe.position, pe.surface_form, pe.char_start, pe.char_end, b.text, "
        "b.kind, b.level FROM page_entities pe LEFT JOIN blocks b ON b.block_id = pe.block_id"
    ).fetchall()
    schema = {}
    for page_id, raw in con.execute("SELECT page_id, json FROM schema_blocks").fetchall():
        schema[page_id] = schema.get(page_id, "") + raw
    wrong = []
    for page_id, position, surface, start, end, text, kind, level in rows:
        if position == "schema":
            ok = text is None and surface in schema.get(page_id, "")
        else:
            ok = text is not None and text[start:end] == surface and (
                (position == "title") == (kind == "title")) and (
                position != "h1" or (kind, level) == ("heading", 1))
        if not ok:
            wrong.append((page_id, position, surface))
    assert (len(rows), wrong) == (run.rows, [])
    assert con.execute("SELECT count(*) FROM mention_sources").fetchone() == (run.rows,)
