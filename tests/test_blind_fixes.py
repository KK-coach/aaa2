"""A vak próba megítéléséből jött szabályok szintetikus bolton, hálózat és LLM nélkül: jogi
oldalak és segédlisták, a kategóriaoldal platformszabálya, a termékoldal title-egyezése, a
headingként jelölt bekezdés, a canonical-duplikátum oldalcsoportja és a hibás canonical."""
import json

import pytest

from aaa2.entities.rules import run_rules
from aaa2.functions.findings import build_findings, exclusion, product_title
from aaa2.functions.graph import build_graph
from aaa2.resolver import overrides
from aaa2.resolver.pages import (
    helper_list,
    helper_list_name,
    legal_kind,
    page_roles,
    page_types,
    support_url,
)
from aaa2.resolver.site import run_site
from tests.test_entities_rules import ld, site
from tests.test_entities_site import NOON

BASE = "https://pelda.hu"
FOOTER = ("<footer><a href='/altalanos-szerzodesi-feltetelek'>ÁSZF</a>"
          "<a href='/adatvedelmi_nyilatkozat_3'>Adatvédelem</a></footer>")
LONG = ("Miből készül a szappan? ÖSSZETEVŐK: extra szűz olívaolaj, kókuszolaj, víz, "
        "nátrium-hidroxid, levendula illóolaj és szárított levendulavirág")


def page(title, body, body_class="information-page-body", head=""):
    return (f"<html lang='hu'><head><title>{title}</title>{head}</head>"
            f"<body class='page-body {body_class}'>{body}{FOOTER}</body></html>")


def product(name, path, title=None, extra=""):
    node = {"@type": "Product", "name": name, "url": f"{BASE}{path}"}
    return page(title or name, f"<main><h1>{name}</h1><p>Leírás.</p>{extra}</main>",
                "product-page-body", ld(node))


def listing(h1, body_class, *paths):
    links = "".join(f"<p><a href='{path}'>termék</a></p>" for path in paths)
    return page(h1, f"<main><h1>{h1}</h1>{links}</main>", f"product-list-body {body_class}")


def shop():
    return site({
        "/": page("Pelda Bolt", "<main><h1>Pelda Bolt</h1></main>", "home-body",
                  ld({"@type": "Organization", "name": "Pelda Bolt", "url": f"{BASE}/"})),
        "/szappanok": listing("Szappanok", "category-list-body", "/levendula-szappan-100g",
                              "/furdogolyo-kecsketejes-90g"),
        "/akcios-termekek": listing("Akciós termékek", "category-list-body",
                                    "/levendula-szappan-100g"),
        "/index.php?route=product/list&special=1": listing("Akciók", "special-list-body"),
        "/hibabejelentes_8": page("Hibabejelentés", "<main><h1>Hibabejelentés</h1></main>"),
        "/megszunt-kategoria": page("A keresett oldal nem található", "<main><h1>A keresett "
                                    "oldal nem található</h1></main>", "not_found_body"),
        "/levendula-szappan-100g": product(
            "Levendula szappan 100g", "/levendula-szappan-100g",
            extra=f"<h4>{LONG}</h4><h4>Tárolás</h4><p>Száraz helyen.</p>"),
        "/furdogolyo-kecsketejes-90g": product(
            "Fürdőgolyó kecsketejes 90g", "/furdogolyo-kecsketejes-90g",
            title="Fürdőgolyó organikus kecsketejjel, shea vajjal és körömvirággal 90g"),
        "/sampon-korpas-hajra-50g": product(
            "Sampon korpás hajra 50g", "/sampon-korpas-hajra-50g",
            title="Herbál hajmosó rozmaringgal és teafa illóolajjal"),
        "/altalanos-szerzodesi-feltetelek": page(
            "ÁSZF", "<main><h1>ÁSZF</h1><p>Elállási jog</p><p>Tizennégy nap.</p>"
            "<h3>Üres rész</h3><h3>Záró rendelkezések</h3><p>Vége.</p></main>"),
        "/adatvedelmi_nyilatkozat_3": page(
            "Adatkezelés", "<main><h1>Adatkezelési tájékoztató</h1><p>Szöveg.</p></main>"),
        "/garancia_7": page("Garancia", "<main><h1>Garancia</h1><p>Két év.</p></main>"),
        "/index.php?route=information/contact": page(
            "Kapcsolat", "<main><h1>Kapcsolat</h1><p>Írj nekünk.</p></main>",
            "contact-page-body"),
    })


def analysed(con):
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    build_graph(con)
    build_findings(con)
    nodes = {url.removeprefix(BASE): rest for url, *rest in con.execute(
        "SELECT p.url, p.role, p.support_kind, p.main_status, (SELECT e.name FROM "
        "page_main_entity m JOIN entities e USING (entity_id) WHERE m.page_id = p.page_id AND "
        "m.role = 'main') FROM page_nodes p").fetchall()}
    found = [(kind, severity, summary, json.loads(evidence)) for kind, severity, summary, evidence
             in con.execute("SELECT type, severity, summary, evidence FROM findings "
                            "ORDER BY finding_id").fetchall()]
    return nodes, found


def test_legal_slugs_with_numbers_and_hungarian_names_are_recognised():
    kinds = {path: legal_kind(BASE + path) for path in (
        "/altalanos-szerzodesi-feltetelek", "/vasarlasi_feltetelek_5",
        "/adatvedelmi_nyilatkozat_3", "/garancia_7", "/withdrawal", "/szallitas-es-fizetes",
        "/index.php?route=information/contact", "/garancialis-javitas-blog",
        "/blog/payment-gateway-tips/", "/eprivacy-and-gdpr-diagnostics/")}
    assert kinds == {
        "/altalanos-szerzodesi-feltetelek": "terms", "/vasarlasi_feltetelek_5": "terms",
        "/adatvedelmi_nyilatkozat_3": "privacy", "/garancia_7": "warranty",
        "/withdrawal": "withdrawal", "/szallitas-es-fizetes": "shipping_payment",
        "/index.php?route=information/contact": "contact", "/garancialis-javitas-blog": None,
        "/blog/payment-gateway-tips/": None, "/eprivacy-and-gdpr-diagnostics/": None}
    assert support_url(BASE + "/garancia_7") and support_url(BASE + "/privacy-policy/")
    # a kapcsolat-oldalt a gráf külön kezeli, a szerep-döntésben nem jogi oldal
    assert not support_url(BASE + "/index.php?route=information/contact")
    assert helper_list(BASE + "/index.php?route=product/list&special=1")
    assert helper_list(BASE + "/hibabejelentes_8") and not helper_list(BASE + "/szappanok")
    assert helper_list_name(BASE + "/termekeink-111/kifuto-termekek")
    assert helper_list_name(BASE + "/akcios-termekek") and not helper_list_name(BASE + "/szappanok")


def test_the_platform_marks_category_pages_and_helper_lists():
    con = shop()
    kinds = {con.execute("SELECT url FROM pages WHERE page_id = ?", [page_id]).fetchone()[0]
             .removeprefix(BASE): kind for page_id, kind in page_types(con).items()}
    assert kinds["/szappanok"] == "category"
    assert kinds["/akcios-termekek"] == "list"                 # kategóriaoldal, segédlista-név
    assert kinds["/index.php?route=product/list&special=1"] == "list"
    assert kinds["/hibabejelentes_8"] == "list"
    assert kinds["/levendula-szappan-100g"] == "product" and kinds["/garancia_7"] == "other"


def test_category_legal_and_helper_pages_get_the_right_main_entity():
    nodes, found = analysed(shop())
    # a kategóriaoldal fő entitása a kategória, oldalhoz kötött entitásként
    assert nodes["/szappanok"] == ["category", None, "main", "Szappanok"]
    # a segédlistának nincs fő entitása, és ez nem megállapítás
    for path in ("/akcios-termekek", "/index.php?route=product/list&special=1",
                 "/hibabejelentes_8"):
        assert nodes[path] == ["listing", "list", "support", None]
    # a 200-as „nem található” oldal sem kap fő entitást és megállapítást
    assert nodes["/megszunt-kategoria"][1:] == ["placeholder", "support", None]
    # a jogi oldalnak nincs fő entitása
    for path in ("/altalanos-szerzodesi-feltetelek", "/adatvedelmi_nyilatkozat_3",
                 "/garancia_7"):
        assert nodes[path] == ["support", "legal", "support", None]
    assert nodes["/index.php?route=information/contact"][:3] == ["support", "contact", "support"]
    assert not [f for f in found if f[0] == "unclear_topic"]
    # jogi oldalon nincs tartalmi szerkezeti megállapítás (az ÁSZF üres szakasza, H1→H3 ugrása)
    legal = [f for f in found if f[0] in ("empty_section", "skipped_level", "missing_h2",
                                          "h1_title_mismatch")
             and "feltetelek" in json.dumps(f[3])]
    assert legal == []


def test_the_shop_gets_a_finding_for_each_missing_or_unreachable_legal_page():
    _, found = analysed(shop())
    legal = {f[3]["kind"]: (f[1], f[3]["status"]) for f in found if f[0] == "legal_page"}
    # ÁSZF és adatvédelem: megvan, a láblécből elérhető; elállás: az ÁSZF egy címként álló
    # sora nevezi meg, így megvan (az ÁSZF lábléc-linkjével); szállítás és fizetés: hiányzik; garancia és
    # kapcsolat: megvan, de nincs rá lábléc-link
    assert legal == {"shipping_payment": ("medium", "missing"),
                     "warranty": ("low", "not_in_footer"),
                     "contact": ("low", "not_in_footer")}
    inside = next(f[3] for f in found if f[0] == "legal_page" and f[3]["kind"] == "warranty")
    assert inside["pages"] == [{"url": f"{BASE}/garancia_7"}]
    # nem bolton (nincs termékoldal) az ellenőrzés nem fut
    plain = site({"/": page("Pelda", "<main><h1>Pelda</h1></main>")})
    assert not [f for f in analysed(plain)[1] if f[0] == "legal_page"]


def test_a_product_title_with_the_headword_and_the_size_is_not_a_mismatch():
    assert product_title("Fürdőgolyó kecsketejes 90g",
                         "Fürdőgolyó organikus kecsketejjel, shea vajjal 90g")
    assert product_title("Hajbalzsam 130 g", "Hajbalzsam levendulával 130 g")
    assert not product_title("Sampon korpás hajra 50g", "Sampon rozmaringgal")     # nincs 50g
    assert not product_title("Fürdőgolyó kecsketejes 90g", "Kecsketejes golyó 90g")  # más fejszó
    assert product_title("Levendula szappan", "Levendula szappan natúr") \
        and not product_title("Levendula szappan", "Levendula olaj")
    _, found = analysed(shop())
    mismatches = [f[3]["url"].removeprefix(BASE) for f in found if f[0] == "h1_title_mismatch"]
    assert mismatches == ["/sampon-korpas-hajra-50g"]


def test_a_long_empty_heading_is_a_paragraph_marked_as_a_heading():
    _, found = analysed(shop())
    paragraph = [f for f in found if f[0] == "paragraph_heading"]
    assert [(f[1], f[3]["url"].removeprefix(BASE)) for f in paragraph] == [
        ("low", "/levendula-szappan-100g")]
    assert paragraph[0][3]["headings"] == [f"H4: {LONG}"]
    assert paragraph[0][2].startswith("bekezdés headingként jelölve: H4: Miből készül")
    # ugyanaz a heading nem üres szakasz is egyben
    assert not [f for f in found if f[0] == "empty_section"
                and "levendula-szappan" in json.dumps(f[3])]


def test_the_common_word_exclusion_ignores_the_spelling_of_the_name():
    base = {"template_share": 0.0, "in_site_name": False, "headline": None, "parent": False}
    assert exclusion({**base, "common_word": True}) == "common_word"
    from aaa2.db.connect import connect
    from aaa2.entities import store

    con = connect(":memory:")
    con.execute("INSERT INTO pages (url, status) VALUES ('https://pelda.hu/', 200)")
    for name, forms in (("Stratégia", ["stratégia", "Stratégia"]), ("Google", ["Google"]),
                        ("SEO", ["SEO"])):
        (entity_id,) = con.execute("INSERT INTO entities (name, type, source) VALUES "
                                   "(?, 'concept', 'llm') RETURNING entity_id", [name]).fetchone()
        for start, form in enumerate(forms):
            con.execute("INSERT INTO page_entities (page_id, entity_id, surface_form, "
                        "char_start, char_end, position) VALUES (1, ?, ?, ?, ?, 'schema')",
                        [entity_id, form, start * 20, start * 20 + len(form)])
    lower = {con.execute("SELECT name FROM entities WHERE entity_id = ?", [e]).fetchone()[0]
             for (e,) in store.lowercase_word_entities(con)}
    assert lower == {"Stratégia"}


def articles(canonical):
    post = ld({"@type": "BlogPosting", "headline": "x"})
    service = ld({"@type": "Service", "name": "Mérés", "url": f"{BASE}/meres/"})
    con = site({
        "/": page("Pelda", "<main><h1>Pelda</h1></main>"),
        "/blog/meresi-terv/": page("Mérési terv", "<main><h1>Mérési terv</h1><p>A.</p></main>",
                                   head=post),
        "/archiv/meresi-terv/": page("Mérési terv", "<main><h1>Mérési terv</h1><p>A.</p>"
                                        "</main>", head=post),
        "/meres/": page("Mérés", "<main><h1>Mérés</h1><p>B.</p></main>", head=service)})
    for path, target in canonical.items():
        con.execute("UPDATE pages SET canonical = ? WHERE url = ?", [target, BASE + path])
    return con


def test_a_canonical_duplicate_joins_the_group_of_its_target_and_gets_no_entity_of_its_own():
    con = articles({"/archiv/meresi-terv/": f"{BASE}/blog/meresi-terv/"})
    roles = {info.url.removeprefix(BASE): info for info in page_roles(con).values()}
    assert roles["/archiv/meresi-terv/"].group == roles["/blog/meresi-terv/"].group
    _, found = analysed(con)
    assert con.execute("SELECT count(*) FROM entities WHERE name = 'Mérési terv' AND "
                       "anchor_page_id IS NOT NULL").fetchone() == (1,)
    assert con.execute("SELECT count(*) FROM page_nodes WHERE canonical_page IS NOT NULL"
                       ).fetchone() == (1,)
    assert not [f for f in found if f[0] == "canonical_issue"]


def test_a_canonical_to_another_kind_of_page_stays_separate_and_is_a_finding():
    con = articles({"/archiv/meresi-terv/": f"{BASE}/meres/"})
    roles = {info.url.removeprefix(BASE): info for info in page_roles(con).values()}
    assert roles["/archiv/meresi-terv/"].group != roles["/meres/"].group
    _, found = analysed(con)
    issues = [(f[1], f[3]["url"].removeprefix(BASE), f[3]["issue"]) for f in found
              if f[0] == "canonical_issue"]
    assert issues == [("medium", "/archiv/meresi-terv/", "other_type")]
    assert con.execute("SELECT canonical_page, canonical_issue FROM page_nodes WHERE url LIKE "
                       "'%/archiv/%'").fetchone() == (None, "other_type")    # nem duplikátum


@pytest.mark.parametrize(("crawl", "url", "target", "expected"), [
    ("", "/blog/meresi-terv/", "/nincs/", ["not_crawled"]),              # teljes crawl
    ("exclude = ['/nincs/']", "/blog/meresi-terv/", "/nincs/", []),      # a keret kizárta
    ("sitemap_only = true", "/blog/meresi-terv/", "/nincs/", []),        # korlátozott crawl
    ("", "/blog/meresi-terv/", "https://masik.hu/x/", []),               # más host
])
def test_a_canonical_outside_the_set_is_a_finding_unless_the_crawl_frame_excludes_it(
        tmp_path, monkeypatch, crawl, url, target, expected):
    (tmp_path / "pelda.hu.toml").write_text(f"[crawl]\n{crawl}\n", encoding="utf-8")
    monkeypatch.setattr(overrides, "SITES_DIR", tmp_path)
    _, found = analysed(articles({url: target}))
    assert [f[3]["issue"] for f in found if f[0] == "canonical_issue"] == expected


def test_a_canonical_that_drops_the_query_is_a_finding_even_on_a_limited_crawl(
        tmp_path, monkeypatch):
    (tmp_path / "pelda.hu.toml").write_text("[crawl]\nsitemap_only = true\n", encoding="utf-8")
    monkeypatch.setattr(overrides, "SITES_DIR", tmp_path)
    con = site({"/": page("Pelda", "<main><h1>Pelda</h1></main>"),
                "/index.php?route=product/list&latest=1": listing("Újdonságok",
                                                                  "latest-list-body")})
    con.execute("UPDATE pages SET canonical = ? WHERE url LIKE '%latest=1%'",
                [f"{BASE}/index.php"])
    _, found = analysed(con)
    assert [(f[1], f[3]["canonical"]) for f in found if f[0] == "canonical_issue"] == [
        ("medium", f"{BASE}/index.php")]
