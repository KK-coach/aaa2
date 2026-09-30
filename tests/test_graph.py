"""Az entitásgráf (aaa2/functions/graph.py) szintetikus site-okon, hálózat és LLM nélkül:
oldal-csomópontok és segédoldalak, a fő entitás a bizonyítékaival, élek, súlyok, kivonatok."""
import csv
import json
import math

import pytest

from aaa2.entities import overrides
from aaa2.entities.rules import run_rules
from aaa2.entities.site import run_site
from aaa2.functions.graph import (
    Candidate,
    build_graph,
    export_csv,
    load_graph_config,
    url_has_word,
)
from tests.test_entities_rules import html, site
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
        ("https://pelda.hu/blog/cikk/", "Hogyan mérj jól", "main", "strong",
         ["anchored", "h1", "inbound_anchor", "title", "top_mentions"]),
        ("https://pelda.hu/en/measurement/", "Mérés", "main", "strong",
         ["anchored", "h1", "title", "top_mentions"]),            # a hreflang-pár kötött entitása
        ("https://pelda.hu/hu/meres/", "Mérés", "main", "strong",
         ["anchored", "h1", "inbound_anchor", "title", "top_mentions", "url"])]
    decision = json.loads(con.execute("SELECT decision FROM page_nodes WHERE url = "
                                      "'https://pelda.hu/kapcsolat/'").fetchone()[0])
    assert decision["status"] == "support" and decision["support"] == "contact"


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
    assert rows[0][1:3] == ("Hogyan mérj jól", "main")
    assert rows[1][1:3] == ("Adatarchitektúra", "secondary")          # H1 + primary
    assert "Pelda" not in {r[1] for r in rows}                        # a site entitása nem
    assert con.execute("SELECT entity_id FROM page_main_entity WHERE role = 'secondary'"
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
                                         '{"property": "P279", "qid": "Q10", "class": "Q20"}')]
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
    for name, pages, mentions, structural, main, inbound, single, weight in con.execute(
            "SELECT e.name, w.pages, w.mentions, w.structural, w.main_pages, "
            "w.inbound_anchors, w.single_mention, w.weight FROM entity_weights w "
            "JOIN entities e USING (entity_id)").fetchall():
        parts = {"pages": pages, "mentions": mentions, "structural": structural,
                 "main_pages": main, "inbound_anchors": inbound}
        expected = sum(config.weights[k] * math.log2(1 + v) for k, v in parts.items())
        assert single == (mentions == 1), name
        assert weight == round(expected * (config.single_mention if single else 1), 4), name
    assert con.execute("SELECT main_pages, inbound_anchors FROM entity_weights JOIN entities "
                       "USING (entity_id) WHERE name = 'Mérés'").fetchone() == (2, 5)


def test_csv_exports(tmp_path):
    con = business_site()
    graph_of(con)
    paths = export_csv(con, tmp_path, "pelda")
    with paths["main_entity"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["fő entitás"] for r in rows if r["url"] == "https://pelda.hu/hu/meres/"] == ["Mérés"]
    assert rows[0].keys() >= {"url", "szerep", "segédoldal", "állapot", "megbízhatóság",
                              "bizonyítékok", "másodlagos"}
    with paths["edges"].open(encoding="utf-8-sig", newline="") as handle:
        assert {r["él"] for r in csv.DictReader(handle)} == {"mentions", "main_entity",
                                                              "part_of"}
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
