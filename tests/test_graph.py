"""Az entitásgráf (aaa2/functions/graph.py) szintetikus site-okon, hálózat és LLM nélkül:
oldal-csomópontok és segédoldalak, a fő entitás a bizonyítékaival, élek, súlyok, kivonatok."""
import csv
import json
import math

import pytest

from aaa2.entities.rules import run_rules
from aaa2.functions.findings import build_findings, export_views, site_views
from aaa2.functions.graph import (
    Candidate,
    build_graph,
    export_csv,
    is_a_reason,
    load_graph_config,
    row_label,
    url_has_word,
)
from aaa2.resolver import overrides
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, ld, site
from tests.test_entities_shop import shop_site
from tests.test_entities_site import NOON, business_site, llm_entity


@pytest.fixture
def shop_types(tmp_path, monkeypatch):
    """A webshop site-fájlja (`[page_types]`) a site-körnek."""
    (tmp_path / "pelda.hu.toml").write_text(
        "[page_types]\ncategory = ['/sct/']\nbrand_category = ['/spl/']\n", encoding="utf-8")
    monkeypatch.setattr(overrides, "SITES_DIR", tmp_path)


def graph_of(con, **options):
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    return build_graph(con, **options)


def chosen(con):
    return [(url, name, role, confidence, sorted(json.loads(evidence)))
            for url, name, role, confidence, evidence in con.execute(
                "SELECT p.url, e.name, m.role, m.confidence, m.evidence FROM page_main_entity m "
                "JOIN page_nodes p USING (page_id) JOIN entities e USING (entity_id) "
                "ORDER BY p.url, m.rank").fetchall()]


def primary(con, url, names):
    """A kinyerés rekordja az oldalra (`primary_entities`), egy kész LLM-futásban."""
    run_id = con.execute("SELECT run_id FROM entity_runs WHERE method = 'llm'").fetchone()
    if run_id is None:
        (run_id,) = con.execute("INSERT INTO entity_runs (started_at, method, model) VALUES "
                                "(?, 'llm', 'm') RETURNING run_id", [NOON]).fetchone()
    else:
        run_id = run_id[0]
    (page_id,) = con.execute("SELECT page_id FROM pages WHERE url = ?", [url]).fetchone()
    con.execute("INSERT INTO entity_run_pages (run_id, page_id, status, extraction, refined, "
                "finished_at) VALUES (?, ?, 'done', ?, ?, ?)",
                [run_id, page_id, json.dumps({"primary_entities": names}),
                 json.dumps({"primary_entities": names}), NOON])


def test_pages_get_roles_support_kinds_and_a_main_entity_with_evidence():
    con = business_site()
    run = graph_of(con)
    assert (run.pages, run.main, run.support, run.none) == (6, 4, 2, 0)
    assert con.execute("SELECT url, role, support_kind, main_status, hreflang_pages "
                       "FROM page_nodes ORDER BY url").fetchall() == [
        ("https://pelda.hu/", "home", None, "main", []),
        ("https://pelda.hu/adatvedelem/", "support", "legal", "support", []),
        ("https://pelda.hu/blog/cikk/", "article", None, "main", []),
        ("https://pelda.hu/en/measurement/", "offer", None, "main",
         ["https://pelda.hu/hu/meres/"]),
        ("https://pelda.hu/hu/meres/", "offer", None, "main",
         ["https://pelda.hu/en/measurement/"]),
        ("https://pelda.hu/kapcsolat/", "support", "contact", "support", [])]
    assert chosen(con) == [
        ("https://pelda.hu/", "Pelda", "main", "strong", ["h1", "home", "title",
                                                          "top_mentions"]),
        ("https://pelda.hu/blog/cikk/", "Mérés", "main", "weak", ["heading"]),   # a téma
        ("https://pelda.hu/en/measurement/", "Mérés", "main", "strong",
         ["anchored", "h1", "title", "top_mentions"]),            # a hreflang-pár kötött entitása
        ("https://pelda.hu/hu/meres/", "Mérés", "main", "strong",
         ["anchored", "h1", "inbound_anchor", "title", "top_mentions", "url"])]
    decision = json.loads(con.execute("SELECT decision FROM page_nodes WHERE url = "
                                      "'https://pelda.hu/kapcsolat/'").fetchone()[0])
    assert decision["status"] == "support" and decision["support"] == "contact"
    # a cikk az oldal csomópontja marad, `about` éllel a témához
    assert con.execute("SELECT f.name, t.name, e.source FROM edges e JOIN entities f ON "
                       "f.entity_id = e.from_id JOIN entities t ON t.entity_id = e.to_id "
                       "WHERE e.type = 'about'").fetchall() == [
        ("Hogyan mérj jól", "Mérés", "m3_article_topic")]


def test_primary_entities_schema_about_and_the_secondary_entities():
    con = business_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    topic = llm_entity(con, "https://pelda.hu/blog/cikk/", "Mérés és adatarchitektúra",
                       "Adatarchitektúra", "concept")
    con.execute("UPDATE pages SET h1 = 'Hogyan mérj jól: adatarchitektúra' WHERE url = "
                "'https://pelda.hu/blog/cikk/'")
    primary(con, "https://pelda.hu/blog/cikk/", ["Adatarchitektúra", "Pelda"])
    build_graph(con)
    rows = [r for r in chosen(con) if r[0] == "https://pelda.hu/blog/cikk/"]
    assert rows[0][1:4] == ("Adatarchitektúra", "main", "strong")     # a téma: primary + H1
    assert {r[1] for r in rows} & {"Hogyan mérj jól", "Pelda"} == set()   # a cikk és a site nem
    assert con.execute("SELECT entity_id FROM page_main_entity WHERE page_id = (SELECT page_id "
                       "FROM pages WHERE url = 'https://pelda.hu/blog/cikk/') AND role = 'main'"
                       ).fetchall() == [(topic,)]


def test_a_name_resolves_to_the_entity_named_so_before_an_alias():
    con = business_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    topic = llm_entity(con, "https://pelda.hu/blog/cikk/", "Mérés és adatarchitektúra",
                       "Adatarchitektúra", "concept")
    con.execute("UPDATE entities SET aliases = list_append(aliases, 'Adatarchitektúra') "
                "WHERE name = 'Mérés'")                       # az ajánlat aliasa is ez
    primary(con, "https://pelda.hu/blog/cikk/", ["Adatarchitektúra"])
    build_graph(con)
    assert con.execute("SELECT entity_id FROM page_main_entity WHERE role = 'main' AND page_id "
                       "= (SELECT page_id FROM pages WHERE url = 'https://pelda.hu/blog/cikk/')"
                       ).fetchall() == [(topic,)]


def test_the_site_entity_and_json_ld_about(tmp_path):
    about = ('<script type="application/ld+json">{"@context": "https://schema.org", '
             '"@graph": [{"@type": "Person", "@id": "https://pelda.hu/#kiss", "name": '
             '"Kiss Anna"}, {"@type": "AboutPage", "mainEntity": {"@id": '
             '"https://pelda.hu/#kiss"}}, {"@type": "FAQPage", "mainEntity": [{"@type": '
             '"Question", "name": "Mi ez?"}]}]}</script>')
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
                "/rolam/": html("Rólam · Pelda", "<main><h1>Rólam</h1><p>Kiss Anna vagyok, "
                                "Pelda alapítója.</p></main>", head=about),
                "/rolunk/": html("Rólunk · Pelda", "<main><h1>Rólunk</h1><p>A Pelda csapata."
                                 "</p></main>")})
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    llm_entity(con, "https://pelda.hu/rolam/", "Kiss Anna", "Kiss Anna", "person")
    primary(con, "https://pelda.hu/rolunk/", ["Pelda"])
    build_graph(con)
    got = {r[0]: r for r in chosen(con)}
    assert got["https://pelda.hu/rolam/"][1:3] == ("Kiss Anna", "main")
    assert "schema_about" in got["https://pelda.hu/rolam/"][4]      # @id → név; a kérdés nem
    assert got["https://pelda.hu/rolunk/"][1] == "Pelda"             # a primary első eleme


def test_a_primary_name_without_the_legal_form_resolves_to_the_organisation():
    # a kinyerés „Pelda Hungary”-t nevez meg; a szervezet neve „PELDA HUNGARY Kft.”, alias nélkül
    con = site({"/": html("Bolt", "<main><h1>Bolt</h1><p>Üdv.</p></main>"),
                "/rolunk/": html("Rólunk", "<main><h1>Rólunk</h1><p>A PELDA HUNGARY Kft. "
                                 "2010 óta működik.</p></main>")})
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    llm_entity(con, "https://pelda.hu/rolunk/", "PELDA HUNGARY Kft.", "PELDA HUNGARY Kft.", "org")
    primary(con, "https://pelda.hu/rolunk/", ["Pelda Hungary"])
    build_graph(con)
    got = {r[0]: r for r in chosen(con)}
    assert got["https://pelda.hu/rolunk/"][1] == "PELDA HUNGARY Kft."
    assert "primary" in got["https://pelda.hu/rolunk/"][4]
    # más típusnál a jogi forma a név része marad
    con.execute("UPDATE entities SET type = 'concept' WHERE name = 'PELDA HUNGARY Kft.'")
    build_graph(con)
    assert "https://pelda.hu/rolunk/" not in {r[0] for r in chosen(con)
                                               if "primary" in r[4]}


def test_edges_from_mentions_main_entities_relations_and_wikidata_classes():
    con = business_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    ids = dict(con.execute("SELECT name, entity_id FROM entities").fetchall())
    con.execute("UPDATE entities SET wikidata_id = 'Q10', wikidata_status = 'confident' "
                "WHERE entity_id = ?", [ids["Mérés"]])
    con.execute("UPDATE entities SET wikidata_id = 'Q20', wikidata_status = 'confident' "
                "WHERE entity_id = ?", [ids["Pelda"]])
    classes = {"Q10": [("P31", "Q35120"), ("P279", "Q20"), ("P279", "Q99")], "Q20": None}
    run = build_graph(con, superclasses=classes.get)
    assert (run.class_lookups, run.class_failures) == (2, 1)
    assert con.execute("SELECT from_id, to_id, source, evidence FROM edges WHERE type = 'is_a'"
                       ).fetchall() == [(ids["Mérés"], ids["Pelda"], "wikidata",
                                         '{"class": "Q20", "property": "P279", "qid": "Q10"}')]
    assert run.edges["part_of"] == 2 and run.edges["main_entity"] == 4
    weights = load_graph_config().mention_weights
    edge = con.execute("SELECT evidence, weight FROM edges WHERE type = 'mentions' AND "
                       "from_id = (SELECT page_id FROM pages WHERE url = "
                       "'https://pelda.hu/hu/meres/') AND to_id = ?", [ids["Mérés"]]).fetchone()
    counts = json.loads(edge[0])
    assert edge[1] == sum(n * weights.get(p, weights["other"]) for p, n in counts.items()
                          if p != "template")


def test_category_page_is_a_listing_support_and_product_main(shop_types):
    con = shop_site()
    run = graph_of(con, page_type_patterns={"category": ("/sct/",),
                                            "brand_category": ("/spl/",)})
    roles = dict(con.execute("SELECT url, role || coalesce('/' || support_kind, '') "
                             "FROM page_nodes").fetchall())
    assert roles["https://pelda.hu/sct/2/Klima"] == "category"
    assert roles["https://pelda.hu/sct/1/Termekek"] == "listing/list"   # gyökér, entitás nélkül
    assert roles["https://pelda.hu/spl/3/ACME"] == "listing/list"
    assert roles["https://pelda.hu/spd/a"] == "product"
    assert run.edges["is_a"] == 5 and con.execute(
        "SELECT DISTINCT source FROM edges WHERE type = 'is_a'").fetchall() == [
        ("category_page",)]
    got = {r[0]: r[1] for r in chosen(con) if r[2] == "main"}
    assert got["https://pelda.hu/sct/2/Klima"] == "Klíma"
    assert got["https://pelda.hu/spd/a"] == "Acme Nordic 2,6 kW oldalfali klíma"


def test_weights_skip_templates_and_scale_single_mentions():
    con = business_site()
    graph_of(con)
    config = load_graph_config()
    for name, pages, mentions, structural, main, content, nav, single, weight in con.execute(
            "SELECT e.name, w.pages, w.mentions, w.structural, w.main_pages, "
            "w.content_anchors, w.nav_anchors, w.single_mention, w.weight FROM entity_weights w "
            "JOIN entities e USING (entity_id)").fetchall():
        parts = {"pages": pages, "mentions": mentions, "structural": structural,
                 "main_pages": main, "content_anchors": content, "nav_anchors": nav}
        expected = sum(config.weights[k] * math.log2(1 + v) for k, v in parts.items())
        assert single == (mentions == 1), name
        assert weight == round(expected * (config.single_mention if single else 1), 4), name
    # a cikk egy tartalmi linkje; a menüből öt oldalcsoport mutat a fő oldalaira, egyszer számítva
    assert con.execute("SELECT main_pages, content_anchors, nav_anchors FROM entity_weights "
                       "JOIN entities USING (entity_id) WHERE name = 'Mérés'"
                       ).fetchone() == (3, 1, 5)
    assert con.execute("SELECT count(*) FROM entity_weights WHERE pages = 0 AND main_pages = 0 "
                       "AND secondary_pages = 0").fetchone() == (0,)


def test_csv_exports(tmp_path):
    con = business_site()
    graph_of(con)
    paths = export_csv(con, tmp_path, "pelda")
    with paths["main_entity"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["fő entitás"] for r in rows if r["url"] == "https://pelda.hu/hu/meres/"] == ["Mérés"]
    assert rows[0].keys() >= {"url", "szerep", "segédoldal", "állapot", "canonical",
                              "megbízhatóság", "bizonyítékok", "másodlagos"}
    with paths["edges"].open(encoding="utf-8-sig", newline="") as handle:
        assert {r["él"] for r in csv.DictReader(handle)} == {"mentions", "main_entity",
                                                              "part_of", "about"}
    with paths["weights"].open(encoding="utf-8-sig", newline="") as handle:
        assert next(csv.DictReader(handle))["rang"] == "1"


def test_url_words_confidence_and_config(tmp_path):
    words = ("privacy", "terms", "thank-you")
    assert url_has_word("https://x.hu/privacy-centre/", words)
    assert url_has_word("https://x.hu/shop_help.php?tab=terms", words)
    assert url_has_word("https://x.hu/thank-you/", words)
    assert not url_has_word("https://x.hu/eprivacy-and-gdpr-diagnostics/", words)
    assert Candidate(1, {"primary": {}}, primary_index=0).confidence() == "medium"
    assert Candidate(1, {"primary": {}, "h1": True}, primary_index=3).confidence() == "strong"
    assert Candidate(1, {"heading": True}).confidence() == "weak"
    bad = tmp_path / "graph.toml"
    bad.write_text("[mention_weights]\ntitle = 1\n[weight]\npages = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mention_weights"):
        load_graph_config(bad)


def test_superclasses_read_p31_and_p279_from_the_cached_request():
    from aaa2.entities.gate import KnowledgeBase

    def claim(qid):
        return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}

    body = {"entities": {"Q1": {"claims": {"P31": [claim("Q5")], "P279": [claim("Q7")],
                                           "P361": [claim("Q9")]}}}}
    asked = []

    def get(service, url, params):
        asked.append(dict(params))
        return "k", body

    assert KnowledgeBase(get).superclasses("Q1") == [("P31", "Q5"), ("P279", "Q7")]
    assert asked[0]["props"] == "claims|descriptions"             # ugyanaz, mint a classes-é
    assert KnowledgeBase(lambda *a: ("k", None)).superclasses("Q1") is None


def test_reference_verdicts():
    from tests.acceptance.m3_main_entity import verdict

    con = business_site()
    graph_of(con)
    ids = dict(con.execute("SELECT name, entity_id FROM entities").fetchall())
    main = (ids["Mérés"],)
    assert verdict(["Mérés"], main, [], con) == "fő"
    assert verdict(["Valami", "Mérés"], main, [], con) == "fő, a referencia más eleme"
    assert verdict(["Pelda"], main, [(ids["Pelda"],)], con) == "másodlagos"
    assert verdict([], None, [], con) == "segédoldal: egyezik"
    assert verdict([], main, [], con) == "segédoldal: van fő entitás"
    assert verdict(["Mérés"], None, [], con) == "nincs fő entitás"
    assert verdict(["Valami"], main, [], con) == "eltér"


def test_profile_pages_get_the_person_also_from_the_hreflang_pair():
    about = ld({"@context": "https://schema.org", "@graph": [
        {"@type": "Person", "@id": "https://pelda.hu/#kiss", "name": "Kiss Anna"},
        {"@type": "AboutPage", "mainEntity": {"@id": "https://pelda.hu/#kiss"}}]})
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
                "/about/": html("About · Pelda", "<main><h1>About</h1><p>I am Kiss Anna, "
                                "founder of Pelda.</p></main>", head=about, lang="en"),
                "/hu/rolam/": html("Rólam · Pelda", "<main><h1>Rólam</h1><p>A Pelda "
                                   "alapítója vagyok.</p></main>")}, languages=("hu", "en"))
    pair = ["en|https://pelda.hu/about/", "hu|https://pelda.hu/hu/rolam/"]
    con.execute("UPDATE pages SET hreflang = ? WHERE url IN ('https://pelda.hu/about/', "
                "'https://pelda.hu/hu/rolam/')", [pair])
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    llm_entity(con, "https://pelda.hu/about/", "Kiss Anna", "Kiss Anna", "person")
    primary(con, "https://pelda.hu/hu/rolam/", ["Pelda"])
    build_graph(con)
    got = {r[0]: r for r in chosen(con) if r[2] == "main"}
    roles = dict(con.execute("SELECT url, role FROM page_nodes").fetchall())
    assert roles["https://pelda.hu/about/"] == roles["https://pelda.hu/hu/rolam/"] == "profile"
    assert got["https://pelda.hu/about/"][1] == "Kiss Anna"
    assert "profile" in got["https://pelda.hu/about/"][4]
    assert got["https://pelda.hu/hu/rolam/"][1] == "Kiss Anna"      # a pár JSON-LD-jéből
    assert "schema_about" in got["https://pelda.hu/hu/rolam/"][4]


def test_a_page_of_teasers_is_a_list():
    post = ld({"@type": "BlogPosting", "headline": "x"})
    first = "A mérés a döntések alapja minden héten, a riportok mögött is ott áll."
    second = "A kampányok eredménye a konverziókon múlik, nem a kattintások számán."
    con = site({
        "/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
        "/blog/": html("Blog · Pelda", "<main><h1>Blog</h1>"
                       "<h2><a href='/blog/meres/'>Hogyan mérj jól</a></h2>"
                       f"<p>{first}</p>"
                       "<h2><a href='/blog/kampany/'>Kampány és konverzió</a></h2>"
                       f"<p>{second}</p></main>"),
        "/blog/meres/": html("Hogyan mérj jól · Pelda", "<main><h1>Hogyan mérj jól</h1>"
                             f"<p>{first} Utána a részletek.</p></main>", head=post),
        "/blog/kampany/": html("Kampány és konverzió · Pelda", "<main><h1>Kampány és "
                               f"konverzió</h1><p>{second} És még.</p></main>", head=post)})
    graph_of(con)
    rows = dict(con.execute("SELECT url, role || '/' || coalesce(support_kind, '') || '/' || "
                            "main_status FROM page_nodes").fetchall())
    assert rows["https://pelda.hu/blog/"] == "listing/list/support"
    assert rows["https://pelda.hu/blog/meres/"].startswith("article/")


def test_canonical_duplicates_inherit_the_original_and_broken_canonicals_do_not_count():
    con = business_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    canonical = {"https://pelda.hu/adatvedelem/": "/kapcsolat/",           # relatív, azonos szerep
                 "https://pelda.hu/blog/cikk/": "https://pelda.hu/hu/meres/",   # más szerep
                 "https://pelda.hu/kapcsolat/": "https://pelda.hu/nincs/",
                 "https://pelda.hu/": "https://pelda.hu"}                  # önmaga
    for url, target in canonical.items():
        con.execute("UPDATE pages SET canonical = ? WHERE url = ?", [target, url])
    run = build_graph(con)
    assert (run.duplicates, dict(run.canonical_issues)) == (
        1, {"not_crawled": 1, "other_type": 1})
    rows = {url: rest for url, *rest in con.execute(
        "SELECT p.url, o.url, p.role, p.main_status, p.group_key = o.group_key, "
        "p.canonical_issue FROM page_nodes p LEFT JOIN page_nodes o "
        "ON o.page_id = p.canonical_page ORDER BY p.url").fetchall()}
    kapcsolat = "https://pelda.hu/kapcsolat/"
    # a duplikátum a cél oldalcsoportjába tartozik, és a cél döntését örökli
    assert rows["https://pelda.hu/adatvedelem/"] == [kapcsolat, "support", "support", True, None]
    # a más szerepű oldalra mutató canonical nem számít: az oldal külön marad
    assert rows["https://pelda.hu/blog/cikk/"] == [None, "article", "main", None, "other_type"]
    assert rows[kapcsolat] == [None, "support", "support", None, "not_crawled"]
    assert rows["https://pelda.hu/"][0] is None and rows["https://pelda.hu/"][4] is None
    assert run.edges["duplicate_of"] == 1
    assert con.execute("SELECT count(*) FROM edges WHERE type = 'main_entity' AND from_id IN "
                       "(SELECT page_id FROM page_nodes WHERE canonical_page IS NOT NULL)"
                       ).fetchone() == (0,)


def test_a_canonical_loop_does_not_count():
    con = business_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    con.execute("UPDATE pages SET canonical = 'https://pelda.hu/adatvedelem/' "
                "WHERE url = 'https://pelda.hu/blog/cikk/'")
    con.execute("UPDATE pages SET canonical = 'https://pelda.hu/blog/cikk/' "
                "WHERE url = 'https://pelda.hu/adatvedelem/'")
    run = build_graph(con)
    assert (run.duplicates, dict(run.canonical_issues)) == (0, {"loop": 2})


def test_contact_and_category_urls_and_articles_anchored_elsewhere():
    post = ld({"@type": "BlogPosting", "headline": "x"})
    con = site({
        "/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
        "/ceg-contact/": html("Írj nekünk · Pelda", "<main><h1>Írj nekünk</h1><p>Várjuk a "
                              "leveled a hét minden napján.</p></main>", head=post),
        "/category/hirek/": html("Hírek · Pelda", "<main><h1>Hírek</h1><p>A legfrissebb "
                                 "írások egy helyen.</p></main>", head=post),
        "/blog/meres/": html("Hogyan mérj jól · Pelda", "<main><h1>Hogyan mérj jól</h1><p>A "
                             "mérés a döntések alapja.</p></main>",
                             head=ld({"@type": "BlogPosting", "headline": "Hogyan mérj jól"})),
        "/utmutato/": html("Útmutató · Pelda", "<main><h1>Hogyan mérj jól: útmutató</h1><p>A "
                           "Hogyan mérj jól cikk folytatása.</p></main>", head=post)})
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    primary(con, "https://pelda.hu/utmutato/", ["Hogyan mérj jól"])
    build_graph(con)
    roles = dict(con.execute("SELECT url, role || '/' || coalesce(support_kind, '') "
                             "FROM page_nodes").fetchall())
    assert roles["https://pelda.hu/ceg-contact/"] == "support/contact"
    assert roles["https://pelda.hu/category/hirek/"] == "listing/list"
    (article,) = con.execute("SELECT entity_id FROM entities WHERE type = 'work' AND "
                             "anchor_page_id IS NOT NULL AND name = 'Hogyan mérj jól'"
                             ).fetchone()
    assert con.execute("SELECT count(*) FROM page_main_entity WHERE entity_id = ?",
                       [article]).fetchone() == (0,)                # a más oldal cikke sem


def test_on_a_profile_page_the_person_wins():
    about = ld({"@context": "https://schema.org", "@graph": [
        {"@type": "Organization", "@id": "https://pelda.hu/#org", "name": "Pelda"},
        {"@type": "ProfilePage", "about": {"@id": "https://pelda.hu/#org"}}]})
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
                "/author/anna/": html("Pelda", "<main><h1>Pelda szerzői</h1><p>Kiss Anna "
                                      "írásai. Kiss Anna a Pelda szerzője.</p></main>",
                                      head=about)})
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    llm_entity(con, "https://pelda.hu/author/anna/", "Kiss Anna", "Kiss Anna", "person")
    primary(con, "https://pelda.hu/author/anna/", ["Pelda"])
    build_graph(con)
    got = {r[0]: r for r in chosen(con) if r[2] == "main"}
    assert con.execute("SELECT role FROM page_nodes WHERE url = 'https://pelda.hu/author/anna/'"
                       ).fetchone() == ("profile",)
    assert got["https://pelda.hu/author/anna/"][1:3] == ("Kiss Anna", "main")


def test_attribute_labels_in_two_cell_rows_do_not_count():
    table = ("<table><tr><td>SCOP</td><td>4,2</td></tr>"
             "<tr><td>Hűtőközeg</td><td>R32</td></tr></table>"
             "<table><tr><td>Mérési csomag</td><td>8 h</td><td>100 000 Ft</td></tr></table>")
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
                "/a/": html("A · Pelda", f"<main><h1>A</h1><p>Leírás.</p>{table}</main>"),
                "/b/": html("B · Pelda", f"<main><h1>B</h1><p>Leírás.</p>{table}</main>")})
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    ids = {}
    for url in ("https://pelda.hu/a/", "https://pelda.hu/b/"):
        ids["SCOP"] = llm_entity(con, url, "SCOP", "SCOP", "concept")            # címke
        ids["R32"] = llm_entity(con, url, "R32", "R32", "product")               # érték
        ids["csomag"] = llm_entity(con, url, "Mérési csomag", "Mérési csomag", "service")
    build_graph(con)
    weighted = dict(con.execute("SELECT entity_id, pages FROM entity_weights").fetchall())
    assert ids["SCOP"] not in weighted                    # csak címkeként áll
    assert weighted[ids["R32"]] == 2 and weighted[ids["csomag"]] == 2   # a háromcellás sor nem
    edge = con.execute("SELECT evidence, weight FROM edges WHERE type = 'mentions' "
                       "AND to_id = ? LIMIT 1", [ids["SCOP"]]).fetchone()
    assert json.loads(edge[0]) == {"label": 1} and edge[1] == 0
    assert row_label('[{"value": "SCOP"}, {"value": "4,2"}]', 4)
    assert not row_label('[{"value": "Hűtőközeg"}, {"value": "R32"}]', 15)
    assert not row_label('[{"value": "a"}, {"value": "b"}, {"value": "c"}]', 1)
    assert not row_label(None, 3)


def test_an_article_about_an_own_offer_supports_it_and_keeps_its_topic():
    offer = ld({"@type": "Service", "@id": "https://pelda.hu/meres/#service", "name": "Mérés",
                "url": "https://pelda.hu/meres/"})
    post = ld({"@type": "BlogPosting", "headline": "Adatarchitektúra kezdőknek",
               "about": {"@id": "https://pelda.hu/meres/#service"}})
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
                "/meres/": html("Mérés · Pelda", "<main><h1>Mérés</h1><p>Leírás.</p></main>",
                                head=offer),
                "/blog/cikk/": html("Adatarchitektúra kezdőknek · Pelda", "<main><h1>"
                                    "Adatarchitektúra kezdőknek</h1><p>Az adatarchitektúra "
                                    "alapjai.</p></main>", head=post)})
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    topic = llm_entity(con, "https://pelda.hu/blog/cikk/", "Adatarchitektúra", "Adatarchitektúra",
                       "concept")
    primary(con, "https://pelda.hu/blog/cikk/", ["Adatarchitektúra"])
    run = build_graph(con)
    got = {r[0]: r for r in chosen(con) if r[2] == "main"}
    assert got["https://pelda.hu/blog/cikk/"][1] == "Adatarchitektúra"     # a téma, nem az ajánlat
    assert con.execute("SELECT count(*) FROM page_main_entity m JOIN entities e ON "
                       "e.entity_id = m.entity_id JOIN pages p ON p.page_id = m.page_id "
                       "WHERE e.name = 'Mérés' AND p.url LIKE '%/blog/cikk/'"
                       ).fetchone() == (0,)
    assert run.edges["supports"] == 1
    assert con.execute(
        "SELECT f.type, f.subtype, t.name, e.source, e.from_kind FROM edges e JOIN entities f "
        "ON f.entity_id = e.from_id JOIN entities t ON t.entity_id = e.to_id "
        "WHERE e.type = 'supports'").fetchall() == [
        ("work", "article", "Mérés", "schema_about", "entity")]
    assert topic is not None


def test_wikidata_is_a_rules():
    assert is_a_reason("concept", "concept", "P279", "Q2") is None
    assert is_a_reason("concept", "concept", "P31", "Q2") == "P31 concept típuson"
    assert is_a_reason("tech", "tech", "P31", "Q2") is None
    assert is_a_reason("tech", "concept", "P31", "Q2") == "tech → concept"
    assert is_a_reason("concept", "tech", "P279", "Q2") == "concept → tech"
    assert is_a_reason("org", "concept", "P31", "Q2") is None
    for generic in ("Q35120", "Q151885", "Q1799072", "Q3249551", "Q1914636", "Q11016",
                    "Q2267705"):
        assert is_a_reason("tech", "tech", "P279", generic) == "általános osztály"


def article_pair():
    """Egy cikk két nyelven (hreflang-pár), nyelvenként más nevű témával."""
    blog = ld({"@type": "BlogPosting", "headline": "Cikk"})
    con = site({
        "/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
        "/blog/ai-search/": html("Clicks fell · Pelda", "<main><article><h1>Clicks fell</h1>"
                                 "<p>AI search changed how clicks behave in Search Console. "
                                 "Search Console shows it. Search Console again.</p></article>"
                                 "</main>", head=blog, lang="en"),
        "/hu/blog/ai-kereses/": html("Kevesebb kattintás · Pelda", "<main><article>"
                                     "<h1>Kevesebb kattintás</h1><p>Az AI-keresés átírta a "
                                     "kattintásokat. A Search Console mutatja.</p></article>"
                                     "</main>", head=blog),
    }, languages=("hu", "en"))
    pair = ["en|https://pelda.hu/blog/ai-search/", "hu|https://pelda.hu/hu/blog/ai-kereses/"]
    con.execute("UPDATE pages SET hreflang = ? WHERE url LIKE '%/blog/%'", [pair])
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    llm_entity(con, "https://pelda.hu/blog/ai-search/", "AI search", "AI search", "concept")
    llm_entity(con, "https://pelda.hu/blog/ai-search/", "Search Console", "Search Console",
               "tech")
    llm_entity(con, "https://pelda.hu/hu/blog/ai-kereses/", "AI-keresés", "AI-keresés",
               "concept")
    llm_entity(con, "https://pelda.hu/hu/blog/ai-kereses/", "Search Console", "Search Console",
               "tech")
    return con


def mains(con):
    return {url: name for url, name, role, *_ in chosen(con) if role == "main"}


def signals(con):
    return {url: json.loads(decision).get("signals") for url, decision in con.execute(
        "SELECT url, decision FROM page_nodes").fetchall() if "/blog/" in url}


EN, HU = "https://pelda.hu/blog/ai-search/", "https://pelda.hu/hu/blog/ai-kereses/"


def test_a_language_pair_shares_the_primary_entities_of_its_members():
    # az egyik tag kinyerése nem nevezett meg fő témát: a pár másik tagjának listája dönt
    con = article_pair()
    primary(con, EN, [])
    primary(con, HU, ["AI-keresés", "Search Console"])
    build_graph(con)
    assert mains(con) | {} == {**mains(con), EN: "AI-keresés", HU: "AI-keresés"}
    evidence = {url: json.loads(e) for url, e in con.execute(
        "SELECT n.url, m.evidence FROM page_main_entity m JOIN page_nodes n USING (page_id) "
        "WHERE m.role = 'main' AND n.url LIKE '%/blog/%'").fetchall()}
    assert evidence[EN]["group_primary"] == {"index": 0, "name": "AI-keresés", "page": HU}
    assert "primary" in evidence[HU] and "group_primary" not in evidence[HU]
    assert signals(con) == {EN: {"primary_empty": True}, HU: None}


def test_a_language_pair_gets_one_main_entity_even_if_each_member_names_its_own():
    con = article_pair()
    primary(con, EN, ["AI search", "Search Console"])
    primary(con, HU, ["AI-keresés"])
    build_graph(con)
    # azonos sorszám, egy-egy tag nevezi meg: az elsődleges nyelvű (hu) tagé nyer mindkét oldalon
    assert (mains(con)[EN], mains(con)[HU]) == ("AI-keresés", "AI-keresés")
    assert signals(con) == {EN: None, HU: None}
    # amit mindkét tag megnevez, megelőzi azt, amit csak az egyik (azonos sorszámnál)
    con.execute("DELETE FROM entity_run_pages")
    primary(con, EN, ["Search Console", "AI search"])
    primary(con, HU, ["AI-keresés", "Search Console"])
    build_graph(con)
    assert (mains(con)[EN], mains(con)[HU]) == ("Search Console", "Search Console")


def test_a_pair_without_any_primary_entities_is_flagged_when_its_main_entities_differ(tmp_path):
    con = article_pair()
    primary(con, EN, [])
    primary(con, HU, [])
    build_graph(con)
    got = mains(con)
    assert got[EN] != got[HU]                  # oldalanként a title, a H1, az említésszám dönt
    found = signals(con)
    assert found[EN]["primary_empty"] is True and found[HU]["primary_empty"] is True
    assert [(o["url"], o["entity"]) for o in found[EN]["pair_differs"]] == [(HU, got[HU])]
    assert [(o["url"], o["entity"]) for o in found[HU]["pair_differs"]] == [(EN, got[EN])]
    # a jelzés a nézetben és a szerződésben
    build_findings(con)
    views = site_views(con, "pelda", "pelda.hu")
    notes = {page.url: page.notes for page in views.pages}
    assert notes[EN] == ["a kinyerés nem nevezett meg fő témát",
                         f"a nyelvi pár fő entitása eltér: {HU} → {got[HU]}"]
    paths = export_views(con, tmp_path, "pelda")
    assert "a nyelvi pár fő entitása eltér" in paths["pages"].read_text(encoding="utf-8-sig")
    assert "jelzések" in paths["html"].read_text(encoding="utf-8")
    # a fül- és lekérdezés-változat (azonos nyelv) nem nyelvi pár
    graph = con.execute("SELECT count(*) FROM page_nodes WHERE url LIKE '%/blog/%'").fetchone()
    assert graph == (2,)
