"""A SEO-megállapítások és a nézetek (aaa2/functions/findings.py) szintetikus site-on, hálózat
és LLM nélkül."""
import csv
import json

from aaa2.entities.rules import run_rules
from aaa2.entities.site import run_site
from aaa2.functions import findings
from aaa2.functions.findings import (
    PAGINATION,
    build_findings,
    export_findings,
    export_views,
    names_in,
)
from aaa2.functions.graph import build_graph
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON, llm_entity
from tests.test_graph import primary

BASE = "https://pelda.hu"


def service(name, path):
    return ld({"@type": "Service", "name": name, "url": f"{BASE}{path}"})


def findings_site():
    post = ld({"@type": "BlogPosting", "headline": "x"})
    topic = "<h2>Konverzió</h2><p>A konverzió számít.</p>"
    return site({
        "/": html("Pelda", "<main><h1>Üdvözlünk</h1><p>Üdv.</p></main>"),
        "/meres/": html("Mérés · Pelda", f"<main><h1>Mérés nélkül csak találgatás</h1>{topic}"
                        "</main>", head=service("Mérés", "/meres/")),
        "/ux/": html("UX optimalizálás · Pelda", "<main><h1>A súrlódás a gond. Nem a forgalom."
                     f"</h1>{topic}</main>", head=service("UX optimalizálás", "/ux/")),
        "/seo/": html("Szolgáltatás · Pelda", f"<main><h1>SEO tanácsadás</h1>{topic}</main>",
                      head=service("SEO tanácsadás", "/seo/")),
        "/blog/a/": html("Adatarchitektúra kezdőknek · Pelda", "<main><h1>Adatarchitektúra "
                         "kezdőknek</h1><p>Az adatarchitektúra alapjai.</p></main>", head=post),
        "/blog/b/": html("Adatarchitektúra haladóknak · Pelda", "<main><h1>Adatarchitektúra "
                         "haladóknak</h1><p>Az adatarchitektúra mélyebben.</p></main>",
                         head=post),
        "/blog/a/reszletek/": html("Adatarchitektúra: részletek · Pelda", "<main><h1>"
                                   "Adatarchitektúra: részletek</h1><p>Az adatarchitektúra "
                                   "részletei.</p></main>", head=post),
        "/blog/vegyes/": html("Vegyes · Pelda", "<main><h1>Vegyes gondolatok</h1>"
                              "<h2>Riportolás</h2><p>Szöveg.</p></main>", head=post)})


def built(monkeypatch):
    monkeypatch.setattr(findings, "TOP_SHARE", 1.0)        # a kis site minden entitása számít
    con = findings_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    for path in ("/blog/a/", "/blog/b/", "/blog/a/reszletek/"):
        llm_entity(con, f"{BASE}{path}", "Adatarchitektúra", "Adatarchitektúra", "concept")
        primary(con, f"{BASE}{path}", ["Adatarchitektúra"])
    headings(con)
    llm_entity(con, f"{BASE}/blog/vegyes/", "Riportolás", "Riportolás", "concept")
    con.execute("UPDATE page_entities SET position = 'heading' WHERE surface_form = 'Riportolás'")
    build_graph(con)
    return con, build_findings(con)


def headings(con):
    """A „Konverzió” fogalom a három ajánlatoldal H2-jében."""
    for path in ("/meres/", "/ux/", "/seo/"):
        llm_entity(con, f"{BASE}{path}", "Konverzió", "Konverzió", "concept")
        llm_entity(con, f"{BASE}{path}", "konverzió számít", "Konverzió", "concept")
    con.execute("UPDATE page_entities SET position = 'heading' WHERE surface_form = 'Konverzió'")


def rows(con, kind):
    return [(severity, summary, json.loads(evidence)) for severity, summary, evidence in
            con.execute("SELECT severity, summary, evidence FROM findings WHERE type = ? "
                        "ORDER BY finding_id", [kind]).fetchall()]


def test_h1_and_title_mismatches(monkeypatch):
    con, run = built(monkeypatch)
    found = {e["url"]: (severity, e["problems"]) for severity, _, e in
             rows(con, "h1_title_mismatch")}
    assert found == {
        f"{BASE}/ux/": ("medium", ["a H1 általános: nincs benne entitás"]),   # szlogen a H1
        f"{BASE}/seo/": ("high", ["a fő entitás nincs a title-ben"])}
    # a kezdőoldal H1-e nem megállapítás; a /meres/ H1-e és title-je megnevezi az entitást
    assert run.by_type()["h1_title_mismatch"] == 2


def test_cannibalization_skips_children_and_needs_two_groups(monkeypatch):
    con, _ = built(monkeypatch)
    ((severity, summary, evidence),) = rows(con, "cannibalization")
    assert severity == "medium" and summary.startswith("Adatarchitektúra: 2 oldal")
    assert [p["url"] for p in evidence["pages"]] == [f"{BASE}/blog/a/", f"{BASE}/blog/b/"]
    assert findings._parent_child(f"{BASE}/blog/a/", f"{BASE}/blog/a/reszletek/")
    assert not findings._parent_child(f"{BASE}/blog/a/", f"{BASE}/blog/b/")
    assert PAGINATION.search(f"{BASE}/spl/1/X?infinite_page=2")
    assert PAGINATION.search(f"{BASE}/blog/page/3/") and not PAGINATION.search(f"{BASE}/blog/")


def test_missing_page_and_unclear_topic(monkeypatch):
    con, run = built(monkeypatch)
    missing = rows(con, "missing_page")
    assert [(severity, e["entity"], e["page_count"]) for severity, _, e in missing] == [
        ("medium", "Konverzió", 3)]         # három oldal headingjében, hat említés, oldal nélkül
    assert run.missing_literal >= 1
    ((severity, summary, evidence),) = rows(con, "unclear_topic")
    assert severity == "medium" and evidence["url"] == f"{BASE}/blog/vegyes/"
    assert "Riportolás" in summary


def test_a_covered_entity_is_not_a_missing_page(monkeypatch):
    monkeypatch.setattr(findings, "TOP_SHARE", 1.0)
    con = findings_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    headings(con)
    con.execute("UPDATE pages SET h1 = 'Konverzió' WHERE url = ?", [f"{BASE}/blog/vegyes/"])
    build_graph(con)
    run = build_findings(con)
    assert rows(con, "missing_page") == [] and run.missing_literal >= 1   # van ilyen H1-ű oldal


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
    assert {r["típus"] for r in exported} == {"H1/title-eltérés", "Kannibalizáció",
                                               "Hiányzó oldal", "Nem egyértelmű téma"}
    paths = export_views(con, tmp_path, "pelda")
    with paths["pages"].open(encoding="utf-8-sig", newline="") as handle:
        pages = {r["url"]: r for r in csv.DictReader(handle)}
    assert pages[f"{BASE}/ux/"]["a H1-ben"] == "nem"
    assert pages[f"{BASE}/ux/"]["a title-ben"] == "igen"
    assert "H1/title-eltérés" in pages[f"{BASE}/ux/"]["megállapítások"]
    assert "Kannibalizáció" in pages[f"{BASE}/blog/a/"]["megállapítások"]
    with paths["entities"].open(encoding="utf-8-sig", newline="") as handle:
        entities = {r["entitás"]: r for r in csv.DictReader(handle)}
    assert f"{BASE}/blog/a/" in entities["Adatarchitektúra"]["fő oldalak"]
    assert entities["Konverzió"]["csak említő oldalak száma"] == "3"
    with paths["site"].open(encoding="utf-8-sig", newline="") as handle:
        assert {r["típus"] for r in csv.DictReader(handle)} >= {"concept", "service"}
    page = paths["html"].read_text(encoding="utf-8")
    assert page.count("<details") > 10 and "Site-áttekintő" in page and "Oldalnézet" in page
