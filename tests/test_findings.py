"""A SEO-megállapítások és a nézetek (aaa2/functions/findings.py) szintetikus site-on, hálózat
és LLM nélkül."""
import csv
import json

from aaa2.entities.rules import run_rules
from aaa2.functions import findings
from aaa2.functions.findings import (
    PAGINATION,
    build_findings,
    exclusion,
    export_findings,
    export_views,
    is_context,
    names_in,
    title_similarity,
)
from aaa2.functions.graph import build_graph
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON, llm_entity
from tests.test_graph import primary

BASE = "https://pelda.hu"
TOPICS = {"Adatarchitektúra": ("/blog/a/", "/blog/b/", "/blog/a/reszletek/"),
          "Mérési terv": ("/blog/c/", "/blog/d/")}


def service(name, path):
    return ld({"@type": "Service", "name": name, "url": f"{BASE}{path}"})


def post(title, text):
    return html(f"{title} · Pelda", f"<main><h1>{title}</h1><p>{text}</p></main>",
                head=ld({"@type": "BlogPosting", "headline": "x"}))


def findings_site():
    topic = ("<h2>Konverzió</h2><p>A konverzió számít.</p>"
             "<h2>Nordic széria</h2><p>A Nordic széria tagjai.</p>"
             "<h2>Nordic Plus alcsalád</h2><p>A Nordic Plus alcsalád tagjai.</p>")
    return site({
        "/": html("Pelda", "<main><h1>Üdvözlünk</h1><p>Üdv.</p></main>"),
        "/meres/": html("Mérés · Pelda", f"<main><h1>Mérés nélkül csak találgatás</h1>{topic}"
                        "</main>", head=service("Mérés", "/meres/")),
        "/ux/": html("UX optimalizálás · Pelda", "<main><h1>A súrlódás a gond. Nem a forgalom."
                     f"</h1>{topic}</main>", head=service("UX optimalizálás", "/ux/")),
        "/geo/": html("GEO láthatóság · Pelda", "<main><h1>A márkád nem jelenik meg.</h1>"
                      "<p>Szöveg.</p></main>", head=service("GEO láthatóság", "/geo/")),
        "/seo/": html("Szolgáltatás · Pelda", f"<main><h1>SEO tanácsadás</h1>{topic}</main>",
                      head=service("SEO tanácsadás", "/seo/")),
        "/blog/a/": post("Adatarchitektúra kezdőknek", "Az adatarchitektúra alapjai."),
        "/blog/b/": post("Adatarchitektúra haladóknak", "Az adatarchitektúra mélyebben."),
        "/blog/a/reszletek/": post("Adatarchitektúra: részletek", "Az adatarchitektúra "
                                   "részletei."),
        "/blog/c/": post("Mérési terv készítése", "A mérési terv lépései."),
        "/blog/d/": post("Mérési terv készítése lépésenként", "A mérési terv részletesen."),
        "/blog/vegyes/": html("Vegyes · Pelda", "<main><h1>Vegyes gondolatok</h1>"
                              "<h2>Riportolás</h2><p>Szöveg.</p></main>",
                              head=ld({"@type": "BlogPosting", "headline": "x"}))})


def headings(con):
    """A „Konverzió” fogalom és a „Nordic széria” termékcsalád a három ajánlatoldal H2-jében."""
    for path in ("/meres/", "/ux/", "/seo/"):
        llm_entity(con, f"{BASE}{path}", "Konverzió", "Konverzió", "concept")
        llm_entity(con, f"{BASE}{path}", "konverzió számít", "Konverzió", "concept")
        llm_entity(con, f"{BASE}{path}", "Nordic széria", "Nordic széria", "product", "line")
        llm_entity(con, f"{BASE}{path}", "Nordic széria tagjai", "Nordic széria", "product",
                   "line")
        llm_entity(con, f"{BASE}{path}", "Nordic Plus alcsalád", "Nordic Plus", "product",
                   "line")
        llm_entity(con, f"{BASE}{path}", "Nordic Plus alcsalád tagjai", "Nordic Plus", "product",
                   "line")
    con.execute("UPDATE page_entities SET position = 'heading' WHERE surface_form IN "
                "('Konverzió', 'Nordic széria', 'Nordic Plus alcsalád')")
    ids = dict(con.execute("SELECT name, entity_id FROM entities WHERE type = 'product'"
                           ).fetchall())
    con.execute("INSERT INTO entity_relations (from_id, to_id, type, source) VALUES "
                "(?, ?, 'part_of', 'shop') ON CONFLICT DO NOTHING",
                [ids["Nordic Plus"], ids["Nordic széria"]])


def built(monkeypatch):
    monkeypatch.setattr(findings, "TOP_SHARE", 1.0)        # a kis site minden entitása számít
    con = findings_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    for name, paths in TOPICS.items():
        for path in paths:
            llm_entity(con, f"{BASE}{path}", name, name, "concept")
            primary(con, f"{BASE}{path}", [name])
    headings(con)
    llm_entity(con, f"{BASE}/blog/vegyes/", "Riportolás", "Riportolás", "concept")
    con.execute("UPDATE page_entities SET position = 'heading' WHERE surface_form = 'Riportolás'")
    build_graph(con)
    return con, build_findings(con)


def rows(con, kind):
    return [(severity, summary, json.loads(evidence)) for severity, summary, evidence in
            con.execute("SELECT severity, summary, evidence FROM findings WHERE type = ? "
                        "ORDER BY finding_id", [kind]).fetchall()]


def test_h1_and_title_mismatches_grouped_by_cause_and_role(monkeypatch):
    con, run = built(monkeypatch)
    found = rows(con, "h1_title_mismatch")
    single = {e["url"]: (severity, e["problems"]) for severity, _, e in found if "url" in e}
    assert single == {f"{BASE}/seo/": ("high", ["a fő entitás nincs a title-ben"])}
    ((severity, summary, evidence),) = [f for f in found if "pages" in f[2]]
    assert severity == "medium" and summary == "a H1 általános: nincs benne entitás: " \
                                               "2 ajánlatoldal"                 # szlogen a H1
    assert [p["url"] for p in evidence["pages"]] == [f"{BASE}/geo/", f"{BASE}/ux/"]
    # a kezdőoldal H1-e nem megállapítás; a /meres/ H1-e és title-je megnevezi az entitást
    assert run.by_type()["h1_title_mismatch"] == 2


def test_shared_topic_and_cannibalization(monkeypatch):
    con, _ = built(monkeypatch)
    ((severity, summary, evidence),) = rows(con, "shared_topic")      # más-más szög
    assert severity == "low" and summary.startswith("Adatarchitektúra: 2 oldal közös témája")
    assert [p["url"] for p in evidence["pages"]] == [f"{BASE}/blog/a/", f"{BASE}/blog/b/"]
    ((severity, summary, evidence),) = rows(con, "cannibalization")   # nagyon hasonló title
    assert severity == "medium" and summary.startswith("Mérési terv: lehetséges kannibalizáció: 2 oldal")
    assert evidence["overlaps"] == [{"pages": [f"{BASE}/blog/c/", f"{BASE}/blog/d/"],
                                     "secondary": [], "title_similarity": 0.75,
                                     "relation": "testvér", "content_links": "nincs",
                                     "any_link": False}]
    assert title_similarity("A – B C · Pelda", "A – B C · Pelda") == 1.0
    assert title_similarity("Alfa béta", "Gamma delta") == 0.0
    assert findings._parent_child(f"{BASE}/blog/a/", f"{BASE}/blog/a/reszletek/")
    assert not findings._parent_child(f"{BASE}/blog/a/", f"{BASE}/blog/b/")
    assert PAGINATION.search(f"{BASE}/spl/1/X?infinite_page=2")
    assert PAGINATION.search(f"{BASE}/blog/page/3/") and not PAGINATION.search(f"{BASE}/blog/")


def test_uncovered_topic_missing_family_page_and_unclear_topic(monkeypatch):
    con, run = built(monkeypatch)
    ((severity, _, evidence),) = rows(con, "uncovered_topic")
    assert (severity, evidence["entity"], evidence["page_groups"]) == ("low", "Konverzió", 3)
    assert "cikk" in evidence["action"]                   # fogalom: nem feltétlenül új oldal
    ((severity, summary, evidence),) = rows(con, "missing_page")
    assert (severity, evidence["entity"]) == ("medium", "Nordic széria")     # termékcsalád
    assert "családoldal" in evidence["action"]
    # az alcsalád a szülő megállapításában áll, nem külön
    assert evidence["subfamilies"] == [{"entity": "Nordic Plus", "page_count": 3}]
    assert summary.endswith("alcsaládjai: Nordic Plus")
    assert run.missing_literal >= 3 and run.context == []
    ((severity, summary, evidence),) = rows(con, "unclear_topic")
    assert severity == "medium" and evidence["url"] == f"{BASE}/blog/vegyes/"
    assert "Riportolás" in summary


def test_context_and_covered_entities_are_not_uncovered(monkeypatch):
    monkeypatch.setattr(findings, "TOP_SHARE", 1.0)
    con = findings_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    headings(con)
    # a „Konverzió” említései chrome-helyen (pl. oldalsáv-doboz): kontextus, nem lefedetlen téma
    con.execute("UPDATE blocks SET region = 'chrome' WHERE block_id IN (SELECT block_id FROM "
                "page_entities WHERE surface_form IN ('Konverzió', 'konverzió számít'))")
    build_graph(con)
    run = build_findings(con)
    assert rows(con, "uncovered_topic") == [] and run.context == ["Konverzió"]
    assert len(rows(con, "missing_page")) == 1              # a tartalomban álló család marad
    con.execute("UPDATE blocks SET region = 'content'")
    con.execute("UPDATE pages SET h1 = 'Konverzió' WHERE url = ?", [f"{BASE}/blog/vegyes/"])
    build_graph(con)
    run = build_findings(con)
    assert rows(con, "uncovered_topic") == [] and run.context == []       # van ilyen H1-ű oldal
    # csak a title említi: mellékes, a téma lefedetlen marad
    con.execute("UPDATE pages SET h1 = 'Vegyes gondolatok', title = 'A konverzió növelése' "
                "WHERE url = ?", [f"{BASE}/blog/vegyes/"])
    build_graph(con)
    build_findings(con)
    assert [e["entity"] for _, _, e in rows(con, "uncovered_topic")] == ["Konverzió"]
    # ajánlatoldalon a title önmagában is megnevezés
    con.execute("UPDATE page_nodes SET role = 'offer' WHERE url = ?", [f"{BASE}/blog/vegyes/"])
    site = findings._Site(con)
    covered = {site.name(c["entity_id"]): exclusion(c)
               for c in findings.uncovered_candidates(site)}
    assert covered["Konverzió"] == "headline"
    # a H1 és a title is megnevezi: van róla szóló oldal, nem lefedetlen téma
    con.execute("UPDATE pages SET h1 = 'A konverzió növelése' WHERE url = ?",
                [f"{BASE}/blog/vegyes/"])
    build_graph(con)
    build_findings(con)
    assert rows(con, "uncovered_topic") == []
    assert len(rows(con, "missing_page")) == 1          # a szülőcsaládra nem vonatkozik
    # a rövid alias, amely egy másik entitás nevének szava, nem megnevezés
    con.execute("UPDATE pages SET h1 = 'KV Search útmutató', title = 'KV Search útmutató' "
                "WHERE url = ?", [f"{BASE}/blog/vegyes/"])
    con.execute("UPDATE entities SET aliases = ['KV'] WHERE name = 'Konverzió'")
    con.execute("INSERT INTO entities (name, type, aliases, source, created_at) VALUES "
                "('KV Search', 'concept', [], 'llm', ?)", [NOON])
    build_graph(con)
    build_findings(con)
    assert [e["entity"] for _, _, e in rows(con, "uncovered_topic")] == ["Konverzió"]
    base = {"template_share": 0.0, "in_site_name": False, "headline": None,
            "common_word": False, "parent": False}
    assert exclusion(base) is None
    assert exclusion({**base, "headline": "https://x.hu/a/"}) == "headline"
    assert exclusion({**base, "headline": "https://x.hu/a/", "parent": True}) is None
    assert exclusion({**base, "common_word": True}) == "common_word"     # „stratégia”
    assert exclusion({**base, "template_share": 0.9}) == "context"
    assert is_context({"template_share": 0.0, "in_site_name": True})      # a site nevében
    assert is_context({"template_share": 0.5, "in_site_name": False})
    assert not is_context({"template_share": 0.04, "in_site_name": False})   # sok oldalon tárgyalt


def test_names_in_text():
    assert names_in(["Tracking and Measurement"], "Tracking & Measurement")
    assert not names_in(["Mérés"], "Kimérés nélkül")
    name = "LG LZ-H025GBA4 ERV Hővisszanyerős szellőztető készülék 250 m3/h, távirányítóval"
    cut = "LG LZ-H025GBA4 ERV Hővisszanyerős szellőztető készülék 250 m"
    assert names_in([name], cut, cut=True) and not names_in([name], cut)
    assert not names_in(["Mérés"], None)
    assert names_in(["Eprivacy and GDPR diagnostics"], "E-Privacy & GDPR Diagnostics")
    long = "Purchase Consideration Services - Mid-Funnel Marketing Strategy"
    assert names_in([long], "Purchase Consideration - Pelda")         # a név rövid alakja
    assert not names_in([long], "Consideration - Pelda")              # egy szó kevés
    assert not names_in(["Online and Offline Sales Conversion Strategy"],
                        "Grow Online and Offline Sales")


def test_views_and_findings_export(monkeypatch, tmp_path):
    con, _ = built(monkeypatch)
    path = export_findings(con, tmp_path, "pelda")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert {r["típus"] for r in exported} == {
        "H1/title-eltérés", "Lehetséges kannibalizáció", "Közös téma", "Hiányzó oldal", "Lefedetlen téma",
        "Nem egyértelmű téma"}
    grouped = next(r for r in exported if "2 ajánlatoldal" in r["összefoglaló"])
    assert grouped["oldalak"] == f"{BASE}/geo/ | {BASE}/ux/"
    paths = export_views(con, tmp_path, "pelda")
    with paths["pages"].open(encoding="utf-8-sig", newline="") as handle:
        pages = {r["url"]: r for r in csv.DictReader(handle)}
    assert pages[f"{BASE}/ux/"]["a H1-ben"] == "nem"
    assert pages[f"{BASE}/ux/"]["a title-ben"] == "igen"
    assert "H1/title-eltérés" in pages[f"{BASE}/ux/"]["megállapítások"]
    assert "Közös téma" in pages[f"{BASE}/blog/a/"]["megállapítások"]
    assert "Lehetséges kannibalizáció" in pages[f"{BASE}/blog/c/"]["megállapítások"]
    with paths["entities"].open(encoding="utf-8-sig", newline="") as handle:
        entities = {r["entitás"]: r for r in csv.DictReader(handle)}
    assert f"{BASE}/blog/a/" in entities["Adatarchitektúra"]["fő oldalak"]
    assert entities["Konverzió"]["csak említő oldalak száma"] == "3"
    with paths["site"].open(encoding="utf-8-sig", newline="") as handle:
        assert {r["típus"] for r in csv.DictReader(handle)} >= {"concept", "service"}
    page = paths["html"].read_text(encoding="utf-8")
    assert page.count("<details") > 10 and "Site-áttekintő" in page and "Oldalnézet" in page
