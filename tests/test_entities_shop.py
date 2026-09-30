"""A webshop-szintek (aaa2/entities/shop.py; M2/6 spec, 9a pont), a site-fájl crawl- és
oldaltípus-része (overrides.py) és az oldaltípus (pages.page_types), hálózat nélkül."""
import json

import pytest

from aaa2.entities import overrides
from aaa2.entities.overrides import load_site_config
from aaa2.entities.pages import breadcrumbs, page_types
from aaa2.entities.rules import run_rules
from aaa2.entities.shop import (
    base_tokens,
    families,
    longest_prefix,
    name_attributes,
    nest_families,
    orphan_target,
    trim_family,
)
from aaa2.entities.site import cut_off, run_site, site_name_form
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
        "render_timeout = 30\n[page_types]\ncategory = '/sct/'\nbrand_category = ['/spl/']\n",
        encoding="utf-8")
    config = load_site_config("x.hu", tmp_path)
    assert config.crawl.seed == "https://shop.x.hu/"
    assert config.crawl.exclude_pattern == "(?:shop_cart)|(?:^https?://[^/]+[^?]*//)"
    assert (config.crawl.concurrency, config.crawl.render_timeout) == (2, 30)
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
