"""A címmezők ténye és a „cím nem nevezi meg a fő entitást” jelölt (aaa2/functions/titles.py,
findings.py) szintetikus site-on, hálózat és LLM nélkül."""
import csv
import json
import re

from aaa2.entities.rules import run_rules
from aaa2.functions import findings
from aaa2.functions.findings import build_findings, export_views, site_views
from aaa2.functions.graph import build_graph
from aaa2.functions.titles import compare_key, cut_suffix, names_whole
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON, llm_entity
from tests.test_graph import primary

BASE = "https://pelda.hu"
SPLIT = re.compile(r"\s+[-–—|·:]\s+")


def article(title, h1, text, about=None, og_title=None):
    node = {"@type": "BlogPosting", "headline": h1}
    if about:
        node["about"] = {"@type": "Thing", "name": about}
    head = ld(node) + '<meta property="og:type" content="article">' + (
        f'<meta property="og:title" content="{og_title}">' if og_title else "")
    return html(f"{title} · Pelda", f"<main><h1>{h1}</h1><p>{text}</p></main>", head=head)


def titles_site():
    return site({
        "/": html("Pelda", "<main><h1>Üdvözlünk</h1><p>Üdv.</p></main>"),
        "/blog/sebesseg/": article(
            "Amikor az oldalsebesség üzleti kérdés lett", "Amikor az oldalsebesség üzleti "
            "kérdés lett", "Az oldalsebesség számít.", about="Oldalsebesség",
            og_title="Amikor az oldalsebesség üzleti kérdés lett · Pelda"),
        "/blog/gorbe/": article("Négy hónapig semmi, aztán több", "Négy hónapig semmi, aztán "
                                "több", "A keresőoptimalizálás lassan hat."),
        "/blog/olaj/": article("Mire jó a kókuszolajat használni?", "Mire jó a kókuszolajat "
                               "használni?", "A kókuszolaj sokoldalú."),
        "/blog/hirdetes/": article("Meta Ads tippek & trükkök", "Meta Ads tippek és trükkök",
                                   "A Meta sokat változott. A Meta Ads is."),
        "/szolgaltatas/": html(
            "Jogi marketing · Pelda", "<main><h1>A legjobbat érdemled</h1><p>Szöveg.</p></main>",
            head=ld({"@type": "Service", "name": "Jogi marketing",
                     "url": f"{BASE}/szolgaltatas/"})),
        # a H1 a fő tartalmon kívül áll, a tartalmi régió első címsora egy kisebb címsor
        "/kulso/": html(
            "Külső marketing · Pelda", "<header><h1>Külső marketing</h1></header><main>"
            "<h3>Videók</h3><p>Szöveg.</p></main>",
            head=ld({"@type": "Service", "name": "Külső marketing", "url": f"{BASE}/kulso/"}))})


def built():
    con = titles_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    for path, text, name in (("/blog/sebesseg/", "oldalsebesség számít", "Oldalsebesség"),
                             ("/blog/gorbe/", "keresőoptimalizálás", "Keresőoptimalizálás"),
                             ("/blog/olaj/", "kókuszolaj sokoldalú", "Kókuszolaj"),
                             ("/blog/hirdetes/", "Meta sokat", "Meta")):
        llm_entity(con, f"{BASE}{path}", text, name, "concept")
        primary(con, f"{BASE}{path}", [name])
    llm_entity(con, f"{BASE}/blog/hirdetes/", "Meta Ads", "Meta Ads", "tech")
    build_graph(con)
    return con, build_findings(con)


def candidates(con):
    found = {}
    for (evidence,) in con.execute("SELECT evidence FROM findings WHERE type = "
                                   "'title_without_main_entity'").fetchall():
        evidence = json.loads(evidence)
        for page in evidence.get("pages") or [evidence]:
            found[page["url"].removeprefix(BASE)] = page
    return found


def test_whole_name_matching_by_language():
    assert names_whole(["Kókuszolaj"], "Mire jó a kókuszolajat használni?", "hu")
    assert names_whole(["Kókuszolaj"], "Főzés kókuszolajjal", "hu")          # hasonult -val
    assert names_whole(["Levendulaolaj"], "Mire jó a levendula olaj?", "hu")  # különírva
    assert not names_whole(["Kókusztej"], "Kókusztejes hajmosó szappan", "hu")    # képző
    assert not names_whole(["Kókuszolaj"], "Kókuszolajat", "en")     # a rag nyelvhez kötött
    assert names_whole(["site speed"], "When site speed stopped being a detail", "en")
    assert names_whole(["tracker"], "Rank trackers compared", "en")          # többes szám
    assert not names_whole(["Mérés"], "Kimérés a gyakorlatban", "hu")        # szó belseje
    # rövidebb név hosszabb névben nem egyezés; a saját hosszabb alakja az
    assert not names_whole(["Meta"], "Meta Ads tippek", "en", longer=["Meta Ads"])
    assert names_whole(["Meta", "Meta Ads"], "Meta Ads tippek", "en", longer=["Meta Ads"])
    assert names_whole(["Meta"], "Meta Ads és a Meta jövője", "en", longer=["Meta Ads"])


def test_title_fields_compare_after_normalising():
    assert compare_key("Tracking & Measurement") == compare_key("Tracking and Measurement")
    assert compare_key("Mérés és analitika") == compare_key("mérés &  analitika!")
    assert cut_suffix("SEO – Kezdőknek · Pelda", {"pelda"}, SPLIT) == "SEO – Kezdőknek"
    assert cut_suffix("SEO – Kezdőknek", {"pelda"}, SPLIT) == "SEO – Kezdőknek"


def test_candidate_only_where_neither_title_nor_visible_title_names_the_main_entity():
    con, run = built()
    found = candidates(con)
    # a cím nem nevezi meg: cikk; a ragozott alak megnevezés; a „Meta” a „Meta Ads”-ben nem az
    assert {path: page["case"] for path, page in found.items()} == {
        "/blog/gorbe/": "neither", "/blog/hirdetes/": "neither",
        "/szolgaltatas/": "title_only"}
    gorbe = found["/blog/gorbe/"]
    assert gorbe["main_entity"] == "Keresőoptimalizálás" and not gorbe["in_title"] \
        and gorbe["article_basis"] == "cikkoldal (szerep)" \
        and set(gorbe["claim"]) == {"fact", "inference"}
    # az ajánlatoldalon a title megnevezi, a látható cím nem: szereptől független eset
    offer = found["/szolgaltatas/"]
    assert offer["in_title"] and not offer["in_visible_title"] \
        and offer["visible_title"] == "A legjobbat érdemled" and offer["article_basis"] is None
    # a látható cím egy kisebb címsor, de az oldal H1-e megnevezi a fő entitást: nem jelölt
    outside = next(page for page in site_views(con, "pelda").pages
                   if page.url == f"{BASE}/kulso/")
    assert (outside.visible_title, outside.visible_title_element) == ("Videók", "h3")
    assert outside.main_entity == "Külső marketing" and "/kulso/" not in found
    severities = {s for (s,) in con.execute(
        "SELECT severity FROM findings WHERE type = 'title_without_main_entity'").fetchall()}
    assert severities == {"low"}
    assert run.by_type()["title_without_main_entity"] == 2      # két cikk egy megállapításban


def test_title_fields_in_the_page_view(tmp_path):
    con, _ = built()
    pages = {page.url.removeprefix(BASE): page for page in site_views(con, "pelda").pages}
    speed = pages["/blog/sebesseg/"]
    assert speed.title == "Amikor az oldalsebesség üzleti kérdés lett · Pelda"      # nyersen
    assert speed.title_cut == speed.visible_title == speed.og_title \
        == "Amikor az oldalsebesség üzleti kérdés lett"
    assert speed.visible_title_element == "h1" and speed.h1_count == 1
    assert [(item.field, item.text) for item in speed.schema_titles] == [
        ("headline (BlogPosting)", "Amikor az oldalsebesség üzleti kérdés lett")]
    assert speed.title_differences == []
    (about,) = speed.schema_about
    assert about.name == "Oldalsebesség" and about.entity == "Oldalsebesség" \
        and about.type == "concept" and about.same_as_main is True
    # az „&” és az „és” nem eltérés; a nyers értékek megmaradnak
    ads = pages["/blog/hirdetes/"]
    assert ads.title_cut == "Meta Ads tippek & trükkök" \
        and ads.visible_title == "Meta Ads tippek és trükkök" and ads.title_differences == []
    offer = pages["/szolgaltatas/"]
    assert "title ≠ látható cím" in offer.title_differences
    assert ("name (Service)", "Jogi marketing") in [(i.field, i.text)
                                                    for i in offer.schema_titles]
    paths = export_views(con, tmp_path, "pelda")
    with paths["pages"].open(encoding="utf-8-sig", newline="") as handle:
        rows = {r["url"].removeprefix(BASE): r for r in csv.DictReader(handle)}
    assert set(findings.TITLE_COLUMNS) <= set(rows["/"])
    assert rows["/blog/sebesseg/"]["az about és a fő entitás"] == \
        "Oldalsebesség (concept): azonos a fő entitással"
    assert rows["/szolgaltatas/"]["látható cím"] == "A legjobbat érdemled"
    assert "A cím nem nevezi meg a fő entitást" in rows["/blog/gorbe/"]["megállapítások"]
