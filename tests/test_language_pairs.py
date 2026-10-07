"""Nyelvi változatok összevonása (aaa2/resolver/language.py, `language_pair`) szintetikus
site-on, hálózat és LLM nélkül."""
import json

import pytest

from aaa2.entities.rules import run_rules
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, site
from tests.test_entities_site import NOON, article_record, llm_entity

BASE = "https://pelda.hu"


def pair_site(pages, pairs):
    """Site a megadott oldalakkal; `pairs`: hreflang-készletek (nyelv → útvonal), minden
    tagjuk megkapja a készletet."""
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"), **pages},
               languages=("hu", "en"))
    for pair in pairs:
        entries = [f"{lang}|{BASE}{path}" for lang, path in pair.items()]
        con.execute("UPDATE pages SET hreflang = ? WHERE url IN (SELECT unnest(?))",
                    [entries, [f"{BASE}{path}" for path in pair.values()]])
    run_rules(con)
    return con


def topic(con, path, name, kind, subtype=None):
    """Az oldal fő témája: egy LLM-említés a névvel és a kinyerés első fő entitása."""
    entity_id = llm_entity(con, f"{BASE}{path}", name, name, kind, subtype)
    article_record(con, f"{BASE}{path}", name, kind)
    return entity_id


def merges(con):
    return [(kept, removed, json.loads(evidence)) for kept, removed, evidence in con.execute(
        "SELECT kept_name, removed_name, evidence FROM merge_log WHERE rule = 'language_pair' "
        "ORDER BY merge_id").fetchall()]


def page(lang, h1, body):
    return html(h1, f"<main><h1>{h1}</h1>{body}</main>", lang=lang)


def test_the_main_topics_of_a_language_pair_become_one_entity():
    con = pair_site({
        "/hu/blog/serp/": page("hu", "Saját mérőeszköz", "<p>A SERP-figyelő naponta mér.</p>"),
        "/en/blog/serp/": page("en", "My own tool", "<p>The SERP tracker measures daily.</p>"),
    }, [{"hu": "/hu/blog/serp/", "en": "/en/blog/serp/"}])
    kept = topic(con, "/hu/blog/serp/", "SERP-figyelő", "tech", "software")
    topic(con, "/en/blog/serp/", "SERP tracker", "tech", "software")
    run_site(con, clock=lambda: NOON)
    # a megtartott név a site elsődleges nyelvén (a gyökéroldal nyelve: hu) álló alak
    assert merges(con) == [("SERP-figyelő", "SERP tracker", {
        "pages": [f"{BASE}/hu/blog/serp/", f"{BASE}/en/blog/serp/"], "langs": ["hu", "en"],
        "names": ["SERP-figyelő", "SERP tracker"], "type": "tech", "subtype": "software"})]
    assert con.execute("SELECT entity_id, name FROM entities WHERE name IN ('SERP-figyelő', "
                       "'SERP tracker')").fetchall() == [(kept, "SERP-figyelő")]
    # a másik név alias a saját nyelvével és hreflang forrással
    assert ("SERP tracker", "en", "hreflang") in con.execute(
        "SELECT alias, lang, source FROM entity_aliases WHERE entity_id = ?", [kept]).fetchall()
    # a két oldal külön oldal marad, mindkettő említése az összevont entitásé
    assert con.execute(
        "SELECT count(DISTINCT pe.page_id) FROM page_entities pe JOIN pages p USING (page_id) "
        "WHERE pe.entity_id = ? AND p.url LIKE '%/blog/serp/'", [kept]).fetchone() == (2,)


@pytest.mark.parametrize("body, merged", [("", 0), ("<p>A mérés leírása.</p>", 1)])
def test_a_page_without_body_text_is_a_list_and_its_first_item_is_not_its_topic(body, merged):
    # a csupa címsorból álló oldal felsorolás: az első tétele nem az oldal témája; ugyanez az
    # oldalpár szövegtörzzsel összeolvad (a szabály 5. feltétele dönt, nem más)
    con = pair_site({
        "/hu/meres/": page("hu", "Mérés", f"{body}<h3>Webanalitika</h3><h3>Jelentés</h3>"),
        "/en/measurement/": page("en", "Measurement",
                                 f"{body}<h3>Web analytics</h3><h3>Reporting</h3>"),
    }, [{"hu": "/hu/meres/", "en": "/en/measurement/"}])
    topic(con, "/hu/meres/", "Webanalitika", "concept", "discipline")
    topic(con, "/en/measurement/", "Web analytics", "concept", "discipline")
    run_site(con, clock=lambda: NOON)
    assert len(merges(con)) == merged


@pytest.mark.parametrize("body", ["", "<p>A pince válogatása.</p>"])
def test_the_wine_list_never_merges_its_first_items_even_with_a_paragraph(body):
    # a borlap két nyelvén más-más bor áll az első helyen: termék nem olvad össze nyelvi
    # párként akkor sem, ha az oldalnak van bekezdése
    con = pair_site({
        "/hu/borlap/": page("hu", "Borlap", f"{body}<h3>Riserva 2011</h3><h3>Saten</h3>"),
        "/en/wine-list/": page("en", "Wine list", f"{body}<h3>Amarone 2017</h3><h3>Saten</h3>"),
    }, [{"hu": "/hu/borlap/", "en": "/en/wine-list/"}])
    topic(con, "/hu/borlap/", "Riserva 2011", "product", "wine")
    topic(con, "/en/wine-list/", "Amarone 2017", "product", "wine")
    run_site(con, clock=lambda: NOON)
    assert merges(con) == []


@pytest.mark.parametrize(("kind", "subtype", "merged"), [
    ("concept", "discipline", 1), ("service", None, 1), ("tech", "software", 1),
    ("product", None, 0), ("product", "variant", 0), ("work", "article", 0),
    ("work", "course", 0), ("org", "company", 0)])
def test_only_concepts_services_and_technologies_merge_as_a_language_pair(kind, subtype,
                                                                          merged):
    con = pair_site({
        "/hu/tema/": page("hu", "Magyar cím", "<p>Magyar megnevezés a témáról.</p>"),
        "/en/topic/": page("en", "English title", "<p>English naming of the topic.</p>"),
    }, [{"hu": "/hu/tema/", "en": "/en/topic/"}])
    topic(con, "/hu/tema/", "Magyar megnevezés", kind, subtype)
    topic(con, "/en/topic/", "English naming", kind, subtype)
    run_site(con, clock=lambda: NOON)
    assert len(merges(con)) == merged


def test_topics_of_a_different_subtype_or_type_stay_apart():
    con = pair_site({
        "/hu/blog/ai/": page("hu", "Kattintásvesztés", "<p>Az organikus kattintás csökken.</p>"),
        "/en/blog/ai/": page("en", "Click loss", "<p>AI search changes the journey.</p>"),
        "/hu/analitika/": page("hu", "Tanácsadás", "<p>A webanalitika a mérés alapja.</p>"),
        "/en/analytics/": page("en", "Consulting", "<p>Web analytics consulting for teams.</p>"),
    }, [{"hu": "/hu/blog/ai/", "en": "/en/blog/ai/"},
        {"hu": "/hu/analitika/", "en": "/en/analytics/"}])
    topic(con, "/hu/blog/ai/", "organikus kattintás", "concept", "metric")
    topic(con, "/en/blog/ai/", "AI search", "concept", "discipline")
    topic(con, "/hu/analitika/", "webanalitika", "concept")
    topic(con, "/en/analytics/", "Web analytics consulting", "service")
    run_site(con, clock=lambda: NOON)
    assert merges(con) == []


@pytest.mark.parametrize("own_url_in_set, merged", [(False, 0), (True, 1)])
def test_a_canonical_duplicate_is_not_a_member_of_the_pair(own_url_in_set, merged):
    # a /hu/megoldas/ a /hu/megoldasok/ duplikátuma: a cél hreflangját hordozza, a saját címe
    # nincs benne; ha a saját címe állna a készletben, pár-tag lenne és összeolvadna
    con = pair_site({
        "/en/solutions/": page("en", "Solutions", "<p>The Growth System in short.</p>"),
        "/hu/megoldasok/": page("hu", "Megoldások", "<p>Rövid áttekintés.</p>"),
        "/hu/megoldas/": page("hu", "Megoldások", "<p>A Növekedési rendszer röviden.</p>"),
    }, [])
    hu = "/hu/megoldas/" if own_url_in_set else "/hu/megoldasok/"
    con.execute("UPDATE pages SET hreflang = ? WHERE url LIKE '%/solutions/' OR url LIKE "
                "'%/megoldas%'", [[f"en|{BASE}/en/solutions/", f"hu|{BASE}{hu}"]])
    con.execute("UPDATE pages SET canonical = ? WHERE url LIKE '%/hu/megoldas/'",
                [f"{BASE}/hu/megoldasok/"])
    topic(con, "/en/solutions/", "Growth System", "concept", "method")
    topic(con, "/hu/megoldas/", "Növekedési rendszer", "concept", "method")
    run_site(con, clock=lambda: NOON)
    assert len(merges(con)) == merged


def test_the_home_pages_of_a_pair_keep_their_own_topics():
    con = pair_site({
        "/en/": page("en", "Pelda", "<p>Organic growth consulting for teams.</p>"),
    }, [])
    con.execute("UPDATE pages SET hreflang = ? WHERE url IN (?, ?)",
                [[f"hu|{BASE}/", f"en|{BASE}/en/"], f"{BASE}/", f"{BASE}/en/"])
    con.execute("UPDATE blocks SET text = 'Üdv, növekedési tanácsadás.' WHERE text = 'Üdv.'")
    topic(con, "/", "növekedési tanácsadás", "concept")
    topic(con, "/en/", "Organic growth consulting", "concept")
    run_site(con, clock=lambda: NOON)
    assert merges(con) == []


def exported(con, tmp_path):
    """A gráf és a nézetek CSV-kimenete: fájlkulcs → sorok."""
    import csv

    from aaa2.functions.findings import build_findings, export_views
    from aaa2.functions.graph import build_graph, export_csv

    build_graph(con)
    build_findings(con)
    paths = {**export_csv(con, tmp_path, "pelda"), **export_views(con, tmp_path, "pelda")}
    found = {}
    for key, path in paths.items():
        if path.suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as handle:
                found[key] = list(csv.DictReader(handle))
    return found


def test_page_outputs_show_the_name_in_the_language_of_the_page(tmp_path):
    con = pair_site({
        "/hu/blog/serp/": page("hu", "Saját mérőeszköz", "<p>A SERP-figyelő naponta mér.</p>"),
        "/en/blog/serp/": page("en", "My own tool", "<p>The SERP tracker measures daily.</p>"),
    }, [{"hu": "/hu/blog/serp/", "en": "/en/blog/serp/"}])
    topic(con, "/hu/blog/serp/", "SERP-figyelő", "tech", "software")
    topic(con, "/en/blog/serp/", "SERP tracker", "tech", "software")
    run_site(con, clock=lambda: NOON)
    out = exported(con, tmp_path)
    # oldalszinten: a magyar (elsődleges nyelvű) oldalon a megtartott név, az angolon az angol
    for key in ("main_entity", "pages"):
        names = {row["url"]: row["fő entitás"] for row in out[key]}
        assert names[f"{BASE}/hu/blog/serp/"] == "SERP-figyelő", key
        assert names[f"{BASE}/en/blog/serp/"] == "SERP tracker", key
    # site-szinten a megtartott név marad, a másik nyelvű név külön oszlopban, forrással
    for key in ("weights", "site", "entities"):
        row = next(row for row in out[key] if row["entitás"] == "SERP-figyelő")
        assert row["más nyelvű nevek"] == "en: SERP tracker (hreflang)", key
        assert not any(row["entitás"] == "SERP tracker" for row in out[key]), key
    # az összevonás a kimenetben is megjelenik, következtetésként, a két oldallal
    assert out["language_pairs"] == [{
        "entitás": "SERP-figyelő", "a megtartott név": "SERP-figyelő", "nyelv": "hu",
        "oldal": f"{BASE}/hu/blog/serp/", "a másik nyelvű név": "SERP tracker",
        "a másik nyelv": "en", "a másik oldal": f"{BASE}/en/blog/serp/", "típus": "tech",
        "altípus": "software",
        "alap": "következtetett: a hreflang-pár két oldalának fő témája (language_pair)"}]


def test_other_language_labels_of_a_page_entity_are_listed_but_never_shown(tmp_path):
    # az ajánlatoldal angol H1-e és title-je `hreflang` forrású alias, de nem a nyelvi
    # összevonásból jön: az angol oldalon is a megtartott név áll, a címkék csak az oszlopban
    from tests.test_entities_site import business_site

    con = business_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    kept, = con.execute("SELECT e.name FROM entities e JOIN pages p ON p.page_id = "
                        "e.anchor_page_id WHERE p.url LIKE '%/meres/'").fetchone()
    assert con.execute("SELECT count(*) FROM entity_aliases WHERE source = 'hreflang' AND "
                       "lang = 'en'").fetchone()[0] > 0
    out = exported(con, tmp_path)
    names = {row["url"]: row["fő entitás"] for row in out["pages"]}
    assert names[f"{BASE}/en/measurement/"] == names[f"{BASE}/hu/meres/"] == kept
    row = next(row for row in out["site"] if row["entitás"] == kept)
    assert "(hreflang)" in row["más nyelvű nevek"] and row["más nyelvű nevek"].startswith("en: ")
