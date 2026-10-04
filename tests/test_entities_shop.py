"""A webshop-szintek (aaa2/entities/shop.py; M2/6 spec, 9a pont), a site-fájl crawl- és
oldaltípus-része (overrides.py) és az oldaltípus (pages.page_types), hálózat nélkül."""
import json

import pytest

from aaa2.entities.rules import run_rules
from aaa2.resolver import overrides
from aaa2.resolver.names import cut_off, site_name_form
from aaa2.resolver.overrides import load_site_config
from aaa2.resolver.pages import breadcrumbs, page_types
from aaa2.resolver.shop import (
    base_tokens,
    families,
    longest_prefix,
    name_attributes,
    nest_families,
    orphan_target,
    trim_family,
)
from aaa2.resolver.site import run_site
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON, llm_entity

SHOP = "https://pelda.hu"


def trail(*items):
    return ld({"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": name, "item": f"{SHOP}{path}"}
        for i, (name, path) in enumerate(items)]})


def product_page(name, path, brand_path=None, brand=None, category=("Klíma", "/sct/2/Klima")):
    items = [("Főoldal", "/"), ("Termékek", "/sct/1/Termekek"), category]
    if brand_path:
        items.append((brand, brand_path))
    node = {"@type": "Product", "name": name, "url": f"{SHOP}{path}"}
    return html(f"{name} - Pel", f"<main><h1>{name}</h1><p>Leírás.</p></main>",
                head=ld(node) + trail(*items))


def listing(h1, *items):
    return html(f"{h1} - Pelda Webshop", f"<main><h1>{h1}</h1><p>Lista.</p></main>",
                head=trail(*items) + ld({"@type": "ItemList", "itemListElement": []}))


@pytest.fixture
def shop_config(tmp_path, monkeypatch):
    (tmp_path / "pelda.hu.toml").write_text(
        "[page_types]\ncategory = ['/sct/']\nbrand_category = ['/spl/']\n", encoding="utf-8")
    monkeypatch.setattr(overrides, "SITES_DIR", tmp_path)
    return tmp_path


def shop_site():
    brand = ("ACME", "/spl/3/ACME")
    pages = {
        "/": html("Pelda Webshop", "<main><h1>Pelda Webshop</h1></main>",
                  head=ld({"@type": "Organization", "name": "Pelda Webshop",
                           "url": f"{SHOP}/"})),
        "/sct/1/Termekek": listing("Termékek", ("Főoldal", "/"), ("Termékek", "/sct/1/Termekek")),
        "/sct/2/Klima": listing("Klíma", ("Főoldal", "/"), ("Termékek", "/sct/1/Termekek"),
                                ("Klíma", "/sct/2/Klima")),
        "/spl/3/ACME": listing("ACME", ("Főoldal", "/"), ("Termékek", "/sct/1/Termekek"),
                               ("Klíma", "/sct/2/Klima"), brand),
    }
    for path, name in [("/spd/a", "Acme Nordic 2,6 kW oldalfali klíma"),
                       ("/spd/b", "Acme Nordic 3,5 kW oldalfali klíma"),
                       ("/spd/c", "Acme Solo 5,1 kW A+++")]:
        pages[path] = product_page(name, path, brand[1], brand[0])
    # márkaoldal nélküli morzsamenü: a márka a név elejéről
    pages["/spd/d"] = product_page("Acme Nordic 5,1 kW oldalfali klíma", "/spd/d")
    pages["/spd/e"] = product_page("Egyedi Termék 1 Fázis", "/spd/e")
    return site(pages)


def test_name_parsing_finds_attributes_and_the_family_base():
    assert name_attributes("AUX HELIA 2,7 kW A+++") == {"capacity": "2,7 kW",
                                                       "energy_class": "A+++"}
    assert name_attributes("LG ThermaV R32 5,5KW 1Fázis") == {"capacity": "5,5 KW", "phase": 1}
    assert name_attributes("Fisher lakás szellőztető 400m3/h") == {"capacity": "400 m3/h"}
    assert base("Fujitsu Waterstage High Power V2 1 Fázis 11KW") ==         "Fujitsu Waterstage High Power V2"
    assert base("AUX DELTA 3 2,7 kW") == "AUX DELTA 3"
    assert base("Cascade FREE MATCH BORA CWH09AAA-K6DNA5A klíma") == "Cascade FREE MATCH BORA"
    assert base("Cascade EcoStar Plus R290 CLN-006TC1 egység") == "Cascade EcoStar Plus R290"


def base(name):
    return " ".join(base_tokens(name.split()))


def test_families_per_brand_with_the_digit_divergence_fallback():
    names = {1: "Cascade BORA 2,6 kW", 2: "Cascade BORA 3,5 kW", 3: "FREE MATCH BORA CWH09AAA",
             4: "Cascade FREE MATCH BORA CWH12AABXB", 5: "Cascade LEGEND 2,5 kW",
             6: "BlueSoft Eco 12 HF Plug&Play", 7: "BlueSoft Eco 18 Plug&Play"}
    found = families(names, "Cascade")
    assert found[1] == found[2] == ("bora",)
    assert found[3] == found[4] == ("free", "match", "bora")
    assert found[6] == found[7] == ("bluesoft", "eco")
    assert 5 not in found                                   # egyetlen tag
    # a márka után rögtön típuskód: nincs család
    assert families({8: "Fisher A200 légtisztító", 9: "Fisher A350 légtisztító"}, "Fisher") == {}


def test_truncated_titles_are_not_names():
    keys = {"pelda webshop"}
    assert site_name_form("P", keys, minimum=1) and not site_name_form("P", keys)
    assert site_name_form("Pelda Webs", keys)
    assert cut_off("Fujitsu Waterstage High Power V2 1 Fázis Hmv Tartá",
                   "Fujitsu Waterstage High Power V2 1 Fázis Hmv Tartályal 14KW")
    assert not cut_off("SEO", "SEO tanácsadás") and not cut_off("Nordic", "Nordic")


def test_site_file_crawl_and_page_type_sections(tmp_path):
    (tmp_path / "x.hu.toml").write_text(
        "[crawl]\nseed = 'https://shop.x.hu/'\ninclude = '^https://shop\\\\.x\\\\.hu/'\n"
        "exclude = ['shop_cart', '^https?://[^/]+[^?]*//']\nconcurrency = 2\n"
        "render_timeout = 30\nmax_pages = 300\nsitemap_only = true\n[page_types]\ncategory = '/sct/'\nbrand_category = ['/spl/']\n",
        encoding="utf-8")
    config = load_site_config("x.hu", tmp_path)
    assert config.crawl.seed == "https://shop.x.hu/"
    assert config.crawl.exclude_pattern == "(?:shop_cart)|(?:^https?://[^/]+[^?]*//)"
    assert (config.crawl.concurrency, config.crawl.render_timeout) == (2, 30)
    assert (config.crawl.max_pages, config.crawl.sitemap_only) == (300, True)
    assert load_site_config("nincs.hu", tmp_path).crawl.sitemap_only is False
    assert config.page_types == {"category": ("/sct/",), "brand_category": ("/spl/",)}
    (tmp_path / "y.hu.toml").write_text("[page_types]\nshelf = ['/x/']\n", encoding="utf-8")
    with pytest.raises(ValueError, match="oldaltípus"):
        load_site_config("y.hu", tmp_path)
    (tmp_path / "z.hu.toml").write_text("[crawl]\nexclude = ['(']\n", encoding="utf-8")
    with pytest.raises(ValueError, match="regex"):
        load_site_config("z.hu", tmp_path)


def test_page_types_and_breadcrumbs(shop_config):
    con = shop_site()
    urls = dict(con.execute("SELECT page_id, url FROM pages").fetchall())
    kinds = {urls[p]: k for p, k in page_types(con, load_site_config("pelda.hu").page_types)
             .items()}
    assert kinds[f"{SHOP}/sct/2/Klima"] == "category"
    assert kinds[f"{SHOP}/spl/3/ACME"] == "brand_category"
    assert kinds[f"{SHOP}/spd/a"] == "product" and kinds[f"{SHOP}/"] == "other"
    trails = {urls[p]: t for p, t in breadcrumbs(con).items()}
    assert [n for n, _ in trails[f"{SHOP}/spd/a"]] == ["Főoldal", "Termékek", "Klíma", "ACME"]


def test_shop_levels_brand_family_variant_and_category(shop_config):
    con = shop_site()
    run_rules(con)
    run = run_site(con)
    assert run.shop["products"] == 5 and run.shop["unbranded"] == ["Egyedi Termék 1 Fázis"]
    rows = {(name, kind, subtype) for name, kind, subtype in con.execute(
        "SELECT name, type, subtype FROM entities").fetchall()}
    assert ("Acme", "brand", None) in rows                  # a név eleji írásmód, nem ACME
    assert ("Acme Nordic", "product", "line") in rows
    assert ("Klíma", "concept", "category") in rows
    assert not any(name == "Termékek" for name, _, _ in rows)   # gyökérkategória
    ids = dict(con.execute("SELECT name, entity_id FROM entities").fetchall())
    relations = set(con.execute("SELECT from_id, to_id, type FROM entity_relations").fetchall())
    family, brand, category = ids["Acme Nordic"], ids["Acme"], ids["Klíma"]
    for name in ("Acme Nordic 2,6 kW oldalfali klíma", "Acme Nordic 3,5 kW oldalfali klíma",
                 "Acme Nordic 5,1 kW oldalfali klíma"):
        assert (ids[name], family, "part_of") in relations
        assert (ids[name], category, "in_category") in relations
    assert (brand, family, "brand_of") in relations
    assert (brand, ids["Acme Solo 5,1 kW A+++"], "brand_of") in relations   # család nélkül
    attributes = json.loads(con.execute("SELECT attributes FROM entities WHERE entity_id = ?",
                                        [ids["Acme Solo 5,1 kW A+++"]]).fetchone()[0])
    assert attributes == {"capacity": "5,1 kW", "energy_class": "A+++"}
    assert con.execute("SELECT count(*) FROM merge_log WHERE rule = 'page_identity'"
                       ).fetchone() == (0,)                 # a csonkolt title nem von össze
    assert con.execute("SELECT count(*) FROM page_entities WHERE entity_id = ? "
                       "AND position = 'h1'", [family]).fetchone() == (3,)


# ---------------------------------------------------------------------------
# ugyanaz a bolt microdatával (JSON-LD nélkül)
# ---------------------------------------------------------------------------


def md_trail(*items):
    """Morzsamenü microdatával, relatív linkekkel (ahogy a webshop-motorok adják)."""
    rows = "".join(
        f'<li itemprop="itemListElement" itemscope itemtype="http://schema.org/ListItem">'
        f'<a itemprop="item" href="{path}"><span itemprop="name">{name}</span></a>'
        f'<meta itemprop="position" content="{i + 1}"></li>'
        for i, (name, path) in enumerate(items))
    return f'<ol itemscope itemtype="http://schema.org/BreadcrumbList">{rows}</ol>'


def md_product_page(name, brand_path=None, brand=None, brand_item=None,
                    category=("Klíma", "/sct/2/Klima")):
    """Termékoldal microdata `Product` elemmel: `url` nélkül, beágyazott `Offer`-rel és
    `Review`-val; a márka a morzsamenüből vagy a beágyazott `Brand` elemből."""
    items = [("Főoldal", "/"), ("Termékek", "/sct/1/Termekek"), category]
    if brand_path:
        items.append((brand, brand_path))
    brand_html = (f'<span itemprop="brand" itemscope itemtype="http://schema.org/Brand">'
                  f'<meta itemprop="name" content="{brand_item}"></span>') if brand_item else ""
    return html(
        f"{name} - Pel",
        f'{md_trail(*items)}<main itemscope itemtype="http://schema.org/Product">'
        f'<h1 itemprop="name">{name}</h1><p>Leírás.</p>{brand_html}'
        f'<div itemprop="offers" itemscope itemtype="http://schema.org/Offer">'
        f'<meta itemprop="price" content="199000"><meta itemprop="priceCurrency" content="HUF">'
        f'</div><div itemprop="aggregateRating" itemscope '
        f'itemtype="http://schema.org/AggregateRating"><meta itemprop="ratingValue" content="5">'
        f'</div></main>')


def md_listing(h1, *items):
    return html(f"{h1} - Pelda Webshop",
                f"{md_trail(*items)}<main><h1>{h1}</h1><p>Lista.</p></main>")


def md_shop_site():
    brand = ("ACME", "/spl/3/ACME")
    pages = {
        "/": html("Pelda Webshop", "<main><h1>Pelda Webshop</h1></main>",
                  head=ld({"@type": "Organization", "name": "Pelda Webshop",
                           "url": f"{SHOP}/"})),
        "/sct/1/Termekek": md_listing("Termékek", ("Főoldal", "/"),
                                      ("Termékek", "/sct/1/Termekek")),
        "/sct/2/Klima": md_listing("Klíma", ("Főoldal", "/"), ("Termékek", "/sct/1/Termekek"),
                                   ("Klíma", "/sct/2/Klima")),
        "/spl/3/ACME": md_listing("ACME", ("Főoldal", "/"), ("Termékek", "/sct/1/Termekek"),
                                  ("Klíma", "/sct/2/Klima"), brand),
    }
    for path, name in [("/spd/a", "Acme Nordic 2,6 kW oldalfali klíma"),
                       ("/spd/b", "Acme Nordic 3,5 kW oldalfali klíma"),
                       ("/spd/c", "Acme Solo 5,1 kW A+++")]:
        pages[path] = md_product_page(name, brand[1], brand[0])
    pages["/spd/d"] = md_product_page("Acme Nordic 5,1 kW oldalfali klíma")
    pages["/spd/e"] = md_product_page("Egyedi Termék 1 Fázis")
    # márkaoldal nélkül: a márka a beágyazott `Brand` elemből
    pages["/spd/f"] = md_product_page("Borealis X 2,6 kW", brand_item="Borealis")
    return site(pages)


def test_microdata_gives_page_types_and_breadcrumbs_like_json_ld(shop_config):
    con = md_shop_site()
    assert con.execute("SELECT count(*) FROM schema_blocks").fetchone() == (1,)   # csak a kezdőlap
    urls = dict(con.execute("SELECT page_id, url FROM pages").fetchall())
    kinds = {urls[p]: k for p, k in page_types(con, load_site_config("pelda.hu").page_types)
             .items()}
    assert kinds[f"{SHOP}/sct/2/Klima"] == "category"
    assert kinds[f"{SHOP}/spl/3/ACME"] == "brand_category"
    assert kinds[f"{SHOP}/"] == "other"
    for path in "abcdef":
        assert kinds[f"{SHOP}/spd/{path}"] == "product"       # a microdata `Product` alapján
    # site-fájl nélkül is: a microdata `Product` önmagában termékoldalt ad
    assert {urls[p] for p, k in page_types(con).items() if k == "product"} == {
        f"{SHOP}/spd/{path}" for path in "abcdef"}
    trails = {urls[p]: t for p, t in breadcrumbs(con).items()}
    assert trails[f"{SHOP}/spd/a"] == [
        ("Főoldal", f"{SHOP}/"), ("Termékek", f"{SHOP}/sct/1/Termekek"),
        ("Klíma", f"{SHOP}/sct/2/Klima"), ("ACME", f"{SHOP}/spl/3/ACME")]   # a relatív link feloldva


def test_microdata_shop_gives_the_same_levels_as_the_json_ld_shop(shop_config):
    con = md_shop_site()
    run_rules(con)
    run = run_site(con)
    assert run.shop["products"] == 6 and run.shop["unbranded"] == ["Egyedi Termék 1 Fázis"]
    rows = {(name, kind, subtype) for name, kind, subtype in con.execute(
        "SELECT name, type, subtype FROM entities").fetchall()}
    assert ("Acme", "brand", None) in rows
    assert ("Borealis", "brand", None) in rows              # a beágyazott `Brand` elemből
    assert ("Acme Nordic", "product", "line") in rows
    assert ("Klíma", "concept", "category") in rows
    ids = dict(con.execute("SELECT name, entity_id FROM entities").fetchall())
    relations = set(con.execute("SELECT from_id, to_id, type FROM entity_relations").fetchall())
    family, brand, category = ids["Acme Nordic"], ids["Acme"], ids["Klíma"]
    for name in ("Acme Nordic 2,6 kW oldalfali klíma", "Acme Nordic 3,5 kW oldalfali klíma",
                 "Acme Nordic 5,1 kW oldalfali klíma"):
        assert (ids[name], family, "part_of") in relations
        assert (ids[name], category, "in_category") in relations
    assert (brand, family, "brand_of") in relations
    assert (ids["Borealis"], ids["Borealis X 2,6 kW"], "brand_of") in relations
    # a termék schema-említése a microdatából jön
    assert con.execute(
        "SELECT count(*) FROM page_entities pe JOIN mention_sources s USING (mention_id) "
        "WHERE pe.entity_id = ? AND s.source = 'schema'", [ids["Borealis X 2,6 kW"]]
    ).fetchone() == (1,)


REVIEWED = {
    "@type": "Product", "name": "Levendula szappan", "url": f"{SHOP}/spd/l",
    "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4,8", "reviewCount": "12"},
    "review": [{"@type": "Review", "name": "Nagyon jó",
                "author": {"@type": "Person", "name": "Kovács Béla"},
                "reviewRating": {"@type": "Rating", "ratingValue": "5"},
                "publisher": {"@type": "Organization", "name": "Véleménygyűjtő Kft."}}],
    "offers": {"@type": "Offer", "price": "1990"}}
REVIEWED_MICRODATA = (
    '<main itemscope itemtype="http://schema.org/Product"><h1 itemprop="name">Rózsa szappan</h1>'
    '<div itemprop="aggregateRating" itemscope itemtype="http://schema.org/AggregateRating">'
    '<meta itemprop="ratingValue" content="4.5"><meta itemprop="ratingCount" content="7"></div>'
    '<div itemprop="review" itemscope itemtype="http://schema.org/Review">'
    '<span itemprop="author" itemscope itemtype="http://schema.org/Person">'
    '<span itemprop="name">Szabó Anna</span></span>'
    '<span itemprop="reviewRating" itemscope itemtype="http://schema.org/Rating">'
    '<meta itemprop="ratingValue" content="4"></span></div></main>')


def test_review_nodes_are_not_entities_and_the_aggregate_rating_is_a_product_attribute():
    con = site({
        "/": html("Pelda", "<main><h1>Pelda</h1></main>",
                  head=ld({"@type": "Organization", "name": "Pelda", "url": f"{SHOP}/"})),
        "/spd/l": html("Levendula szappan", "<main><h1>Levendula szappan</h1></main>",
                       head=ld(REVIEWED)),
        "/spd/r": html("Rózsa szappan", REVIEWED_MICRODATA),
        # önálló (nem termék alatti) vélemény is kimarad
        "/velemenyek": html("Vélemények", "<main><h1>Vélemények</h1></main>", head=ld(
            {"@type": "Review", "author": {"@type": "Person", "name": "Tóth Géza"},
             "itemReviewed": {"@type": "Organization", "name": "Pelda"}})),
    })
    run_rules(con)
    run_site(con)
    entities = set(con.execute("SELECT type, name FROM entities").fetchall())
    assert ("product", "Levendula szappan") in entities
    assert ("product", "Rózsa szappan") in entities
    names = {name for _, name in entities}
    assert not names & {"Kovács Béla", "Szabó Anna", "Tóth Géza", "Véleménygyűjtő Kft.",
                        "Nagyon jó"}
    assert not {kind for kind, _ in entities} & {"person"}
    skipped = json.loads(con.execute(
        "SELECT skipped FROM entity_runs WHERE method = 'rules' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()[0])
    assert "Review" not in skipped.get("unmapped_schema_types", {})     # be sem járja
    assert "Rating" not in skipped.get("unmapped_schema_types", {})
    attributes = {name: json.loads(value) for name, value in con.execute(
        "SELECT name, attributes FROM entities WHERE type = 'product'").fetchall()}
    assert attributes["Levendula szappan"] == {"rating_average": 4.8, "rating_count": 12}
    assert attributes["Rózsa szappan"] == {"rating_average": 4.5, "rating_count": 7}


def test_schema_items_resolve_microdata_urls_and_leave_listing_products_alone():
    from aaa2.engine import queries

    card = ('<div itemscope itemtype="http://schema.org/Product"><a itemprop="url" href="/spd/{0}">'
            '<span itemprop="name">Termék {0}</span></a></div>')
    con = site({
        "/lista": html("Lista", "<main><h1>Lista</h1>" + card.format("a") + card.format("b")
                       + "</main>"),
        "/spd/a": html("Termék a", '<main itemscope itemtype="http://schema.org/Product">'
                                   '<h1 itemprop="name">Termék a</h1></main>'),
        "/spd/b": html("Termék b", "<main><h1>Termék b</h1></main>"),
    })
    urls = dict(con.execute("SELECT page_id, url FROM pages").fetchall())
    found = {(urls[item.page_id], item.data.get("url")) for item in queries.schema_items(con)
             if item.syntax == "microdata"}
    assert found == {(f"{SHOP}/lista", f"{SHOP}/spd/a"), (f"{SHOP}/lista", f"{SHOP}/spd/b"),
                     (f"{SHOP}/spd/a", f"{SHOP}/spd/a")}
    kinds = {urls[p]: k for p, k in page_types(con).items()}
    # a listaoldal több terméket jelöl: nem termékoldal; a /spd/b-re a lista terméke mutat
    assert kinds == {f"{SHOP}/lista": "other", f"{SHOP}/spd/a": "product",
                     f"{SHOP}/spd/b": "other"}
    # microdata nélkül a forrás a JSON-LD maga
    plain = site({"/": html("Kezdő", "<main><h1>Kezdő</h1></main>",
                            head=ld({"@type": "Organization", "name": "Pelda"}))})
    assert [(i.syntax, i.data) for i in queries.schema_items(plain)] == [
        (i.syntax, i.data) for i in queries.json_ld(plain)]


def test_a_rerun_keeps_the_relations_consistent(shop_config):
    """Újrafuttatáskor (szabálykör + site-kör) a kapcsolatok száma ugyanaz, és egyik sem mutat
    törölt entitásra."""
    con = shop_site()
    counts = []
    for _ in range(2):
        run_rules(con)
        run_site(con)
        counts.append(con.execute("SELECT type, count(*) FROM entity_relations GROUP BY type "
                                  "ORDER BY type").fetchall())
    assert counts[0] == counts[1]
    assert con.execute("SELECT count(*) FROM entity_relations WHERE from_id NOT IN (SELECT "
                       "entity_id FROM entities) OR to_id NOT IN (SELECT entity_id FROM "
                       "entities)").fetchone() == (0,)


def test_orphan_brand_targets_by_normalized_name_prefix_or_suffix():
    assert orphan_target("Waterstage", [(1, "Fujitsu Waterstage"),
                                        (2, "Fujitsu Waterstage Comfort")]) == ([1], "affix")
    assert orphan_target("LG THERMA", [(1, "LG ThermaV R32"), (2, "LG ThermaV R290 HYDRO"),
                                       (3, "LG")]) == ([1, 2], "affix")
    assert orphan_target("acme", [(1, "Acme"), (2, "Acme Nordic")]) == ([1], "equal")
    assert orphan_target("LG", [(1, "LG ThermaV R32")]) == ([], "none")     # túl rövid


def test_brands_without_products_become_aliases_or_orphans(shop_config):
    con = shop_site()
    run_rules(con)
    llm_entity(con, f"{SHOP}/spd/a", "Nordic", "Nordic", "brand")          # a család utótagja
    llm_entity(con, f"{SHOP}/", "Pelda Webshop", "Pelda Webshop", "brand")  # a site neve
    llm_entity(con, f"{SHOP}/spd/e", "Leírás", "Leírás", "brand")           # nincs cél
    run = run_site(con, clock=lambda: NOON)
    assert run.shop["orphan_aliases"] == {"Nordic": "Acme Nordic",
                                          "Pelda Webshop": "Pelda Webshop"}
    assert run.shop["orphans"] == {"Leírás": []}
    rows = dict(con.execute("SELECT name, type FROM entities").fetchall())
    assert "Nordic" not in rows and rows["Leírás"] == "brand"
    assert "orphan" in con.execute("SELECT flags FROM entities WHERE name = 'Leírás'"
                                   ).fetchone()[0]
    assert "Nordic" in con.execute("SELECT aliases FROM entities WHERE name = 'Acme Nordic'"
                                   ).fetchone()[0]
    assert con.execute("SELECT type, role FROM entities WHERE name = 'Pelda Webshop'"
                       ).fetchall() == [("org", "brand")]
    assert sorted(con.execute("SELECT removed_name, rule FROM merge_log WHERE rule LIKE "
                              "'orphan%'").fetchall()) == [("Nordic", "orphan_brand"),
                                                           ("Pelda Webshop", "orphan_brand_site")]


def test_category_named_products_and_truncated_site_names_merge(shop_config):
    con = shop_site()
    run_rules(con)
    product = f"{SHOP}/spd/a"
    llm_entity(con, product, "klíma", "Klima", "product", "physical_good")   # a kategória neve
    con.execute("UPDATE blocks SET text = 'Acme Nordic 2,6 kW oldalfali klíma - Pelda Web' "
                "WHERE kind = 'title' AND page_id = (SELECT page_id FROM pages WHERE url = ?)",
                [product])
    cut = llm_entity(con, product, "Pelda Web", "Pelda Web", "org")          # csonkolt site-név
    whole = llm_entity(con, product, "Leírás", "Pelda We", "org")            # nem title-ben
    con.execute("UPDATE page_entities SET position = 'title' WHERE entity_id = ?", [cut])
    run = run_site(con, clock=lambda: NOON)
    assert run.shop["category_products"] == ["Klima"]
    assert run.shop["site_name_cuts"] == ["Pelda Web"]
    rows = {name: kind for name, kind in con.execute("SELECT name, type FROM entities")
            .fetchall()}
    assert "Klima" not in rows and rows["Klíma"] == "concept" and "Pelda Web" not in rows
    assert con.execute("SELECT type FROM entities WHERE entity_id = ?", [whole]).fetchone() \
        == ("org",)
    site_org, aliases = con.execute("SELECT entity_id, aliases FROM entities WHERE name = "
                                    "'Pelda Webshop'").fetchone()
    assert "Pelda Web" not in (aliases or []) and con.execute(
        "SELECT count(*) FROM entity_aliases WHERE entity_id = ? AND alias = 'Pelda Web'",
        [site_org]).fetchone() == (0,)                              # a csonk nem alias
    assert con.execute("SELECT count(*) FROM page_entities WHERE entity_id = ? AND position = "
                       "'title'", [site_org]).fetchone()[0] >= 1
    assert sorted(con.execute("SELECT removed_name, rule FROM merge_log WHERE rule IN "
                              "('shop_category', 'site_name_cut')").fetchall()) == [
        ("Klima", "shop_category"), ("Pelda Web", "site_name_cut")]


def test_family_trimming_nesting_and_prefix_owner():
    assert trim_family(["LG", "ThermaV", "R290", "HYDRO", "EGYSÉGGEL"]) == ["LG", "ThermaV"]
    assert trim_family(["Cascade", "Nordic", "ECO"]) == ["Cascade", "Nordic", "ECO"]
    assert trim_family(["Acme", "X", "kültéri", "egység"]) == ["Acme", "X"]
    lines = [(1, "Cascade FREE MATCH"), (2, "Cascade FREE MATCH BORA"),
             (3, "Cascade FREE MATCH VISION NORDIC"), (4, "Cascade VISION NORDIC"),
             (5, "Cascade BORA")]
    assert nest_families(lines) == {2: 1, 3: 1}
    assert longest_prefix("Cascade FREE MATCH LEGEND CWH09YC klíma", lines) == 1
    assert longest_prefix("Cascade LEGEND 2,5 kW", lines) is None


def nested_shop():
    brand = ("ACME", "/spl/3/ACME")
    pages = {"/": html("Pelda Webshop", "<main><h1>Pelda Webshop</h1></main>",
                       head=ld({"@type": "Organization", "name": "Pelda Webshop",
                                "url": f"{SHOP}/"})),
             "/sct/1/Termekek": listing("Termékek", ("Főoldal", "/"),
                                        ("Termékek", "/sct/1/Termekek")),
             "/sct/2/Klima": listing("Klíma", ("Főoldal", "/"), ("Termékek", "/sct/1/Termekek"),
                                     ("Klíma", "/sct/2/Klima")),
             "/spl/3/ACME": listing("ACME", ("Főoldal", "/"), ("Termékek", "/sct/1/Termekek"),
                                    ("Klíma", "/sct/2/Klima"), brand)}
    names = ["Acme FREE MATCH 4,1KW CWHD14NK6OO Multi kültéri egység",
             "Acme FREE MATCH 5,3KW CWHD18NK600 Multi kültéri egység",
             "FREE MATCH BORA CWH09AAA-K6DNA5A klíma Multi beltéri egység",
             "FREE MATCH BORA CWH12AABXB-K6DNA5A klíma Multi beltéri egység",
             "Acme FREE MATCH LEGEND CWH09YC-K6DNA2A klíma multi beltéri egység",
             "Acme Therma R32 12KW 3Fázis", "Acme Therma R32 14KW 3Fázis",
             "Acme Therma R290 HYDRO EGYSÉGGEL 9KW 3Fázis",
             "Acme Therma R290 HYDRO EGYSÉGGEL 12KW 3Fázis"]
    for i, name in enumerate(names):
        pages[f"/spd/{i}"] = product_page(name, f"/spd/{i}", brand[1], brand[0])
    return site(pages)


def test_nested_families_with_brand_prefix_parent_and_orphan(shop_config):
    con = nested_shop()
    run_rules(con)
    llm_entity(con, f"{SHOP}/spd/5", "Acme Therm", "Acme Therm", "brand")    # a szülő előtagja
    run = run_site(con, clock=lambda: NOON)
    ids = dict(con.execute("SELECT name, entity_id FROM entities").fetchall())
    part_of = {(a, b) for a, b in con.execute(
        "SELECT f.name, t.name FROM entity_relations r JOIN entities f ON f.entity_id = r.from_id "
        "JOIN entities t ON t.entity_id = r.to_id WHERE r.type = 'part_of'").fetchall()}
    assert {("Acme FREE MATCH BORA", "Acme FREE MATCH"), ("Acme Therma R32", "Acme Therma"),
            ("Acme Therma R290 HYDRO EGYSÉGGEL", "Acme Therma"),
            ("Acme FREE MATCH LEGEND CWH09YC-K6DNA2A klíma multi beltéri egység",
             "Acme FREE MATCH")} <= part_of
    assert "FREE MATCH BORA" in con.execute("SELECT aliases FROM entities WHERE name = "
                                            "'Acme FREE MATCH BORA'").fetchone()[0]
    brand_of = {name for (name,) in con.execute(
        "SELECT t.name FROM entity_relations r JOIN entities t ON t.entity_id = r.to_id "
        "WHERE r.type = 'brand_of'").fetchall()}
    assert brand_of == {"Acme FREE MATCH", "Acme Therma"}                  # csak a legfelső
    assert (run.shop["parents"], run.shop["nested"]) == (1, 3)
    assert run.shop["orphan_aliases"] == {"Acme Therm": "Acme Therma"}
    assert "Acme Therm" not in ids and con.execute(
        "SELECT count(*) FROM page_entities WHERE entity_id = ?",
        [ids["Acme Therma"]]).fetchone()[0] >= 4                            # H1-említések


PRODUCTS = {"/spd/a": "Levendula szappan 100g", "/spd/b": "Narancs szappan 120g",
            "/spd/c": "Olíva szappan 100g", "/spd/d": "Kamilla szappan 90g"}


def related_shop(recommend):
    """Négy termékoldal; mindegyik alján a `recommend(path)` ajánló-blokk."""
    pages = {"/": html("Pelda Webshop", "<main><h1>Pelda Webshop</h1></main>")}
    for path, name in PRODUCTS.items():
        node = {"@type": "Product", "name": name, "url": f"{SHOP}{path}"}
        pages[path] = html(f"{name} - Pel", f"<main><h1>{name}</h1><p>Leírás.</p>"
                                            f"{recommend(path)}</main>", head=ld(node))
    return site(pages)


def links(paths):
    return "".join(f"<p><a href='{p}'>{PRODUCTS[p]}</a></p>" for p in paths)


def test_a_section_title_above_links_to_several_products_is_not_a_card_title(shop_config):
    # a „Hasonló termékek” szakaszcím alatt több termékre mutató link áll: nem kártyacím
    con = related_shop(lambda path: "<h3>Hasonló termékek</h3>"
                       + links([p for p in PRODUCTS if p != path][:2]))
    run_rules(con)
    run = run_site(con)
    assert run.shop["products"] == 4
    assert con.execute("SELECT count(*) FROM merge_log WHERE rule = 'page_identity'"
                       ).fetchone() == (0,)
    assert sorted(name for (name,) in con.execute(
        "SELECT name FROM entities WHERE type = 'product' AND subtype IS DISTINCT FROM 'line'"
    ).fetchall()) == sorted(PRODUCTS.values())
    assert con.execute("SELECT count(*) FROM entity_aliases WHERE alias = 'Hasonló termékek'"
                       ).fetchone() == (0,)


def test_linked_item_headings_do_not_close_the_section(shop_config):
    # az ajánlott termékek neve maga is (magasabb szintű) linkelt heading: a szakasz ezeken át
    # tart, így a „Hasonló termékek” itt sem kártyacím
    def items(paths):
        return "".join(f"<p><a href='{p}'>Villámnézet</a></p><h2><a href='{p}'>{PRODUCTS[p]}"
                       f"</a></h2><p>Raktáron</p>" for p in paths)
    con = related_shop(lambda path: "<h3>Hasonló termékek</h3>"
                       + items([p for p in PRODUCTS if p != path][:2]))
    run_rules(con)
    run_site(con)
    assert con.execute("SELECT count(*) FROM entity_aliases WHERE alias = 'Hasonló termékek'"
                       ).fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM merge_log WHERE rule = 'page_identity'"
                       ).fetchone() == (0,)


def test_a_heading_above_a_single_link_stays_a_card_title(shop_config):
    # egy heading, alatta egyetlen céloldalra mutató link: kártyacím, a céloldal aliasa
    con = related_shop(lambda path: "<h3>Kedvencünk</h3><p>Rövid ajánló.</p>"
                       + links(["/spd/b"]) if path == "/spd/a" else "")
    run_rules(con)
    run_site(con)
    assert con.execute(
        "SELECT e.name FROM entity_aliases a JOIN entities e USING (entity_id) WHERE "
        "a.alias = 'Kedvencünk'").fetchall() == [("Narancs szappan 120g",)]


def test_entities_bound_to_different_pages_do_not_merge_on_a_shared_name(shop_config):
    # minden oldal alján ugyanaz a cím, alatta egy-egy másik termék linkje: a cím kártyacím,
    # így több termék aliasa, de két, más-más oldalhoz kötött termék ettől nem olvad össze
    order = list(PRODUCTS)
    con = related_shop(lambda path: "<h3>Ajánlott</h3>"
                       + links([order[(order.index(path) + 1) % len(order)]]))
    run_rules(con)
    run_site(con)
    assert con.execute("SELECT count(*) FROM merge_log WHERE rule = 'page_identity'"
                       ).fetchone() == (0,)
    assert sorted(name for (name,) in con.execute(
        "SELECT name FROM entities WHERE type = 'product' AND anchor_page_id IS NOT NULL"
    ).fetchall()) == sorted(PRODUCTS.values())
    again = run_site(con)                                      # újrafuttatva is külön maradnak
    assert again.merges.get("page_identity", 0) == 0
