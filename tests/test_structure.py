"""A menüterületek felismerése (aaa2/engine/menu.py), a tárolt menüfa és az oldalmélységek
(aaa2/functions/structure.py), valamint a két struktúra-megállapítás szintetikus, kétnyelvű
site-on, hálózat és LLM nélkül."""
import csv

from aaa2.engine.menu import menu_entries, visible_breadcrumb
from aaa2.entities.rules import run_rules
from aaa2.functions.findings import build_findings, export_views, site_views, stored_findings
from aaa2.functions.graph import build_graph
from aaa2.functions.structure import menu_differences, menu_items, name_key
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON

BASE = "https://pelda.hu"


def entries(body, area="header", homes=()):
    return [(entry.url and entry.url.removeprefix(BASE), entry.anchor,
             entry.parent and entry.parent.removeprefix(BASE), entry.marker)
            for entry in menu_entries(f"<html><body>{body}</body></html>", f"{BASE}/x/",
                                      frozenset(homes)) if entry.area == area]


def test_breadcrumb_pagination_and_in_content_navigation_are_not_menu():
    body = ('<header><nav><a href="/a/">A</a><a href="/b/">B</a></nav></header>'
            '<nav aria-label="Breadcrumb"><a href="/">Kezdőlap</a><a href="/a/">A</a></nav>'
            '<ol class="breadcrumbs"><li><a href="/">Kezdőlap</a></li></ol>'
            '<nav><a href="/x/?page=1">1</a><a rel="next" href="/x/?page=2">2</a></nav>'
            '<nav class="page-numbers"><a href="/x/?page=3">3</a></nav>'
            '<main><nav class="szuro"><a href="/c/">C</a></nav>'
            '<article><header><a href="/d/">D</a></header></article></main>')
    assert entries(body) == [("/a/", "A", None, None), ("/b/", "B", None, None)]
    # a területen belüli morzsa és lapozó is kimarad, a többi menüpont marad
    inside = ('<header><div class="breadcrumb"><a href="/">Kezdőlap</a></div>'
              '<ul class="pagination"><li><a href="/x/?page=2">2</a></li></ul>'
              '<a href="/a/">A</a><a rel="prev" href="/x/?page=1">előző</a></header>')
    assert entries(inside) == [("/a/", "A", None, None)]


def test_footer_and_sidebar_are_separate_trees():
    body = ('<header><a href="/a/">A</a></header>'
            '<aside><ul><li><a href="/k/">Kategória</a><ul><li><a href="/k/egy/">Egy</a></li>'
            '<li><a href="/k/ketto/">Kettő</a></li></ul></li></ul>'
            '<article><a href="/hir/">Hír</a></article></aside>'
            '<div class="docs-sidebar"><a href="/doc/">Doksi</a></div>'
            '<footer><nav><a href="/adat/">Adatvédelem</a><a href="/aszf/">ÁSZF</a></nav>'
            '<aside><a href="/impresszum/">Impresszum</a></aside></footer>')
    assert entries(body) == [("/a/", "A", None, None)]
    # az oldalsáv fája szülő–gyerek viszonnyal; a benne álló tartalomkártya nem menüpont
    assert entries(body, "sidebar") == [
        ("/k/", "Kategória", None, None), ("/k/egy/", "Egy", "/k/", None),
        ("/k/ketto/", "Kettő", "/k/", None), ("/doc/", "Doksi", None, None)]
    assert entries(body, "footer") == [("/adat/", "Adatvédelem", None, None),
                                       ("/aszf/", "ÁSZF", None, None),
                                       ("/impresszum/", "Impresszum", None, None)]


def test_label_parents_logo_and_home_links():
    body = ('<header><div class="site-logo"><a href="/"><img alt="Pelda" src="l.png"></a></div>'
            '<ul><li><a href="#">Termékek</a><ul><li><a href="/t/egy/">Egy</a></li>'
            '<li><a href="/t/ketto/">Kettő</a></li></ul></li>'
            '<li><a href="/hu/">HU</a><ul><li><a href="/hu/rolunk/">Rólunk</a></li>'
            '<li><a href="/hu/blog/">Blog</a></li></ul></li>'
            '<li><a href="tel:+361">Telefon</a></li><li><a href="#">Üres</a></li>'
            '<li><a class="home" href="/kezdo/"><svg></svg></a></li></ul></header>')
    assert entries(body, homes=[f"{BASE}/", f"{BASE}/hu/"]) == [
        ("/", "Pelda", None, "logo"),
        (None, "Termékek", None, None),                       # link nélküli szülő-címke
        ("/t/egy/", "Egy", "(címke: Termékek)", None),
        ("/t/ketto/", "Kettő", "(címke: Termékek)", None),
        # a kezdőoldal nem almenü szülője, és a címhierarchia sem teszi azzá
        ("/hu/", "HU", None, None), ("/hu/rolunk/", "Rólunk", None, None),
        ("/hu/blog/", "Blog", None, None),
        ("/kezdo/", "", None, "home_icon")]
    # szöveg nélküli link nem almenü szülője (a hibás jelölés üres linkje sem)
    empty = ('<footer><div><a href="https://partner.hu/"></a></div>'
             '<p><a href="/adat/">Adatvédelem</a></p></footer>')
    assert entries(empty, "footer") == [("https://partner.hu/", "", None, None),
                                        ("/adat/", "Adatvédelem", None, None)]
    # a `<base href>` a relatív címek alapja, az alapértelmezett port nélkül
    based = ('<html><head><base href="https://pelda.hu:443/"></head><body><header>'
             '<a href="index.php?route=fiok">Fiók</a></header></body></html>')
    assert [entry.url for entry in menu_entries(based, f"{BASE}/mely/oldal")] == [
        f"{BASE}/index.php?route=fiok"]


def test_the_visible_breadcrumb_is_read_in_order_with_the_closing_text():
    page = ('<html><body><nav aria-label="breadcrumb"><a href="/">Kezdőlap</a> / '
            '<a href="/blog/">Blog</a> / <span>Cikk</span></nav></body></html>')
    assert visible_breadcrumb(page, f"{BASE}/blog/cikk/") == [
        (f"{BASE}/", "Kezdőlap"), (f"{BASE}/blog/", "Blog"), (None, "Cikk")]
    assert visible_breadcrumb("<html><body><p>Szöveg</p></body></html>", BASE) is None


PAIRS = {"/": "/hu/", "/szolg/": "/hu/szolg/", "/szolg/seo/": "/hu/szolg/seo/",
         "/szolg/meres/": "/hu/szolg/meres/", "/blog/": "/hu/blog/",
         "/blog/cikk/": "/hu/blog/cikk/", "/adat/": "/hu/adat/", "/landing/": "/hu/",
         "/hu/arva/": "/"}
OTHER = {**PAIRS, **{hu: en for en, hu in PAIRS.items()
                     if en not in ("/landing/", "/hu/arva/")}}
CRUMB_NAMES = {"szolg": "Szolgáltatások", "seo": "SEO", "meres": "Mérés", "blog": "Blog",
               "cikk": "Cikk", "adat": "Adatvédelem", "landing": "Landing", "arva": "Árva"}


def structure_page(path, flat_crumbs=False):
    lang = "hu" if path.startswith("/hu/") else "en"
    prefix = "/hu" if lang == "hu" else ""
    home = f"{prefix}/"
    home_item = {"en": '<a href="/landing/">Home</a>',
                 "hu": '<a href="/hu/">Főoldal</a>'}[lang]
    header = (
        f'<header><a class="logo" href="{home}">Pelda</a><nav><ul><li>{home_item}</li>'
        f'<li><a href="{prefix}/szolg/">Szolgáltatások</a><ul>'
        f'<li><a href="{prefix}/szolg/seo/">SEO</a></li>'
        f'<li><a href="{prefix}/szolg/meres/">Mérés</a></li></ul></li>'
        f'<li><a href="{prefix}/blog/">Blog</a></li></ul>'
        f'<a class="nyelv" href="{OTHER[path]}">{"EN" if lang == "hu" else "HU"}</a>'
        "</nav></header>")
    parts = [part for part in path.removeprefix(prefix).split("/") if part]
    trail = [{"@type": "ListItem", "position": 1, "name": "Home", "item": f"{BASE}/"}]
    for index, part in enumerate(parts, start=1):
        if flat_crumbs and index < len(parts):
            continue
        trail.append({"@type": "ListItem", "position": index + 1, "name": CRUMB_NAMES[part],
                      "item": f"{BASE}{prefix}/{'/'.join(parts[:index])}/"})
    head = ld({"@type": "BreadcrumbList", "itemListElement": trail}) if parts else ""
    body = "<p>Szöveg az oldalról.</p>"
    if not parts:
        body = f'<p>A <a href="{prefix}/szolg/">szolgáltatások</a> itt.</p>'
    if parts[:1] == ["blog"]:
        body = (f'<nav class="szuro"><a href="{prefix}/szolg/seo/">SEO-cikkek</a></nav>'
                f'<p><a href="{prefix}/blog/cikk/">A cikk</a></p>')
    sidebar = (f'<aside><ul><li><a href="{prefix}/blog/cikk/">Friss cikk</a></li></ul></aside>'
               if parts[:1] == ["blog"] else "")
    title = CRUMB_NAMES[parts[-1]] if parts else "Pelda"
    return html(f"{title} · Pelda",
                f'{header}<nav aria-label="breadcrumb"><a href="{home}">Home</a></nav>'
                f"<main><h1>{title}</h1>{body}</main>{sidebar}"
                f'<footer><a href="{prefix}/adat/">Adatvédelem</a></footer>',
                head=head, lang=lang)


def structure_site():
    con = site({path: structure_page(path) for path in sorted({*PAIRS, *PAIRS.values()})},
               languages=("en", "hu"))
    con.execute("UPDATE site SET home_urls = ?", [[f"{BASE}/", f"{BASE}/hu/"]])
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    return con


def test_the_stored_menu_tree_per_language_and_area():
    con = structure_site()
    items = menu_items(con)
    tree = [(item.lang, item.area, item.level, item.anchor, (item.url or "").removeprefix(BASE),
             item.marker, item.pages, item.area_pages) for item in items]
    assert tree == [
        ("en", "header", 0, "Pelda", "/", "logo", 8, 8),
        ("en", "header", 0, "Home", "/landing/", None, 8, 8),
        ("en", "header", 0, "Szolgáltatások", "/szolg/", None, 8, 8),
        ("en", "header", 1, "SEO", "/szolg/seo/", None, 8, 8),
        ("en", "header", 1, "Mérés", "/szolg/meres/", None, 8, 8),
        ("en", "header", 0, "Blog", "/blog/", None, 8, 8),
        ("en", "footer", 0, "Adatvédelem", "/adat/", None, 8, 8),
        ("en", "sidebar", 0, "Friss cikk", "/blog/cikk/", None, 2, 2),
        # a „Főoldal” menüpont a logóval azonos címre mutat: egy menüpont
        ("hu", "header", 0, "Pelda", "/hu/", "logo", 8, 8),
        ("hu", "header", 0, "Szolgáltatások", "/hu/szolg/", None, 8, 8),
        ("hu", "header", 1, "SEO", "/hu/szolg/seo/", None, 8, 8),
        ("hu", "header", 1, "Mérés", "/hu/szolg/meres/", None, 8, 8),
        ("hu", "header", 0, "Blog", "/hu/blog/", None, 8, 8),
        ("hu", "footer", 0, "Adatvédelem", "/hu/adat/", None, 8, 8),
        ("hu", "sidebar", 0, "Friss cikk", "/hu/blog/cikk/", None, 2, 2)]
    by_id = {item.item_id: item for item in items}
    seo = next(item for item in items if item.url == f"{BASE}/hu/szolg/seo/")
    assert by_id[seo.parent_id].url == f"{BASE}/hu/szolg/" and seo.page_id is not None
    # a nyelvváltó, a morzsa és a tartalmon belüli szűrő sehol nem menüpont: nincs eltérő oldal
    assert menu_differences(con) == []


def test_page_depths_levels_and_parents_in_the_page_view(tmp_path):
    con = structure_site()
    views = site_views(con, "pelda")
    pages = {page.url.removeprefix(BASE): page for page in views.pages}
    # mélység a saját nyelvű kezdőoldaltól: a menü egy lépés, a cikk kettő
    assert [pages[path].click_depth for path in ("/", "/szolg/seo/", "/blog/cikk/", "/adat/")] \
        == [0, 1, 2, 1]
    assert [pages[path].click_depth for path in ("/hu/", "/hu/szolg/seo/", "/hu/blog/cikk/")] \
        == [0, 1, 2]
    assert pages["/szolg/"].click_depth_content == 1                # a kezdőoldal tartalmából
    assert pages["/szolg/seo/"].click_depth_content is None
    assert pages["/szolg/seo/"].click_depth_menu == 1
    orphan = pages["/hu/arva/"]
    assert orphan.unreachable is True and orphan.click_depth is None
    assert pages["/hu/szolg/"].unreachable is False
    # menüszint, lábléc és oldalsáv
    assert pages["/hu/szolg/"].menu_level == 0 and pages["/hu/szolg/seo/"].menu_level == 1
    assert pages["/hu/blog/cikk/"].menu_level is None
    assert pages["/hu/adat/"].in_footer_menu is True and pages["/hu/blog/"].in_footer_menu is False
    assert pages["/hu/blog/cikk/"].in_sidebar_menu is True
    # morzsa- és URL-szint, szülők
    seo = pages["/hu/szolg/seo/"]
    assert (seo.breadcrumb_level, seo.url_level) == (2, 3)
    assert seo.menu_parent == seo.breadcrumb_parent == seo.url_parent == f"{BASE}/hu/szolg/"
    assert seo.parents_agree is True
    services = pages["/hu/szolg/"]
    # az egyszakaszos oldalnál az URL nem mond szülőt
    assert services.menu_parent == "(felső szint)" and services.url_parent is None
    assert services.url_parent_applicable is False and seo.url_parent_applicable is True
    # a morzsa a másik nyelv kezdőoldalára mutat: ez nem szülő-eltérés (külön megállapítás)
    assert services.breadcrumb_parent == f"{BASE}/"
    assert services.parents_agree is True
    assert pages["/hu/"].parents_agree is None
    # site-szint: menüfa a mélységgel, eloszlás nyelvenként, elérhetetlen oldalak
    structure = views.structure
    assert structure.url_hierarchical is True
    assert (structure.url_pages, structure.url_deep, structure.url_with_parent) == (14, 6, 6)
    assert structure.unreachable == [f"{BASE}/hu/arva/"]
    assert [(row.lang, row.home, row.counts) for row in structure.depth] == [
        ("en", f"{BASE}/", {"0": 1, "1": 6, "2": 1, "3": 0, "4+": 0, "elérhetetlen": 0}),
        ("hu", f"{BASE}/hu/", {"0": 1, "1": 5, "2": 1, "3": 0, "4+": 0, "elérhetetlen": 1})]
    item = next(row for row in structure.menu if row.url == f"{BASE}/hu/szolg/seo/")
    assert (item.level, item.parent, item.click_depth, item.in_set, item.role) == (
        1, "Szolgáltatások", 1, True, pages["/hu/szolg/seo/"].role)
    paths = export_views(con, tmp_path, "pelda")
    with paths["pages"].open(encoding="utf-8-sig", newline="") as handle:
        rows = {row["url"].removeprefix(BASE): row for row in csv.DictReader(handle)}
    assert rows["/hu/arva/"]["elérhetetlen a kezdőoldalról"] == "igen"
    assert rows["/hu/arva/"]["mélység (minden link)"] == ""
    assert rows["/hu/szolg/seo/"]["szülő (URL)"] == f"{BASE}/hu/szolg/"
    assert rows["/hu/szolg/"]["a szülők egyeznek"] == "igen"
    assert rows["/hu/szolg/"]["szülő (URL)"] == "nem értelmezhető"
    with paths["menu"].open(encoding="utf-8-sig", newline="") as handle:
        menu = list(csv.DictReader(handle))
    assert [row["menüpont"] for row in menu if row["nyelv"] == "hu"
            and row["terület"] == "fejléc"] == ["Pelda", "Szolgáltatások", "SEO", "Mérés", "Blog"]
    assert paths["menu_differences"].exists()
    page = paths["html"].read_text(encoding="utf-8")
    assert "Menüfa <span>(15 menüpont)</span>" in page
    assert "A kezdőoldalról elérhetetlen oldalak <span>(1)</span>" in page


def test_a_flat_url_structure_has_no_url_parent(tmp_path):
    flat = {path: structure_page(path) for path in ("/", "/szolg/", "/blog/", "/adat/",
                                                    "/landing/")}
    con = site(flat, languages=("en",))
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    views = site_views(con, "pelda")
    assert views.structure.url_hierarchical is False
    assert all(page.url_parent is None for page in views.pages)
    paths = export_views(con, tmp_path, "pelda")
    with paths["pages"].open(encoding="utf-8-sig", newline="") as handle:
        rows = {row["url"].removeprefix(BASE): row for row in csv.DictReader(handle)}
    assert rows["/szolg/"]["szülő (URL)"] == "nem értelmezhető"
    # az URL nem számít forrásnak: a menü és a morzsa szülője egyezik
    assert rows["/szolg/"]["a szülők egyeznek"] == "igen"


def test_the_two_structure_findings():
    con = structure_site()
    found = {finding.type: finding for finding in stored_findings(con)
             if finding.type in ("breadcrumb_foreign_home", "menu_home_target")}
    crumb = found["breadcrumb_foreign_home"]
    assert crumb.severity == "low" and crumb.page_id is None
    assert crumb.evidence["home"] == f"{BASE}/hu/"
    assert crumb.evidence["foreign_home"] == f"{BASE}/"
    assert [page["url"].removeprefix(BASE) for page in crumb.evidence["pages"]] == [
        "/hu/adat/", "/hu/arva/", "/hu/blog/", "/hu/blog/cikk/", "/hu/szolg/",
        "/hu/szolg/meres/", "/hu/szolg/seo/"]
    assert crumb.evidence["pages"][0]["first_anchor"] == "Home"
    home = found["menu_home_target"]
    assert home.severity == "medium" and "két kezdőoldala" in home.summary
    assert home.evidence["menu_item"]["anchor"] == "Home"
    assert home.evidence["menu_item"]["url"] == f"{BASE}/landing/"
    assert home.evidence["logo"] == {"anchor": "Pelda", "url": f"{BASE}/", "points_home": True}
    assert home.evidence["two_homes"] is True and home.evidence["lang"] == "en"
    # a magyar menü „Főoldal” pontja a magyar kezdőoldalra mutat: nincs megállapítás
    assert sum(1 for finding in stored_findings(con)
               if finding.type == "menu_home_target") == 1


def built(pages, languages=("hu",), homes=None):
    con = site(pages, languages=languages)
    if homes:
        con.execute("UPDATE site SET home_urls = ?", [homes])
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    return con


def test_a_linkless_menu_label_agrees_with_the_same_named_breadcrumb_parent():
    assert name_key("Termékek!") == name_key("  termekek ") == "termekek"

    def page(path, name, crumbs):
        header = ('<header><a class="logo" href="/">Pelda</a><ul><li><a href="#">Termékek</a>'
                  '<ul><li><a href="/t/egy/">Egy</a></li><li><a href="/t/ketto/">Kettő</a></li>'
                  "</ul></li></ul></header>")
        trail = [{"@type": "ListItem", "position": n, "name": label, "item": f"{BASE}{url}"}
                 for n, (label, url) in enumerate(crumbs, start=1)]
        return html(f"{name} · Pelda", f"{header}<main><h1>{name}</h1><p>Szöveg.</p></main>",
                    head=ld({"@type": "BreadcrumbList", "itemListElement": trail}) if trail
                    else "")

    con = built({
        "/": page("/", "Pelda", []),
        "/termekek/": page("/termekek/", "Termékek", [("Főoldal", "/"), ("Termékek", "/termekek/")]),
        # a morzsa-szülő a címkével azonos nevű oldal, ugyanazon a szinten: egyezik
        "/t/egy/": page("/t/egy/", "Egy", [("Főoldal", "/"), ("TERMÉKEK", "/termekek/"),
                                           ("Egy", "/t/egy/")]),
        # más nevű morzsa-szülő: eltér
        "/t/ketto/": page("/t/ketto/", "Kettő", [("Főoldal", "/"), ("Akciók", "/termekek/"),
                                                 ("Kettő", "/t/ketto/")])})
    pages = {p.url.removeprefix(BASE): p for p in site_views(con, "pelda").pages}
    assert pages["/t/egy/"].menu_parent == "(címke: Termékek)"
    assert pages["/t/egy/"].parents_agree is True
    assert pages["/t/ketto/"].parents_agree is False


def structure_findings(con, kind):
    return [finding for finding in stored_findings(con) if finding.type == kind]


def test_orphan_pages_in_two_groups_with_severity_by_role():
    ring = {path: html(f"{name} · Pelda", f'<main><h1>{name}</h1><p>Szöveg. <a href="{other}">'
                       f"Tovább</a></p></main>", lang="hu")
            for path, name, other in (("/hu/kor-a/", "Kör A", "/hu/kor-b/"),
                                      ("/hu/kor-b/", "Kör B", "/hu/kor-a/"))}
    con = site({**{path: structure_page(path) for path in sorted({*PAIRS, *PAIRS.values()})},
                **ring}, languages=("en", "hu"))
    con.execute("UPDATE site SET home_urls = ?", [[f"{BASE}/", f"{BASE}/hu/"]])
    con.execute("INSERT INTO sitemap_files (snapshot, ordinal, url, source, found, urls, "
                "fetched_at) VALUES ('crawl', 0, ?, 'robots', true, 1, ?)",
                [f"{BASE}/sitemap.xml", NOON])
    con.execute("INSERT INTO sitemap_urls (snapshot, ordinal, raw_url, url, lastmod, "
                "sitemap_file, source, fetched_at) VALUES ('crawl', 0, ?, ?, '2026-09-01', ?, "
                "'robots', ?)", [f"{BASE}/hu/arva/", f"{BASE}/hu/arva/", f"{BASE}/sitemap.xml",
                                 NOON])
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    found = {finding.evidence["group"]: finding for finding in
             structure_findings(con, "orphan_pages")}
    assert set(found) == {"sitemap_only", "linked_only_by_orphans"}
    alone = found["sitemap_only"]
    assert alone.severity == "low" and "a sitemapben szerepelnek" in alone.summary
    assert [(page["url"].removeprefix(BASE), page["indexable"]) for page in
            alone.evidence["pages"]] == [("/hu/arva/", True)]
    assert set(alone.evidence["pages"][0]) == {"url", "role", "hub", "main_entity", "indexable",
                                               "noindex", "canonical", "word_count"}
    assert "crawl" not in alone.evidence                    # nincs részleges bejárás
    ring_found = found["linked_only_by_orphans"]
    assert [page["url"].removeprefix(BASE) for page in ring_found.evidence["pages"]] == [
        "/hu/kor-a/", "/hu/kor-b/"]
    assert ring_found.evidence["pages"][0]["linked_from"] == [f"{BASE}/hu/kor-b/"]
    # termék-szerepű árva oldallal közepes; sitemap-módú bejárásnál részlegesként jelölt
    con.execute("UPDATE page_nodes SET role = 'product' WHERE url = ?", [f"{BASE}/hu/arva/"])
    con.execute("INSERT INTO crawl_runs (started_at, pages_done, notes, mode) VALUES "
                "(?, 18, 'teszt', 'sitemap')", [NOON])
    build_findings(con)
    alone = next(finding for finding in structure_findings(con, "orphan_pages")
                 if finding.evidence["group"] == "sitemap_only")
    assert alone.severity == "medium"
    assert alone.evidence["crawl"] == "partial" and "részleges bejárás" in alone.summary


def test_menu_items_pointing_at_broken_pages():
    con = structure_site()
    assert structure_findings(con, "menu_broken_target") == []
    con.execute("UPDATE pages SET noindex = true WHERE url = ?", [f"{BASE}/adat/"])
    con.execute("UPDATE pages SET status = 404 WHERE url = ?", [f"{BASE}/blog/"])
    con.execute("UPDATE pages SET final_url = ? WHERE url = ?",
                [f"{BASE}/szolg/", f"{BASE}/landing/"])
    # a csak lekérdezésben eltérő végső cím nem átirányítás
    con.execute("UPDATE pages SET final_url = ? WHERE url = ?",
                [f"{BASE}/szolg/seo/?infinite_page=2", f"{BASE}/szolg/seo/"])
    build_findings(con)
    found = {finding.evidence["url"].removeprefix(BASE): finding
             for finding in structure_findings(con, "menu_broken_target")}
    assert {url: finding.evidence["problem"] for url, finding in found.items()} == {
        "/adat/": "noindex", "/blog/": "error_status", "/landing/": "redirect"}
    assert all(finding.severity == "medium" for finding in found.values())
    assert found["/adat/"].evidence["menu_items"] == [
        {"anchor": "Adatvédelem", "area": "lábléc", "lang": "en", "pages": 8, "area_pages": 8}]
    assert found["/blog/"].evidence["problem_label"] == "hibás státusz: 404"
    assert found["/landing/"].evidence["final_url"] == f"{BASE}/szolg/"
    assert "„Home” (fejléc)" in found["/landing/"].summary


def test_the_breadcrumb_that_ignores_the_menu_hierarchy():
    assert structure_findings(structure_site(), "breadcrumb_ignores_menu") == []
    con = built({path: structure_page(path, flat_crumbs=True)
                 for path in sorted({*PAIRS, *PAIRS.values()})}, languages=("en", "hu"),
                homes=[f"{BASE}/", f"{BASE}/hu/"])
    (finding,) = structure_findings(con, "breadcrumb_ignores_menu")
    assert finding.severity == "low" and finding.page_id is None
    assert (finding.evidence["submenu_pages"], finding.evidence["flat"]) == (4, 4)
    first = finding.evidence["pages"][0]
    assert first["url"] == f"{BASE}/hu/szolg/meres/" and first["menu_parent"] == "Szolgáltatások"
    assert [part["name"] for part in first["breadcrumb"]] == ["Home", "Mérés"]
