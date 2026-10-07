"""A lehetséges kannibalizáció (aaa2/functions/findings.py, `_shared_topics`) szintetikus
site-on: testvéroldalak témaközpont mellett, a témaközpont és a gyermeke, a title utótagja, az
átfedésben részt nem vevő oldal, a pár viszonya és a nyelvek."""
from aaa2.entities.rules import run_rules
from aaa2.functions.findings import build_findings, common_suffixes, title_similarity
from aaa2.functions.graph import build_graph
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON, llm_entity
from tests.test_findings import BASE, rows
from tests.test_graph import primary

ARTICLE = ld({"@type": "BlogPosting", "headline": "x"})


def article(title, text, lang="hu"):
    return html(title, f"<main><h1>{title}</h1><p>{text}</p></main>", head=ARTICLE, lang=lang)


def analysed(pages, topic="SEO", languages=("hu",)):
    """Site a megadott oldalakkal; a kezdőoldal kivételével mindegyik fő entitása a `topic`."""
    con = site({"/": html("Pelda", "<main><h1>Pelda</h1><p>Üdv.</p></main>"), **pages},
               languages=languages)
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    for path in pages:
        llm_entity(con, f"{BASE}{path}", topic, topic, "concept")
        primary(con, f"{BASE}{path}", [topic])
    build_graph(con)
    build_findings(con)
    return con


def cannibalization(con):
    return [(summary, evidence) for _, summary, evidence in rows(con, "cannibalization")]


SIBLINGS = {"/seo/a/": article("SEO útmutató lépésről lépésre", "A SEO alapjai."),
            "/seo/b/": article("SEO útmutató lépésről lépésre", "A SEO a gyakorlatban.")}


def test_two_siblings_with_the_same_title_and_main_entity_are_a_possible_cannibalization():
    ((summary, evidence),) = cannibalization(analysed(SIBLINGS))
    assert summary == ("SEO: lehetséges kannibalizáció: 2 oldal fő entitása, átfedő másodlagos "
                       "entitással vagy hasonló title-lel (hu)")
    assert evidence["overlaps"] == [{
        "pages": [f"{BASE}/seo/a/", f"{BASE}/seo/b/"], "secondary": [], "title_similarity": 1.0,
        "relation": "testvér", "links": "nincs"}]
    assert evidence["other_pages"] == 0


def test_the_siblings_are_still_compared_when_a_hub_stands_above_them():
    # a témaközpont title-je más: a testvérek jelölése megmarad, a témaközpont az átfedésben
    # nem vesz részt, ezért a megállapítás oldallistájában sem áll
    hub = article("SEO témaközpont: minden egy helyen", "A SEO témaközpontja.")
    ((_, evidence),) = cannibalization(analysed({"/seo/": hub, **SIBLINGS}))
    assert [o["pages"] for o in evidence["overlaps"]] == [[f"{BASE}/seo/a/", f"{BASE}/seo/b/"]]
    assert [p["url"] for p in evidence["pages"]] == [f"{BASE}/seo/a/", f"{BASE}/seo/b/"]
    assert evidence["other_pages"] == 1


def test_the_hub_and_its_child_are_marked_when_they_overlap():
    hub = article("SEO útmutató lépésről lépésre",
                  "A SEO témaközpontja. <a href='/seo/a/'>Az első rész</a>.")
    ((summary, evidence),) = cannibalization(analysed({"/seo/": hub, **SIBLINGS}))
    assert summary.startswith("SEO: lehetséges kannibalizáció: 3 oldal")
    found = {tuple(o["pages"]): (o["relation"], o["links"]) for o in evidence["overlaps"]}
    assert found == {
        (f"{BASE}/seo/", f"{BASE}/seo/a/"): ("szülő–gyermek", "első → második"),
        (f"{BASE}/seo/", f"{BASE}/seo/b/"): ("szülő–gyermek", "nincs"),
        (f"{BASE}/seo/a/", f"{BASE}/seo/b/"): ("testvér", "nincs")}


def test_the_last_piece_of_a_title_is_cut_only_when_it_repeats_across_the_site():
    # a két title utolsó szelete maga a különbség: nem site-utótag, nem marad el
    assert title_similarity("SEO – Kezdőknek", "SEO – Haladóknak") == 1 / 3
    con = analysed({"/seo/kezdo/": article("SEO – Kezdőknek", "A SEO alapjai."),
                    "/seo/halado/": article("SEO – Haladóknak", "A SEO mélyebben.")})
    assert cannibalization(con) == []
    ((_, summary, evidence),) = rows(con, "shared_topic")
    assert summary == "SEO: 2 oldal közös témája, átfedés nélkül (hu)"
    assert evidence["overlaps"] == []
    # a site-szerte ismétlődő utótag elmarad: a két title a maradékban azonos
    titles = ["SEO útmutató · Pelda", "SEO útmutató · Pelda", "Kapcsolat · Pelda", "Pelda"]
    suffixes = common_suffixes(titles)
    assert suffixes == {"pelda"}
    assert title_similarity("SEO útmutató – Kezdőknek · Pelda",
                            "SEO útmutató – Haladóknak · Pelda", suffixes) == 0.5
    assert title_similarity(titles[0], titles[1], suffixes) == 1.0
    # egy utótag, amely csak néhány oldalon áll, nem site-utótag
    assert common_suffixes(["A – Blog", "B – Blog", "C", "D", "E"]) == frozenset()


def test_a_third_page_outside_the_overlap_is_not_listed():
    other = article("SEO audit technikai szemmel", "A SEO technikai oldala.")
    ((_, evidence),) = cannibalization(analysed({**SIBLINGS, "/blog/audit/": other}))
    assert [p["url"] for p in evidence["pages"]] == [f"{BASE}/seo/a/", f"{BASE}/seo/b/"]
    assert evidence["other_pages"] == 1


def test_pages_in_different_languages_are_never_a_pair():
    # ugyanaz a fő entitás és ugyanaz a title, de más nyelven: nyelvenként egy-egy oldal
    con = analysed({"/hu/seo/": article("SEO útmutató lépésről lépésre", "A SEO alapjai."),
                    "/en/seo/": article("SEO útmutató lépésről lépésre", "SEO basics.",
                                        lang="en")}, languages=("hu", "en"))
    assert cannibalization(con) == [] and rows(con, "shared_topic") == []
    langs = dict(con.execute("SELECT url, lang FROM page_nodes WHERE url LIKE '%/seo/'"
                             ).fetchall())
    assert langs == {f"{BASE}/hu/seo/": "hu", f"{BASE}/en/seo/": "en"}
