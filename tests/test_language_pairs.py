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


@pytest.mark.parametrize("body, merged", [("", 0), ("<p>A pince válogatása.</p>", 1)])
def test_a_page_without_body_text_is_a_list_and_its_first_item_is_not_its_topic(body, merged):
    # a borlap csupa címsor: a két nyelven más-más bor áll az első helyen; ugyanez az oldalpár
    # szövegtörzzsel összeolvadna (a szabály 5. feltétele dönt, nem más)
    con = pair_site({
        "/hu/borlap/": page("hu", "Borlap", f"{body}<h3>Riserva 2011</h3><h3>Saten</h3>"),
        "/en/wine-list/": page("en", "Wine list", f"{body}<h3>Amarone 2017</h3><h3>Saten</h3>"),
    }, [{"hu": "/hu/borlap/", "en": "/en/wine-list/"}])
    topic(con, "/hu/borlap/", "Riserva 2011", "product", "wine")
    topic(con, "/en/wine-list/", "Amarone 2017", "product", "wine")
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
