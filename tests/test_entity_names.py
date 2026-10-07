"""Entitásnevek: az azonos `@id`-jú schema-csomópont egy entitás, a neve a saját oldaláról;
az azonosító névütközése megállapítás; és az oldal nyelvén hiányzó név jelölése."""
from aaa2.entities.rules import run_rules
from aaa2.functions.findings import build_findings
from aaa2.functions.graph import build_graph
from aaa2.resolver.display import MISSING_NAME, DisplayNames
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON, llm_entity
from tests.test_findings import BASE, rows

SYSTEM_ID = f"{BASE}/#rendszer"


def system_site():
    """A gyűjtő szolgáltatás csomópontja a saját oldalán „Organikus növekedési rendszer”, a
    többi oldalon ugyanazzal az azonosítóval „A növekedési rendszer”, a kezdőoldalra mutatva."""
    own = ld({"@type": "Service", "@id": SYSTEM_ID, "name": "Organikus növekedési rendszer",
              "url": f"{BASE}/rendszer/"})
    elsewhere = {"@type": "Service", "@id": SYSTEM_ID, "name": "A növekedési rendszer",
                 "url": f"{BASE}/"}
    seo = ld([elsewhere, {"@type": "Service", "@id": f"{BASE}/seo/#service",
                          "name": "SEO-tanácsadás", "url": f"{BASE}/seo/"}])
    return site({
        "/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>", head=ld(elsewhere)),
        "/rendszer/": html("Organikus növekedési rendszer · Pelda",
                           "<main><h1>Organikus növekedési rendszer</h1><p>Szöveg.</p></main>",
                           head=own),
        "/seo/": html("SEO-tanácsadás · Pelda", "<main><h1>SEO-tanácsadás</h1><p>Szöveg.</p>"
                      "</main>", head=seo)})


def test_schema_nodes_with_the_same_id_are_one_entity_named_on_its_own_page():
    con = system_site()
    run_rules(con)
    found = con.execute("SELECT name, type, aliases FROM entities WHERE name ILIKE "
                        "'%növekedési rendszer%' OR list_contains(aliases, "
                        "'A növekedési rendszer')").fetchall()
    assert found == [("Organikus növekedési rendszer", "service", ["A növekedési rendszer"])]
    # mindhárom oldal említése ugyanazé az entitásé
    assert con.execute(
        "SELECT count(DISTINCT pe.page_id) FROM page_entities pe JOIN entities e USING "
        "(entity_id) WHERE e.name = 'Organikus növekedési rendszer'").fetchone() == (3,)


def test_the_same_id_with_several_names_is_a_finding_about_the_structured_data():
    con = system_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    ((severity, summary, evidence),) = rows(con, "schema_id_names")
    assert severity == "low"
    assert summary == ("a strukturált adatban 1 azonosító (@id) több névvel szerepel (1 "
                       "csomópontjának url-je nem a saját oldalára mutat)")
    assert evidence["ids"] == [{
        "id": SYSTEM_ID,
        "names": [
            {"name": "A növekedési rendszer", "lang": "hu", "pages": 2,
             "urls": [f"{BASE}/", f"{BASE}/seo/"]},
            {"name": "Organikus növekedési rendszer", "lang": "hu", "pages": 1,
             "urls": [f"{BASE}/rendszer/"]}],
        "languages": ["hu"], "within_language": ["hu"], "several_languages": False,
        # a csomópont url-je a saját oldalán a /rendszer/, máshol a kezdőoldal
        "node_urls": [f"{BASE}/", f"{BASE}/rendszer/"], "url_mismatch": True}]
    # a saját oldalán a megállapítás nem keresi a másik nevet: a H1 és a title megnevezi
    assert not [f for f in rows(con, "h1_title_mismatch")
                if "rendszer" in str(f[2].get("url", "")) or "rendszer" in f[1]]


def test_names_in_two_languages_under_one_id_are_marked_as_such():
    # ugyanaz az azonosító a magyar oldalon magyar, az angolon angol néven: ez nem ugyanaz,
    # mint egy nyelven belül két név; a megállapítás megmarad, a jelzés külön áll
    def node(name):
        return ld({"@type": "Service", "@id": f"{BASE}/meres/#service", "name": name,
                   "url": f"{BASE}/meres/"})
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"),
                "/meres/": html("Mérés · Pelda", "<main><h1>Mérés</h1><p>Szöveg.</p></main>",
                                head=node("Mérés")),
                "/en/": html("Pelda", "<main><h1>Pelda</h1><p>Text.</p></main>", lang="en",
                             head=node("Measurement"))}, languages=("hu", "en"))
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    ((severity, summary, evidence),) = rows(con, "schema_id_names")
    assert severity == "low"
    assert summary == ("a strukturált adatban 1 azonosító (@id) több névvel szerepel (1 alatt "
                       "több nyelv neve áll)")
    (item,) = evidence["ids"]
    assert [(n["lang"], n["name"], n["urls"]) for n in item["names"]] == [
        ("en", "Measurement", [f"{BASE}/en/"]), ("hu", "Mérés", [f"{BASE}/meres/"])]
    assert (item["languages"], item["within_language"], item["several_languages"]) == (
        ["en", "hu"], [], True)
    # a csomópont url-je mindenütt az azonosító oldalára mutat
    assert (item["node_urls"], item["url_mismatch"]) == ([f"{BASE}/meres/"], False)


def test_a_consistent_id_is_not_a_finding():
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>",
                          head=ld({"@type": "Organization", "@id": f"{BASE}/#org",
                                   "name": "Pelda Kft.", "url": f"{BASE}/"})),
                "/a/": html("A · Pelda", "<main><h1>A</h1><p>Szöveg.</p></main>",
                            head=ld({"@type": "Organization", "@id": f"{BASE}/#org",
                                     "name": "PELDA KFT.", "url": f"{BASE}/"}))})
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    assert rows(con, "schema_id_names") == []           # csak kis- és nagybetűben tér el


def test_a_missing_name_in_the_language_of_the_page_is_marked():
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>A mérés és a SEO a Pelda Kft. "
                          "területe.</p></main>"),
                "/en/": html("Pelda", "<main><h1>Pelda</h1><p>Text.</p></main>", lang="en")},
               languages=("hu", "en"))
    run_rules(con)
    concept = llm_entity(con, f"{BASE}/", "mérés", "mérés", "concept")
    short = llm_entity(con, f"{BASE}/", "SEO", "SEO", "concept")
    company = llm_entity(con, f"{BASE}/", "Pelda Kft.", "Pelda Kft.", "org")
    display = DisplayNames(con)
    assert display.site_lang == "hu"
    # a site elsődleges nyelvén a megtartott név az oldal nyelvén áll
    assert display.note(concept, "hu") == ""
    # angol oldalon a magyar nevű fogalomnak nincs angol alakja: jelölés, a név marad
    assert display.note(concept, "en") == MISSING_NAME
    assert display.on_page(concept, "mérés", "en") == "mérés"
    # a rövidítés és a szervezet neve nyelvtől független
    assert display.note(short, "en") == "" and display.note(company, "en") == ""
    # amit a kinyerés ezen a nyelven látott először, annak a neve az oldal nyelvén áll
    con.execute("UPDATE entities SET lang = 'en' WHERE entity_id = ?", [concept])
    assert DisplayNames(con).note(concept, "en") == ""
