"""A vak próba megítéléséből jött szabályok szintetikus bolton, hálózat és LLM nélkül: jogi
oldalak és segédlisták, a kategóriaoldal platformszabálya, a termékoldal title-egyezése, a
headingként jelölt bekezdés, a canonical-duplikátum oldalcsoportja és a hibás canonical."""
import json

import pytest

from aaa2.entities.rules import run_rules
from aaa2.functions.findings import (
    build_findings,
    exclusion,
    loosely_named,
    product_title,
    shares_word,
)
from aaa2.functions.graph import _Graph, build_graph
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


NAV = "<nav><a href='/szappanok'>Szappan</a></nav>"


def page(title, body, body_class="information-page-body", head=""):
    return (f"<html lang='hu'><head><title>{title}</title>{head}</head>"
            f"<body class='page-body {body_class}'>{NAV}{body}{FOOTER}</body></html>")


def crumbs(*names):
    return ld({"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": name} for i, name in enumerate(names)]})


def product(name, path, title=None, extra=""):
    node = {"@type": "Product", "name": name, "url": f"{BASE}{path}"}
    return page(title or name, f"<main><h1>{name}</h1><p>Leírás.</p>{extra}</main>",
                "product-page-body", ld(node))


def listing(h1, body_class, *paths, title=None, head=""):
    links = "".join(f"<p><a href='{path}'>termék</a></p>" for path in paths)
    return page(title or h1, f"<main><h1>{h1}</h1>{links}</main>",
                f"product-list-body {body_class}", head)


def shop():
    return site({
        "/": page("Pelda Bolt", "<main><h1>Pelda Bolt</h1></main>", "home-body",
                  ld({"@type": "Organization", "name": "Pelda Bolt", "url": f"{BASE}/"})),
        "/szappanok": listing("Szappanok", "category-list-body", "/levendula-szappan-100g",
                              "/furdogolyo-kecsketejes-90g"),
        "/furdotejek": listing("Fürdőtejek a legjobb áron", "category-list-body",
                               "/furdogolyo-kecsketejes-90g", title="fürdőtej",
                               head=crumbs("Kezdőlap", "Fürdőtejek")),
        "/bortipus": listing("Segítünk megtalálni a megfelelő terméket", "category-list-body",
                             "/levendula-szappan-100g", title="Keress közöttük",
                             head=crumbs("Kezdőlap", "Bőrtípus szerint")),
        "/katalogusok": page("Letöltések", "<main><h1>Letöltések</h1><p>Itt találod.</p><table>"
                             "<tr><th>Név</th><th>Fájl</th></tr><tr><td>Szappan 2025</td><td>"
                             "Letöltés</td></tr><tr><td>Olaj 2025</td><td>Letöltés</td></tr>"
                             "<tr><td>Krém 2025</td><td>Letöltés</td></tr></table></main>"),
        "/akcios-termekek": listing("Akciós termékek", "category-list-body",
                                    "/levendula-szappan-100g"),
        "/index.php?route=product/list&special=1": listing("Akciók", "special-list-body"),
        "/hibabejelentes_8": page("Hibabejelentés", "<main><h1>Hibabejelentés</h1></main>"),
        "/megszunt-kategoria": page("Hoppá", "<main><h1>Hoppá</h1></main>", "not_found_body"),
        "/regi-oldal": page("A keresett oldal nem található", "<main><h1>Keresés</h1></main>"),
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
    # szókezdet a slug bármely szavában
    assert legal_kind(BASE + "/kedvezo-csomagkuldes") == "shipping_payment"
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
    # a kategóriaoldal fő entitása a kategória, oldalhoz kötött entitásként; a neve a
    # morzsamenü utolsó eleme, annak híján a menü-anchor, és csak végül a H1
    assert nodes["/furdotejek"] == ["category", None, "main", "Fürdőtejek"]
    assert nodes["/bortipus"] == ["category", None, "main", "Bőrtípus szerint"]
    assert nodes["/szappanok"] == ["category", None, "main", "Szappan"]
    # a jórészt táblázatból álló segédoldal lista
    assert nodes["/katalogusok"] == ["listing", "list", "support", None]
    # a segédlistának nincs fő entitása, és ez nem megállapítás
    for path in ("/akcios-termekek", "/index.php?route=product/list&special=1",
                 "/hibabejelentes_8"):
        assert nodes[path] == ["listing", "list", "support", None]
    # a 200-as „nem található” oldal nem kap fő entitást; egy közepes megállapítás sorolja fel
    assert nodes["/megszunt-kategoria"][1:] == ["placeholder", "support", None]
    soft = [f for f in found if f[0] == "soft_404"]
    # a platform jele és a title / H1 szövege egyaránt felismeri
    assert [(f[1], [p["url"].removeprefix(BASE) for p in f[3]["pages"]]) for f in soft] == [
        ("medium", ["/megszunt-kategoria", "/regi-oldal"])]
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
    # ha az ÁSZF szövege külső keretben áll, a benne várható fajták hiánya nem állapítható meg
    con = shop()
    con.execute("UPDATE pages SET rendered_html = ? WHERE url LIKE '%szerzodesi%'", [
        __import__("zstandard").ZstdCompressor().compress(page(
            "ÁSZF", "<main><h1>ÁSZF</h1><iframe src='//jogi.example/aszf'></iframe></main>"
        ).encode())])
    kinds = {f[3]["kind"] for f in analysed(con)[1] if f[0] == "legal_page"}
    assert "shipping_payment" not in kinds and "withdrawal" not in kinds
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
    mismatches = sorted(item["url"].removeprefix(BASE) for f in found
                        if f[0] == "h1_title_mismatch" for item in f[3].get("pages") or [f[3]])
    # a kategóriaoldalon a többes szám nem eltérés („Fürdőtejek” ↔ „fürdőtej”); ahol sem a H1,
    # sem a title nem nevezi meg a kategóriát, az megállapítás
    assert mismatches == ["/bortipus", "/sampon-korpas-hajra-50g"]
    assert loosely_named(["Fürdőtejek"], "fürdőtej") and loosely_named(
        ["Szilárd Sampon"], "Hajsampon, szilárd samponok")
    assert not loosely_named(["Bőrtípus szerint"], "Keress bőrtípusod szerint")
    # a kategóriaoldal title-jének elég a név egy tartalmas szava, szótő szerint
    assert shares_word(["Kéz- és lábápolók"], "Kézkrémek, lábkrémek")
    assert shares_word(["Citrusos illatú illóolajok"], "Citrusos illat - 100%-os illóolaj")
    assert not shares_word(["Zero Waste eszközök"], "Kiegészítő termékek")
    # a kategóriaoldal eltérése közepes súlyosságú (a H1 szlogen, a title nem nevezi meg)
    assert [f[1] for f in found if f[0] == "h1_title_mismatch"
            and "/bortipus" in json.dumps(f[3])] == ["medium"]


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


def test_a_link_to_a_duplicate_counts_as_a_link_to_the_original():
    con = articles({"/archiv/meresi-terv/": f"{BASE}/blog/meresi-terv/"})
    ids = {url.removeprefix(BASE): page_id for page_id, url in con.execute(
        "SELECT page_id, url FROM pages").fetchall()}
    con.execute("INSERT INTO links (from_page_id, to_url, to_page_id, anchor, position, ordinal) "
                "VALUES (?, ?, ?, 'a mérési tervről', 'body', 99)",
                [ids["/meres/"], BASE + "/archiv/meresi-terv/", ids["/archiv/meresi-terv/"]])
    run_rules(con)
    run_site(con, clock=lambda: NOON)
    graph = _Graph(con, {})
    assert (ids["/meres/"], "a mérési tervről", True) in graph.inbound[ids["/blog/meresi-terv/"]]
    assert not graph.inbound[ids["/archiv/meresi-terv/"]]


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


def crawl_frame(con, mode="links", exclude="", skipped=0):
    """A crawl kerete, ahogy a futás rögzíti (a megállapítások ebből olvassák, nem a site-fájlból)."""
    con.execute("INSERT INTO crawl_runs (started_at, finished_at, max_pages, mode, include, "
                "exclude, skipped_by_limit, stopped) VALUES (current_timestamp, "
                "current_timestamp, 5000, ?, '', ?, ?, ?)",
                [mode, exclude, skipped, "oldalkorlát" if skipped else None])
    return con


@pytest.mark.parametrize(("frame", "url", "target", "expected"), [
    ({}, "/blog/meresi-terv/", "/nincs/", ["not_crawled"]),                    # teljes crawl
    ({"exclude": "/nincs/"}, "/blog/meresi-terv/", "/nincs/", []),             # a keret kizárta
    ({"mode": "sitemap"}, "/blog/meresi-terv/", "/nincs/", []),                # sitemap-mód
    ({"skipped": 3}, "/blog/meresi-terv/", "/nincs/", []),                     # a korláton megállt
    ({}, "/blog/meresi-terv/", "https://masik.hu/x/", []),                     # más host
])
def test_a_canonical_outside_the_set_is_a_finding_unless_the_crawl_frame_excludes_it(
        frame, url, target, expected):
    _, found = analysed(crawl_frame(articles({url: target}), **frame))
    assert [f[3]["issue"] for f in found if f[0] == "canonical_issue"] == expected


def test_a_canonical_that_drops_the_query_is_a_finding_even_on_a_limited_crawl(
        tmp_path, monkeypatch):
    con = crawl_frame(site({"/": page("Pelda", "<main><h1>Pelda</h1></main>"),
                            "/index.php?route=product/list&latest=1": listing(
                                "Újdonságok", "latest-list-body")}), mode="sitemap")
    con.execute("UPDATE pages SET canonical = ? WHERE url LIKE '%latest=1%'",
                [f"{BASE}/index.php"])
    _, found = analysed(con)
    assert [(f[1], f[3]["canonical"]) for f in found if f[0] == "canonical_issue"] == [
        ("medium", f"{BASE}/index.php")]
