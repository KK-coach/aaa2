"""A feloldás (aaa2/resolver) azon szabályai, amelyeknek a szabályjegyzék szerint nem volt
tesztje: oldalszerep-feltételek, a site-kör lépéssorrendje, a site neve, az összevonás tiltásai,
a webshop-összevonások, a site-fájl ellenőrzése és valódi értékei, a tudásbázis-keresés
nyelvsorrendje. Hálózat és LLM nélkül."""
import json
from types import SimpleNamespace

import pytest

from aaa2.resolver import knowledge, overrides, shop
from aaa2.resolver import site as site_module
from aaa2.resolver.merge import Merger, _mergeable
from aaa2.resolver.names import Name, _site_name_keys
from aaa2.resolver.overrides import SiteConfig, load_site_config
from aaa2.resolver.pages import PageInfo, _own_role, page_roles, schema_nodes
from tests.test_entities_rules import html, ld, site
from tests.test_entities_site import NOON

BASE = "https://pelda.hu"


def entity(con, name, kind, source="llm", role=None, anchor=None, aliases=()):
    (entity_id,) = con.execute(
        "INSERT INTO entities (name, type, aliases, source, role, anchor_page_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING entity_id",
        [name, kind, list(aliases), source, role, anchor, NOON]).fetchone()
    return entity_id


def merger(con):
    (run_id,) = con.execute("INSERT INTO entity_runs (started_at, method, llm_calls) "
                            "VALUES (?, 'site', 0) RETURNING run_id", [NOON]).fetchone()
    return Merger(con, run_id, lambda: NOON)


def test_only_successfully_rendered_pages_get_a_role():
    con = site({"/": html("Kezdőlap", ""), "/a/": html("A", "<p>x</p>"),
                "/b/": html("B", "<p>x</p>"), "/c/": html("C", "<p>x</p>")})
    ids = dict(con.execute("SELECT url, page_id FROM pages").fetchall())
    con.execute("UPDATE pages SET status = 404 WHERE url = ?", [f"{BASE}/a/"])
    con.execute("UPDATE pages SET error = 'timeout' WHERE url = ?", [f"{BASE}/b/"])
    con.execute("UPDATE pages SET rendered_html = NULL WHERE url = ?", [f"{BASE}/c/"])
    assert set(page_roles(con)) == {ids[f"{BASE}/"]}


def test_a_page_without_any_evidence_is_a_support_page():
    assert _own_role(1, f"{BASE}/valami/", "Valami", {f"{BASE}/"}, [], False, 0) \
        == ("support", "no_entity_evidence")
    # kódblokk és rövid H1 kevés hivatkozó csoporttal: még nem komponensoldal
    assert _own_role(1, f"{BASE}/valami/", "Valami", {f"{BASE}/"}, [], True, 0) \
        == ("support", "no_entity_evidence")


def test_schema_nodes_skip_invalid_blocks_and_flatten_the_graph():
    con = site({"/": html("Kezdőlap", "")})
    (page_id,) = con.execute("SELECT page_id FROM pages").fetchone()
    con.execute("DELETE FROM schema_blocks")
    blocks = [("invalid", '{"@type": "Service", "name": "Hibás"}'),
              ("Service", '{"@type": "Service", "name": "Egy"}'),
              (None, json.dumps({"@graph": [{"@type": "Person", "name": "Kettő"}, "szöveg",
                                            {"@type": "Product", "name": "Három"}]})),
              ("Thing", "nem json")]
    for ordinal, (kind, raw) in enumerate(blocks):
        con.execute("INSERT INTO schema_blocks (page_id, type, json, ordinal) VALUES (?, ?, ?, ?)",
                    [page_id, kind, raw, ordinal])
    assert [node["name"] for node in schema_nodes(con)[page_id]] == ["Egy", "Kettő", "Három"]


def test_the_site_round_runs_its_steps_in_a_fixed_order(monkeypatch):
    con = site({"/": html("Kezdőlap", "")})
    calls = []

    def step(name, result=None):
        def recorded(*args, **kwargs):
            calls.append(name)
            return result
        monkeypatch.setattr(site_module, name, recorded)

    step("_page_entities", {})
    step("_packages")
    step("run_shop", SimpleNamespace(as_dict=dict))
    step("_hreflang_place")
    step("_normalized_merges")
    step("_abbreviation_merges")
    step("_type_split", {})
    step("_steps")
    step("apply_overrides", 0)
    step("_offers", 0)
    step("_demo")
    step("_template")
    site_module.run_site(con, clock=lambda: NOON)
    assert calls == ["_page_entities", "_packages", "run_shop", "_hreflang_place",
                     "_normalized_merges", "_abbreviation_merges", "_type_split", "_steps",
                     "_normalized_merges", "_abbreviation_merges", "apply_overrides", "_offers",
                     "_demo", "_template"]


def test_site_name_keys_are_the_site_brands_not_the_product_brands():
    con = site({"/": html("Kezdőlap", "")})
    con.execute("DELETE FROM entities")
    entity(con, "Pelda", "org", source="schema", role="brand", aliases=["Pelda Kft."])
    entity(con, "Pelda Webshop", "brand", source="rule")
    entity(con, "LLM-márka", "brand", source="llm")
    acme = entity(con, "ACME", "brand", source="rule")
    product = entity(con, "ACME 100", "product")
    con.execute("INSERT INTO entity_relations (from_id, to_id, type, source) "
                "VALUES (?, ?, 'brand_of', 'shop')", [acme, product])
    assert _site_name_keys(con) == {"pelda", "pelda kft.", "pelda webshop"}


def test_mergeable_respects_anchors_tiers_and_subtype_classes():
    def row(anchor=None, tier=None, subtype=None):
        return (1, anchor, tier, "llm", 0, "tech", subtype)

    assert _mergeable(row(), row())
    assert _mergeable(row(anchor=1), row(anchor=1)) and _mergeable(row(anchor=1), row())
    assert not _mergeable(row(anchor=1), row(anchor=2))                 # két oldal entitása
    assert not _mergeable(row(tier="core"), row(tier="package"))        # két eltérő szint
    assert _mergeable(row(tier="work_mode"), row(tier="package"))       # egy csoport
    assert _mergeable(row(tier="core"), row(tier="step")) and _mergeable(row(tier="core"), row())
    assert not _mergeable(row(subtype="package"), row(subtype="component"))
    assert _mergeable(row(subtype="library"), row(subtype="framework"))     # egy osztály
    assert not _mergeable(row(subtype="line"), row(subtype="variant"))
    assert _mergeable(row(subtype="other"), row(subtype="component"))       # osztály nélküli


def test_shop_brand_merges_unanchored_brand_and_org_into_one_brand():
    con = site({"/": html("Kezdőlap", "")})
    con.execute("DELETE FROM entities")
    org = entity(con, "ACME", "org")
    brand = entity(con, "Acme", "brand")
    (page_id,) = con.execute("SELECT page_id FROM pages").fetchone()
    anchored = entity(con, "ACME Kft.", "org", anchor=page_id, aliases=["ACME"])
    ctx = SimpleNamespace(con=con, site_lang="hu")
    kept = shop._brand_entity(ctx, merger(con), "acme", "ACME", ["ACME", "Acme"])
    assert kept == brand
    assert con.execute("SELECT name, type FROM entities WHERE entity_id = ?", [kept]
                       ).fetchone() == ("ACME", "brand")
    assert con.execute("SELECT kept_id, removed_id, rule FROM merge_log").fetchall() \
        == [(brand, org, "shop_brand")]
    # az oldalhoz kötött entitás nem olvad be
    assert con.execute("SELECT count(*) FROM entities WHERE entity_id = ?", [anchored]
                       ).fetchone() == (1,)


def test_a_category_entity_absorbs_the_same_named_unanchored_concepts():
    con = site({"/": html("Kezdőlap", ""), "/sct/1/klimak/": html("Klímák", "<p>x</p>")})
    con.execute("DELETE FROM entities")
    ids = dict(con.execute("SELECT url, page_id FROM pages").fetchall())
    page_id = ids[f"{BASE}/sct/1/klimak/"]
    own = entity(con, "Klíma kategória", "concept", anchor=page_id)
    loose = entity(con, "Klímák", "concept")
    other_type = entity(con, "Klímák", "product")
    member = PageInfo(page_id, f"{BASE}/sct/1/klimak/", "hu", "Klímák", "Klímák", "g", "category",
                      "page_type")
    ctx = SimpleNamespace(con=con, site_lang="hu")
    kept = shop._anchored_entity(ctx, merger(con), [member], "concept", "category", "Klímák",
                                 [Name("Klímák", "h1", "hu")])
    assert kept == own
    assert con.execute("SELECT kept_id, removed_id, rule FROM merge_log").fetchall() \
        == [(own, loose, "page_identity")]
    assert con.execute("SELECT name, subtype, anchor_page_id, aliases FROM entities "
                       "WHERE entity_id = ?", [kept]).fetchone() \
        == ("Klímák", "category", page_id, [])
    assert con.execute("SELECT count(*) FROM entities WHERE entity_id = ?", [other_type]
                       ).fetchone() == (1,)                             # más típus marad


def test_the_site_file_rejects_unknown_tiers_and_nameless_offers(tmp_path):
    assert load_site_config("nincs.hu", tmp_path) == SiteConfig()
    assert load_site_config(None, tmp_path) == SiteConfig()
    (tmp_path / "a.hu.toml").write_text('[[offers]]\nnames = ["X"]\n', encoding="utf-8")
    assert load_site_config("a.hu", tmp_path).offers[0].tier == "package"       # az alapérték
    (tmp_path / "b.hu.toml").write_text('[[offers]]\nnames = ["X"]\ntier = "step"\n',
                                        encoding="utf-8")
    with pytest.raises(ValueError, match="ismeretlen szint"):
        load_site_config("b.hu", tmp_path)
    (tmp_path / "c.hu.toml").write_text('[[offers]]\ntier = "core"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="names vagy url"):
        load_site_config("c.hu", tmp_path)


def test_the_real_site_files_hold_the_agreed_settings():
    kk = load_site_config("kk.coach")
    assert [(o.names, o.tier, o.part_of) for o in kk.offers] == [
        (("Közvetlen implementálás", "Direct Implementation"), "work_mode", "Execution"),
        (("Strukturált felügyelet", "Structured Oversight"), "work_mode", "Execution")]
    duex = load_site_config("duexhungary.hu")
    assert duex.crawl.seed == "https://shop.duexhungary.hu/"
    assert duex.crawl.include == r"^https?://shop\.duexhungary\.hu/"
    assert (duex.crawl.concurrency, duex.crawl.render_timeout) == (2, 30)
    assert len(duex.crawl.exclude) == 10
    for excluded in ("shop_cart", "shop_reg", "shop_order_track", "overlay=login",
                     "infinite_scroll", "ajax=1", r"shop_pic\.php"):
        assert excluded in duex.crawl.exclude
    assert duex.page_types == {"category": ("/sct/",), "brand_category": ("/spl/",)}
    lens = load_site_config("marketinglens.com")
    assert (lens.crawl.seed, lens.crawl.concurrency) == ("https://marketinglens.com/", 4)
    assert overrides.SITES_DIR.name == "sites" and overrides.SITES_DIR.is_dir()


def test_knowledge_search_tries_the_entity_language_then_the_site_languages_then_english():
    con = site({"/": html("Kezdőlap", "<p>Webanalitika és mérés.</p>")}, languages=("de", "hu"))
    con.execute("DELETE FROM entities")
    (page_id,) = con.execute("SELECT page_id FROM pages").fetchone()
    hungarian = entity(con, "Webanalitika", "concept")
    con.execute("UPDATE entities SET lang = 'hu' WHERE entity_id = ?", [hungarian])
    no_lang = entity(con, "Mérés", "concept")
    for entity_id, form in ((hungarian, "Webanalitika"), (no_lang, "mérés")):
        con.execute("INSERT INTO page_entities (page_id, entity_id, surface_form, position) "
                    "VALUES (?, ?, ?, 'schema')", [page_id, entity_id, form])
    calls = []

    class Recorder:
        failures = 0

        def wikidata(self, name, code):
            calls.append((name, code))

    run = knowledge.link_entities(con, Recorder(), lambda: NOON, site_lang="fr")
    assert calls == [("Webanalitika", "hu"), ("Webanalitika", "fr"), ("Webanalitika", "de"),
                     ("Webanalitika", "en"),
                     ("Mérés", "fr"), ("Mérés", "de"), ("Mérés", "hu"), ("Mérés", "en")]
    assert (run.entities, run.none) == (2, 2)


def test_page_roles_of_a_service_page():
    """A szerep bizonyítéka nélküli és a bizonyítékkal bíró oldal egy site-on."""
    service = ld({"@type": "Service", "name": "Mérés", "url": f"{BASE}/meres/"})
    con = site({"/": html("Kezdőlap", ""), "/meres/": html("Mérés", "<p>x</p>", head=service),
                "/egyeb/": html("Egyéb", "<p>x</p>")})
    roles = {info.url: (info.role, info.reason) for info in page_roles(con).values()}
    assert roles[f"{BASE}/meres/"] == ("offer", "schema_service")
    assert roles[f"{BASE}/egyeb/"] == ("support", "no_entity_evidence")
