"""A menühierarchia (aaa2/engine/menu.py) és a hub-címke (findings._Site.hubs) szintetikus
site-on, hálózat és LLM nélkül."""
import csv

from aaa2.engine.menu import menu_pairs
from aaa2.entities.rules import run_rules
from aaa2.functions.findings import build_findings, export_views, site_views
from aaa2.functions.graph import build_graph
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON

BASE = "https://pelda.hu"
LIST_MENU = ('<nav><ul><li><a href="/szolgaltatasok/">Szolgáltatások</a><ul>'
             '<li><a href="/seo/">SEO</a><ul><li><a href="/seo/technikai/">Technikai</a></li>'
             '</ul></li><li><a href="/meres/">Mérés</a></li></ul></li>'
             '<li><a href="/blog/">Blog</a></li></ul></nav>')
DIV_MENU = ('<header><a href="/">Pelda</a><nav class="asztali"><div class="hub">'
            '<a href="/szolgaltatasok/">Szolgáltatások</a><div class="almenu">'
            '<a href="/seo/">SEO</a><a href="/meres/">Mérés</a></div></div>'
            '<a href="/blog/">Blog</a></nav>'
            '<nav class="mobil"><a href="/szolgaltatasok/">Szolgáltatások</a>'
            '<a href="/seo/">SEO</a><a href="/meres/">Mérés</a><a href="/blog/">Blog</a></nav>'
            '</header>')


def test_menu_pairs_from_nested_lists_and_from_plain_containers():
    assert menu_pairs(f"<html><body>{LIST_MENU}</body></html>", f"{BASE}/x/") == [
        (f"{BASE}/szolgaltatasok/", f"{BASE}/seo/", "SEO"),
        (f"{BASE}/szolgaltatasok/", f"{BASE}/meres/", "Mérés"),
        (f"{BASE}/seo/", f"{BASE}/seo/technikai/", "Technikai")]      # az unoka a gyerek alatt
    # listák nélküli lenyíló; a logó nem szülő, a lapos mobilmenü nem ad párt és nem dupláz
    assert menu_pairs(f"<html><body>{DIV_MENU}</body></html>", f"{BASE}/x/") == [
        (f"{BASE}/szolgaltatasok/", f"{BASE}/seo/", "SEO"),
        (f"{BASE}/szolgaltatasok/", f"{BASE}/meres/", "Mérés")]
    # egy szint két menüpontja nem szülő és gyerek
    flat = '<nav><ul><li><a href="/a/">A</a></li><li><a href="/b/">B</a></li></ul></nav>'
    assert menu_pairs(f"<html><body>{flat}</body></html>", f"{BASE}/x/") == []
    # a lábléc menüje nem számít
    footer = f"<html><body><footer>{LIST_MENU}</footer></body></html>"
    assert menu_pairs(footer, f"{BASE}/x/") == []


def page(title, body, head=""):
    return html(f"{title} · Pelda", f"{DIV_MENU}<main><h1>{title}</h1>{body}</main>", head=head)


def service(name, path):
    return ld({"@type": "Service", "name": name, "url": f"{BASE}{path}"})


def hub_site():
    return site({
        "/": page("Pelda", "<p>Üdv.</p>"),
        # menü-hub: két gyerek a menüben, a tartalma mindkettőre linkel
        "/szolgaltatasok/": page("Szolgáltatások", '<p>A <a href="/seo/">SEO</a> és a '
                                 '<a href="/meres/">mérés</a> együtt.</p>'),
        "/seo/": page("SEO tanácsadás", '<p>Vissza a <a href="/szolgaltatasok/">'
                      'szolgáltatásokhoz</a>.</p>', head=service("SEO tanácsadás", "/seo/")),
        "/meres/": page("Mérés", "<p>Szöveg.</p>", head=service("Mérés", "/meres/")),
        # tartalmi hub: nincs a menüben szülőként; három cikkre linkel, mind visszalinkel
        "/blog/": page("Blog", "".join(f'<p><a href="/blog/{n}/">Cikk {n}</a></p>'
                                       for n in ("egy", "ketto", "harom"))),
        **{f"/blog/{n}/": page(f"Cikk {n}", '<p>Szöveg. <a href="/blog/">Minden cikk</a></p>',
                               head=ld({"@type": "BlogPosting", "headline": f"Cikk {n}"}))
           for n in ("egy", "ketto", "harom")}})


def test_hub_labels_and_children_in_the_page_view(tmp_path):
    con = hub_site()
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    pages = {p.url.removeprefix(BASE): p for p in site_views(con, "pelda").pages}
    hub = pages["/szolgaltatasok/"]
    assert hub.hub == "menü-hub" and hub.role != "hub"            # címke, a szerep marad
    assert [(c.url.removeprefix(BASE), c.source, c.links_back) for c in hub.hub_children] == [
        ("/meres/", "mindkettő", False), ("/seo/", "mindkettő", True)]
    blog = pages["/blog/"]
    assert blog.hub == "tartalmi hub"
    assert [(c.url.removeprefix(BASE), c.source, c.links_back) for c in blog.hub_children] == [
        ("/blog/egy/", "tartalom", True), ("/blog/harom/", "tartalom", True),
        ("/blog/ketto/", "tartalom", True)]
    # a kezdőoldal és a gyerekek nem hubok
    assert pages["/"].hub is None and pages["/seo/"].hub is None
    assert pages["/blog/egy/"].hub is None and pages["/blog/egy/"].hub_children == []
    paths = export_views(con, tmp_path, "pelda")
    with paths["pages"].open(encoding="utf-8-sig", newline="") as handle:
        rows = {r["url"].removeprefix(BASE): r for r in csv.DictReader(handle)}
    assert rows["/szolgaltatasok/"]["hub"] == "menü-hub"
    assert rows["/szolgaltatasok/"]["hub gyerekei"] == (
        f"{BASE}/meres/ (mindkettő) | {BASE}/seo/ (mindkettő)")
    assert rows["/seo/"]["hub"] == "" and rows["/seo/"]["hub gyerekei"] == ""
    assert "Hubok <span>(2)</span>" in paths["html"].read_text(encoding="utf-8")
